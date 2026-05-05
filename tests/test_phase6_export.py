"""
Тесты Фазы 6: Юридические заявки и экспорт (EvidenceExporter).
Запуск: pytest tests/test_phase6_export.py -v

Тестируем генерацию юрзаявок, экспорт в JSON/CSV, текстовый лог диалога,
таймлайн событий и формирование ZIP-архивов.
"""
import json
import zipfile
import io
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from database.schema import DatabaseSchema
from database.migrations import DatabaseMigrations
from database.sellers import SellersDB
from database.products import ProductsDB
from database.product_sellers import ProductSellersDB
from database.seller_workflow import SellerWorkflowDB
from database.message_log import MessageLogDB
from database.legal_requests import LegalRequestsDB
from whatsapp.classifier import ClassificationResult, ClassificationType
from workflow.engine import WorkflowEngine
from workflow.export import EvidenceExporter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Временная БД для каждого теста."""
    return tmp_path / "test_export.db"


async def _init_db(db_path: Path):
    """Инициализация схемы + миграции."""
    await DatabaseSchema.init_db(db_path)
    migrations = DatabaseMigrations(db_path)
    await migrations.run_migrations()


async def _seed_seller(
    db_path: Path,
    merchant_id: str = "M001",
    name: str = "Test Shop",
    phone: str = "+77011234567",
):
    """Создать продавца-заглушку."""
    sellers = SellersDB(db_path)
    await sellers.add_seller(merchant_id, name, phone)


async def _seed_product(
    db_path: Path,
    sku: str = "SKU001",
    url: str = "https://kaspi.kz/shop/p/sku001",
    title: str = "Тестовый товар",
):
    """Создать товар-заглушку."""
    products = ProductsDB(db_path)
    await products.add_product(sku, url, title)


def _make_engine(db_path, wa_client=None, classifier=None, notifier=None):
    """Создать WorkflowEngine с mock-зависимостями."""
    workflow_db = SellerWorkflowDB(db_path)
    message_log_db = MessageLogDB(db_path)
    legal_db = LegalRequestsDB(db_path)
    sellers_db = SellersDB(db_path)
    products_db = ProductsDB(db_path)
    product_sellers_db = ProductSellersDB(db_path)

    if wa_client is None:
        wa_client = AsyncMock()
        wa_client.send_text = AsyncMock(return_value={"idMessage": "test123"})

    if classifier is None:
        classifier = AsyncMock()
        classifier.classify = AsyncMock(return_value=ClassificationResult(
            classification=ClassificationType.UNKNOWN, confidence=0.5
        ))

    if notifier is None:
        notifier = AsyncMock()
        notifier.send_to_admins = AsyncMock()

    return WorkflowEngine(
        workflow_db=workflow_db,
        message_log_db=message_log_db,
        legal_db=legal_db,
        sellers_db=sellers_db,
        products_db=products_db,
        product_sellers_db=product_sellers_db,
        whatsapp_client=wa_client,
        classifier=classifier,
        notification_service=notifier,
    )


def _make_exporter(db_path):
    """Создать EvidenceExporter."""
    return EvidenceExporter(
        legal_db=LegalRequestsDB(db_path),
        workflow_db=SellerWorkflowDB(db_path),
        message_log_db=MessageLogDB(db_path),
        sellers_db=SellersDB(db_path),
        products_db=ProductsDB(db_path),
    )


async def _create_full_workflow(db_path: Path):
    """
    Создать полный workflow с WARN1, WARN2, диалогом и юрзаявкой.
    Возвращает (workflow_id, request_id).
    """
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path, "SKU001", "https://kaspi.kz/shop/p/sku001", "Товар 1")
    await _seed_product(db_path, "SKU002", "https://kaspi.kz/shop/p/sku002", "Товар 2")

    engine = _make_engine(db_path)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001", "SKU002"])

    # WARN1 (мокаем sleep чтоб не ждать 5-10 сек)
    with patch("workflow.engine.asyncio.sleep", new_callable=AsyncMock):
        await engine.send_warn1(wf_id)

    # Входящее сообщение от продавца
    msg_log = MessageLogDB(db_path)
    await msg_log.log_message(
        workflow_id=wf_id,
        seller_id="M001",
        direction="IN",
        text="Я не знал, что нельзя",
        classification="DIDNT_KNOW",
    )

    # WARN2
    wf_db = SellerWorkflowDB(db_path)
    await wf_db.update_status(wf_id, "WARN1_SENT")  # для send_warn2
    with patch("workflow.engine.asyncio.sleep", new_callable=AsyncMock):
        await engine.send_warn2(wf_id)

    # Ещё одно входящее
    await msg_log.log_message(
        workflow_id=wf_id,
        seller_id="M001",
        direction="IN",
        text="Не буду снимать, докажите",
        classification="PROVE_IT",
    )

    # Эскалация к юрзаявке
    request_id = await engine.escalate_to_legal(wf_id)

    return wf_id, request_id


# ---------------------------------------------------------------------------
# export_legal_request — JSON
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_legal_request_json(db_path):
    """Экспорт юрзаявки в JSON содержит все требуемые поля."""
    wf_id, request_id = await _create_full_workflow(db_path)
    exporter = _make_exporter(db_path)

    result = await exporter.export_legal_request(request_id, fmt="json")

    assert isinstance(result, bytes)
    data = json.loads(result.decode("utf-8"))

    # Проверяем структуру
    assert "seller" in data
    assert data["seller"]["name"] == "Test Shop"
    assert data["seller"]["merchant_id"] == "M001"
    assert data["seller"]["phone"] == "+77011234567"

    assert "products" in data
    assert len(data["products"]) == 2
    product_ids = {p["product_id"] for p in data["products"]}
    assert "SKU001" in product_ids
    assert "SKU002" in product_ids

    assert "timeline" in data
    events = [e["event"] for e in data["timeline"]]
    assert "DETECTED" in events
    assert "WARN1_SENT" in events
    assert "WARN2_SENT" in events
    assert "LEGAL_REQUEST" in events

    assert "dialog" in data
    assert len(data["dialog"]) >= 2  # минимум OUT (WARN1) + IN

    assert "legal_request" in data
    assert data["legal_request"]["id"] == request_id


@pytest.mark.asyncio
async def test_export_legal_request_not_found(db_path):
    """Экспорт несуществующей юрзаявки → ValueError."""
    await _init_db(db_path)
    exporter = _make_exporter(db_path)

    with pytest.raises(ValueError, match="не найдена"):
        await exporter.export_legal_request(999)


# ---------------------------------------------------------------------------
# export_legal_request — CSV
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_legal_request_csv(db_path):
    """Экспорт юрзаявки в CSV содержит ключевые данные."""
    wf_id, request_id = await _create_full_workflow(db_path)
    exporter = _make_exporter(db_path)

    result = await exporter.export_legal_request(request_id, fmt="csv")

    assert isinstance(result, bytes)
    text = result.decode("utf-8-sig")

    assert "Test Shop" in text
    assert "M001" in text
    assert "SKU001" in text
    assert "SKU002" in text
    assert "WARN1_SENT" in text
    assert "DETECTED" in text


# ---------------------------------------------------------------------------
# export_dialog_log
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_dialog_log(db_path):
    """Текстовый лог содержит входящие и исходящие сообщения."""
    wf_id, _ = await _create_full_workflow(db_path)
    exporter = _make_exporter(db_path)

    result = await exporter.export_dialog_log(wf_id)

    assert isinstance(result, str)
    assert "Лог переписки" in result
    assert "Test Shop" in result
    assert f"#{wf_id}" in result

    # Исходящие (WARN) — маркер →
    assert "→" in result

    # Входящие — маркер ← с именем магазина
    assert "←" in result
    assert "← Test Shop:" in result

    # Счётчик
    assert "Всего сообщений:" in result


@pytest.mark.asyncio
async def test_export_dialog_log_not_found(db_path):
    """Лог несуществующего workflow → ValueError."""
    await _init_db(db_path)
    exporter = _make_exporter(db_path)

    with pytest.raises(ValueError, match="не найден"):
        await exporter.export_dialog_log(999)


# ---------------------------------------------------------------------------
# export_timeline
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_timeline(db_path):
    """Таймлайн содержит все ключевые события в хронологическом порядке."""
    wf_id, _ = await _create_full_workflow(db_path)
    exporter = _make_exporter(db_path)

    result = await exporter.export_timeline(wf_id)

    assert isinstance(result, dict)
    assert result["workflow_id"] == wf_id
    assert result["seller"]["name"] == "Test Shop"
    assert result["seller"]["merchant_id"] == "M001"

    event_types = [e["event"] for e in result["events"]]
    assert "DETECTED" in event_types
    assert "PRODUCT_DETECTED" in event_types
    assert "WARN1_SENT" in event_types
    assert "WARN2_SENT" in event_types
    assert "SELLER_RESPONSE" in event_types
    assert "LEGAL_REQUEST" in event_types


@pytest.mark.asyncio
async def test_export_timeline_not_found(db_path):
    """Таймлайн несуществующего workflow → ValueError."""
    await _init_db(db_path)
    exporter = _make_exporter(db_path)

    with pytest.raises(ValueError, match="не найден"):
        await exporter.export_timeline(999)


@pytest.mark.asyncio
async def test_export_timeline_sorted_chronologically(db_path):
    """События в таймлайне отсортированы по времени."""
    wf_id, _ = await _create_full_workflow(db_path)
    exporter = _make_exporter(db_path)

    result = await exporter.export_timeline(wf_id)

    timestamps = [e.get("at", "") for e in result["events"] if e.get("at")]
    assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# generate_legal_package (ZIP)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_legal_package_zip(db_path):
    """ZIP-архив содержит все три файла."""
    wf_id, request_id = await _create_full_workflow(db_path)
    exporter = _make_exporter(db_path)

    result = await exporter.generate_legal_package(request_id)

    assert isinstance(result, bytes)

    # Проверяем что это валидный ZIP
    zip_file = zipfile.ZipFile(io.BytesIO(result))
    names = zip_file.namelist()

    assert f"заявка_{request_id}.txt" in names
    assert f"переписка_{request_id}.txt" in names
    assert f"хронология_{request_id}.txt" in names

    # Проверяем содержимое заявки (текстовый формат)
    legal_text = zip_file.read(f"заявка_{request_id}.txt").decode("utf-8")
    assert "Test Shop" in legal_text

    # Проверяем текстовый лог переписки
    dialog_text = zip_file.read(f"переписка_{request_id}.txt").decode("utf-8")
    assert "Лог переписки" in dialog_text

    # Проверяем хронологию
    timeline_text = zip_file.read(f"хронология_{request_id}.txt").decode("utf-8")
    assert len(timeline_text) > 0

    zip_file.close()


@pytest.mark.asyncio
async def test_generate_legal_package_not_found(db_path):
    """ZIP для несуществующей юрзаявки → ValueError."""
    await _init_db(db_path)
    exporter = _make_exporter(db_path)

    with pytest.raises(ValueError, match="не найдена"):
        await exporter.generate_legal_package(999)


# ---------------------------------------------------------------------------
# Полный цикл через engine.escalate_to_legal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_escalate_to_legal_creates_request(db_path):
    """escalate_to_legal создаёт юрзаявку со всеми данными."""
    wf_id, request_id = await _create_full_workflow(db_path)

    legal_db = LegalRequestsDB(db_path)
    request = await legal_db.get_request(request_id)

    assert request is not None
    assert request["workflow_id"] == wf_id
    assert request["seller_id"] == "M001"
    assert request["shop_name"] == "Test Shop"
    assert request["phone"] == "+77011234567"

    # product_links — JSON с товарами
    product_links = json.loads(request["product_links"])
    assert len(product_links) == 2

    # warn_timeline — JSON с датами
    warn_timeline = json.loads(request["warn_timeline"])
    assert "warn1_sent_at" in warn_timeline
    assert "warn2_sent_at" in warn_timeline

    # dialog_log — JSON с сообщениями
    dialog = json.loads(request["dialog_log"])
    assert len(dialog) >= 2


@pytest.mark.asyncio
async def test_escalate_to_legal_updates_status(db_path):
    """escalate_to_legal переводит workflow в LEGAL_REQUEST_CREATED."""
    wf_id, _ = await _create_full_workflow(db_path)

    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "LEGAL_REQUEST_CREATED"


@pytest.mark.asyncio
async def test_escalate_to_legal_notifies_admins(db_path):
    """escalate_to_legal отправляет уведомление админам."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)

    notifier = AsyncMock()
    notifier.send_to_admins = AsyncMock()
    notifier.notify_warn1_sent = AsyncMock()
    notifier.notify_legal_request = AsyncMock()
    engine = _make_engine(db_path, notifier=notifier)

    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    with patch("workflow.engine.asyncio.sleep", new_callable=AsyncMock):
        await engine.send_warn1(wf_id)

    wf_db = SellerWorkflowDB(db_path)
    await wf_db.update_status(wf_id, "WARN2_SENT")

    await engine.escalate_to_legal(wf_id)

    # Уведомление через типизированный метод
    notifier.notify_legal_request.assert_called_once()
    call_kwargs = notifier.notify_legal_request.call_args
    assert call_kwargs is not None


# ---------------------------------------------------------------------------
# Экспорт с пустым диалогом
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_empty_dialog_workflow(db_path):
    """Экспорт workflow без сообщений — не падает."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)

    wf_db = SellerWorkflowDB(db_path)
    wf_id = await wf_db.create_workflow("M001")
    await wf_db.add_product_to_workflow(wf_id, "SKU001")

    exporter = _make_exporter(db_path)

    # Текстовый лог — пустой но не падает
    dialog = await exporter.export_dialog_log(wf_id)
    assert "Всего сообщений: 0" in dialog

    # Таймлайн — только DETECTED
    timeline = await exporter.export_timeline(wf_id)
    assert len(timeline["events"]) >= 1
    assert timeline["events"][0]["event"] == "DETECTED"


# ---------------------------------------------------------------------------
# Множество товаров в экспорте
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_multiple_products(db_path):
    """Экспорт с несколькими товарами — все присутствуют."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    for i in range(5):
        await _seed_product(
            db_path, f"SKU{i:03d}",
            f"https://kaspi.kz/shop/p/sku{i:03d}",
            f"Товар {i}",
        )

    engine = _make_engine(db_path)
    wf_id = await engine.on_new_seller_detected(
        "M001", [f"SKU{i:03d}" for i in range(5)]
    )
    with patch("workflow.engine.asyncio.sleep", new_callable=AsyncMock):
        await engine.send_warn1(wf_id)

    wf_db = SellerWorkflowDB(db_path)
    await wf_db.update_status(wf_id, "WARN2_SENT")
    request_id = await engine.escalate_to_legal(wf_id)

    exporter = _make_exporter(db_path)
    result = await exporter.export_legal_request(request_id, fmt="json")
    data = json.loads(result.decode("utf-8"))

    assert len(data["products"]) == 5
    for i in range(5):
        assert any(p["product_id"] == f"SKU{i:03d}" for p in data["products"])
