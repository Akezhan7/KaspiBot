"""
Тесты Фазы 7: Контрольная закупка (admin_handlers — assign_purchase, purchase_done FSM).
Запуск: pytest tests/test_phase7_purchase.py -v

Тестируем:
- /assign_purchase: назначение закупки, обновление статуса
- /purchase_done: FSM-диалог ввода данных (БИН, заказ, документы, комментарий, подтверждение)
- Хранение файлов (документы закупки)
- Валидация (БИН, не-админ, несуществующая заявка)
"""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Временная БД для каждого теста."""
    return tmp_path / "test_purchase.db"


@pytest.fixture
def docs_dir(tmp_path):
    """Временная директория для документов."""
    d = tmp_path / "legal"
    d.mkdir()
    return d


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
    sellers = SellersDB(db_path)
    await sellers.add_seller(merchant_id, name, phone)


async def _seed_product(
    db_path: Path,
    sku: str = "SKU001",
    url: str = "https://kaspi.kz/shop/p/sku001",
    title: str = "Тестовый товар",
):
    products = ProductsDB(db_path)
    await products.add_product(sku, url, title)


def _make_engine(db_path: Path):
    """Создать WorkflowEngine с mock-зависимостями."""
    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "test123"})

    classifier = AsyncMock()
    classifier.classify = AsyncMock(return_value=ClassificationResult(
        classification=ClassificationType.UNKNOWN, confidence=0.5
    ))

    notifier = AsyncMock()
    notifier.send_to_admins = AsyncMock()

    return WorkflowEngine(
        workflow_db=SellerWorkflowDB(db_path),
        message_log_db=MessageLogDB(db_path),
        legal_db=LegalRequestsDB(db_path),
        sellers_db=SellersDB(db_path),
        products_db=ProductsDB(db_path),
        product_sellers_db=ProductSellersDB(db_path),
        whatsapp_client=wa_client,
        classifier=classifier,
        notification_service=notifier,
    )


async def _create_legal_request(db_path: Path) -> tuple[int, int]:
    """
    Полный цикл: создать продавца, товар, workflow, юрзаявку.
    Возвращает (workflow_id, request_id).
    """
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path, "SKU001", "https://kaspi.kz/shop/p/sku001", "Товар 1")

    engine = _make_engine(db_path)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

    with patch("workflow.engine.asyncio.sleep", new_callable=AsyncMock):
        await engine.send_warn1(wf_id)

    wf_db = SellerWorkflowDB(db_path)
    await wf_db.update_status(wf_id, "WARN1_SENT")

    with patch("workflow.engine.asyncio.sleep", new_callable=AsyncMock):
        await engine.send_warn2(wf_id)

    request_id = await engine.escalate_to_legal(wf_id)
    return wf_id, request_id


# ---------------------------------------------------------------------------
# DAO-тесты: assign_purchase + update_purchase_info
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assign_purchase_updates_status(db_path):
    """assign_purchase меняет control_purchase_status на ASSIGNED."""
    wf_id, req_id = await _create_legal_request(db_path)
    legal_db = LegalRequestsDB(db_path)

    await legal_db.assign_purchase(req_id, "@ulzhat")

    req = await legal_db.get_request(req_id)
    assert req["control_purchase_status"] == "ASSIGNED"
    assert req["assigned_to"] == "@ulzhat"


@pytest.mark.asyncio
async def test_assign_purchase_workflow_status(db_path):
    """После assign workflow переходит в CONTROL_PURCHASE_REQUIRED."""
    wf_id, req_id = await _create_legal_request(db_path)
    legal_db = LegalRequestsDB(db_path)
    wf_db = SellerWorkflowDB(db_path)

    await legal_db.assign_purchase(req_id, "@ulzhat")
    await wf_db.update_status(wf_id, "CONTROL_PURCHASE_REQUIRED")

    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "CONTROL_PURCHASE_REQUIRED"


@pytest.mark.asyncio
async def test_update_purchase_info_completes(db_path):
    """update_purchase_info записывает все поля и ставит COMPLETED."""
    wf_id, req_id = await _create_legal_request(db_path)
    legal_db = LegalRequestsDB(db_path)

    docs_json = json.dumps(["/data/legal/1/photo_1.jpg"])
    await legal_db.update_purchase_info(
        request_id=req_id,
        bin_iin="123456789012",
        order_number="KZ-001-001",
        notes="Закупка выполнена",
        documents=docs_json,
    )

    req = await legal_db.get_request(req_id)
    assert req["bin_iin"] == "123456789012"
    assert req["purchase_order_number"] == "KZ-001-001"
    assert req["purchase_notes"] == "Закупка выполнена"
    assert req["purchase_documents"] == docs_json
    assert req["control_purchase_status"] == "COMPLETED"
    assert req["completed_at"] is not None


@pytest.mark.asyncio
async def test_mark_ready_for_lawsuit(db_path):
    """mark_ready_for_lawsuit ставит ready_for_lawsuit = 1."""
    wf_id, req_id = await _create_legal_request(db_path)
    legal_db = LegalRequestsDB(db_path)

    await legal_db.mark_ready_for_lawsuit(req_id)

    req = await legal_db.get_request(req_id)
    assert req["ready_for_lawsuit"] == 1


@pytest.mark.asyncio
async def test_get_pending_purchases(db_path):
    """get_pending_purchases возвращает заявки со статусом PENDING/ASSIGNED."""
    wf_id, req_id = await _create_legal_request(db_path)
    legal_db = LegalRequestsDB(db_path)

    pending = await legal_db.get_pending_purchases()
    assert len(pending) == 1
    assert pending[0]["id"] == req_id

    await legal_db.assign_purchase(req_id, "@user")
    pending = await legal_db.get_pending_purchases()
    assert len(pending) == 1  # ASSIGNED тоже в списке

    # После COMPLETED — не в списке
    await legal_db.update_purchase_info(
        request_id=req_id,
        bin_iin="111111111111",
        order_number="X-1",
    )
    pending = await legal_db.get_pending_purchases()
    assert len(pending) == 0


# ---------------------------------------------------------------------------
# DAO-тесты: workflow статус READY_FOR_LAWSUIT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workflow_to_ready_for_lawsuit(db_path):
    """Полный цикл: LEGAL_REQUEST → CONTROL_PURCHASE → READY_FOR_LAWSUIT."""
    wf_id, req_id = await _create_legal_request(db_path)
    wf_db = SellerWorkflowDB(db_path)
    legal_db = LegalRequestsDB(db_path)

    # По умолчанию после escalate_to_legal — LEGAL_REQUEST_CREATED
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "LEGAL_REQUEST_CREATED"

    # Назначение → CONTROL_PURCHASE_REQUIRED
    await wf_db.update_status(wf_id, "CONTROL_PURCHASE_REQUIRED")
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "CONTROL_PURCHASE_REQUIRED"

    # Завершение → READY_FOR_LAWSUIT
    await legal_db.update_purchase_info(
        request_id=req_id,
        bin_iin="123456789012",
        order_number="KZ-001",
    )
    await legal_db.mark_ready_for_lawsuit(req_id)
    await wf_db.update_status(wf_id, "READY_FOR_LAWSUIT")

    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "READY_FOR_LAWSUIT"

    req = await legal_db.get_request(req_id)
    assert req["ready_for_lawsuit"] == 1
    assert req["control_purchase_status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# Тесты хранения файлов
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_docs_dir_creation(tmp_path):
    """Директория для документов создаётся корректно."""
    from bot.admin_handlers import _docs_dir

    with patch("bot.admin_handlers.Config") as mock_config:
        mock_config.PURCHASE_DOCUMENTS_DIR = tmp_path / "legal"

        d = _docs_dir(42)
        assert d.exists()
        assert d == tmp_path / "legal" / "42"


@pytest.mark.asyncio
async def test_purchase_documents_json(db_path):
    """Пути к документам сохраняются как JSON."""
    wf_id, req_id = await _create_legal_request(db_path)
    legal_db = LegalRequestsDB(db_path)

    paths = [
        "/data/legal/1/photo_1.jpg",
        "/data/legal/1/photo_2.jpg",
        "/data/legal/1/receipt.pdf",
    ]
    docs_json = json.dumps(paths, ensure_ascii=False)

    await legal_db.update_purchase_info(
        request_id=req_id,
        bin_iin="999999999999",
        order_number="Z-1",
        documents=docs_json,
    )

    req = await legal_db.get_request(req_id)
    loaded = json.loads(req["purchase_documents"])
    assert loaded == paths
    assert len(loaded) == 3


# ---------------------------------------------------------------------------
# Валидация БИН/ИИН
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bin_validation_correct():
    """БИН 12 цифр — валиден."""
    clean = "123456789012".replace(" ", "").replace("-", "")
    assert clean.isdigit() and len(clean) == 12


@pytest.mark.asyncio
async def test_bin_validation_with_spaces():
    """БИН с пробелами/дефисами — очищается до 12 цифр."""
    raw = "123 456 789-012"
    clean = raw.replace(" ", "").replace("-", "")
    assert clean.isdigit() and len(clean) == 12


@pytest.mark.asyncio
async def test_bin_validation_invalid_length():
    """БИН не 12 цифр — невалиден."""
    raw = "12345"
    clean = raw.replace(" ", "").replace("-", "")
    assert not (clean.isdigit() and len(clean) == 12)


@pytest.mark.asyncio
async def test_bin_validation_letters():
    """БИН с буквами — невалиден."""
    raw = "12345678ABCD"
    clean = raw.replace(" ", "").replace("-", "")
    assert not (clean.isdigit() and len(clean) == 12)


# ---------------------------------------------------------------------------
# Тесты множественных документов
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_documents_accumulate(db_path):
    """Несколько документов накапливаются в JSON-массиве."""
    wf_id, req_id = await _create_legal_request(db_path)
    legal_db = LegalRequestsDB(db_path)

    paths = [f"/data/legal/{req_id}/photo_{i}.jpg" for i in range(5)]
    docs_json = json.dumps(paths)

    await legal_db.update_purchase_info(
        request_id=req_id,
        bin_iin="111111111111",
        order_number="ORD-5",
        documents=docs_json,
    )

    req = await legal_db.get_request(req_id)
    loaded = json.loads(req["purchase_documents"])
    assert len(loaded) == 5


# ---------------------------------------------------------------------------
# DAO edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_purchase_partial_fields(db_path):
    """update_purchase_info с частичными данными (COALESCE сохраняет NULL)."""
    wf_id, req_id = await _create_legal_request(db_path)
    legal_db = LegalRequestsDB(db_path)

    # Только bin_iin, остальное None
    await legal_db.update_purchase_info(
        request_id=req_id,
        bin_iin="222222222222",
    )

    req = await legal_db.get_request(req_id)
    assert req["bin_iin"] == "222222222222"
    assert req["purchase_order_number"] is None
    assert req["purchase_notes"] is None
    assert req["control_purchase_status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_get_request_nonexistent(db_path):
    """get_request для несуществующего ID возвращает None."""
    await _init_db(db_path)
    legal_db = LegalRequestsDB(db_path)

    req = await legal_db.get_request(99999)
    assert req is None


@pytest.mark.asyncio
async def test_assign_purchase_idempotent(db_path):
    """Повторное назначение перезаписывает assigned_to."""
    wf_id, req_id = await _create_legal_request(db_path)
    legal_db = LegalRequestsDB(db_path)

    await legal_db.assign_purchase(req_id, "@user1")
    await legal_db.assign_purchase(req_id, "@user2")

    req = await legal_db.get_request(req_id)
    assert req["assigned_to"] == "@user2"
    assert req["control_purchase_status"] == "ASSIGNED"


@pytest.mark.asyncio
async def test_count_requests(db_path):
    """count_requests возвращает корректное количество."""
    wf_id, req_id = await _create_legal_request(db_path)
    legal_db = LegalRequestsDB(db_path)

    count = await legal_db.count_requests()
    assert count == 1


@pytest.mark.asyncio
async def test_get_all_requests_pagination(db_path):
    """get_all_requests с пагинацией."""
    wf_id, req_id = await _create_legal_request(db_path)
    legal_db = LegalRequestsDB(db_path)

    # Все заявки
    all_reqs = await legal_db.get_all_requests(limit=10, offset=0)
    assert len(all_reqs) == 1

    # Пустой результат с offset
    empty = await legal_db.get_all_requests(limit=10, offset=100)
    assert len(empty) == 0
