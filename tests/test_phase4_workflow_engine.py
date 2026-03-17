"""
Тесты Фазы 4: Движок воронки (WorkflowEngine).
Запуск: pytest tests/test_phase4_workflow_engine.py -v

Тестируем бизнес-логику engine через mock WhatsApp и classifier.
"""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    return tmp_path / "test_wf.db"


async def _init_db(db_path: Path):
    """Инициализация схемы + миграции."""
    await DatabaseSchema.init_db(db_path)
    migrations = DatabaseMigrations(db_path)
    await migrations.run_migrations()


async def _seed_seller(db_path: Path, merchant_id: str = "M001",
                       name: str = "Test Shop",
                       phone: str = "+77011234567"):
    """Создать продавца-заглушку."""
    sellers = SellersDB(db_path)
    await sellers.add_seller(merchant_id, name, phone)


async def _seed_product(db_path: Path, sku: str = "SKU001",
                        url: str = "https://kaspi.kz/shop/p/sku001",
                        title: str = "Тестовый товар"):
    """Создать товар-заглушку."""
    products = ProductsDB(db_path)
    await products.add_product(sku, url, title)


async def _seed_product_seller(db_path: Path, product_id: str = "SKU001",
                               seller_id: str = "M001", price: float = 100.0):
    """Связать товар и продавца."""
    ps_db = ProductSellersDB(db_path)
    await ps_db.add_or_update_link(product_id, seller_id, price)


def _make_engine(db_path, wa_client=None, classifier=None,
                 notifier=None, scanner=None):
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
        scanner=scanner,
    )


# ---------------------------------------------------------------------------
# on_new_seller_detected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_new_seller_creates_workflow(db_path):
    """Создание нового workflow при обнаружении продавца."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)

    engine = _make_engine(db_path)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

    assert isinstance(wf_id, int)

    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "NEW_SELLER_ATTACH"
    assert wf["seller_id"] == "M001"

    products = await wf_db.get_workflow_products(wf_id)
    assert len(products) == 1
    assert products[0]["product_id"] == "SKU001"


@pytest.mark.asyncio
async def test_on_new_seller_adds_to_existing(db_path):
    """При повторном обнаружении — добавляет товар к существующему workflow."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path, "SKU001")
    await _seed_product(db_path, "SKU002", title="Товар 2")

    engine = _make_engine(db_path)
    wf_id1 = await engine.on_new_seller_detected("M001", ["SKU001"])
    wf_id2 = await engine.on_new_seller_detected("M001", ["SKU002"])

    assert wf_id1 == wf_id2

    wf_db = SellerWorkflowDB(db_path)
    products = await wf_db.get_workflow_products(wf_id1)
    assert len(products) == 2


# ---------------------------------------------------------------------------
# send_warn1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_warn1_success(db_path):
    """WARN1 отправляется, статус обновляется, лог записывается."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "msg001"})
    notifier = AsyncMock()
    notifier.send_to_admins = AsyncMock()
    notifier.notify_warn1_sent = AsyncMock()

    engine = _make_engine(db_path, wa_client=wa_client, notifier=notifier)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

    result = await engine.send_warn1(wf_id)

    assert result is True
    wa_client.send_text.assert_called_once()
    notifier.notify_warn1_sent.assert_called()

    # Проверить статус
    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "WARN1_SENT"
    assert wf["warn1_sent_at"] is not None

    # Проверить лог
    log_db = MessageLogDB(db_path)
    messages = await log_db.get_messages_for_workflow(wf_id)
    assert len(messages) == 1
    assert messages[0]["direction"] == "OUT"
    assert messages[0]["wa_message_id"] == "msg001"


@pytest.mark.asyncio
async def test_send_warn1_no_phone(db_path):
    """WARN1 не отправляется если нет телефона."""
    await _init_db(db_path)
    await _seed_seller(db_path, phone=None)
    await _seed_product(db_path)

    engine = _make_engine(db_path)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

    result = await engine.send_warn1(wf_id)
    assert result is False


@pytest.mark.asyncio
async def test_send_warn1_whatsapp_error(db_path):
    """При ошибке WhatsApp return False, статус не меняется."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(side_effect=Exception("Connection error"))
    notifier = AsyncMock()
    notifier.send_to_admins = AsyncMock()

    engine = _make_engine(db_path, wa_client=wa_client, notifier=notifier)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

    result = await engine.send_warn1(wf_id)
    assert result is False

    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "NEW_SELLER_ATTACH"


# ---------------------------------------------------------------------------
# send_warn2
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_warn2_success(db_path):
    """WARN2 отправляется, статус обновляется."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "msg002"})
    notifier = AsyncMock()
    notifier.send_to_admins = AsyncMock()

    engine = _make_engine(db_path, wa_client=wa_client, notifier=notifier)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

    # Послать WARN1 сначала
    await engine.send_warn1(wf_id)
    wa_client.send_text.reset_mock()

    # Теперь WARN2
    result = await engine.send_warn2(wf_id)
    assert result is True

    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "WARN2_SENT"
    assert wf["warn2_sent_at"] is not None


# ---------------------------------------------------------------------------
# handle_incoming_message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_incoming_unknown(db_path):
    """Входящее от неизвестного номера — пропускается."""
    await _init_db(db_path)

    engine = _make_engine(db_path)
    # Не должно бросить исключение
    await engine.handle_incoming_message("77099999999", "Привет", "Unknown")


@pytest.mark.asyncio
async def test_handle_incoming_known_seller(db_path):
    """Входящее от продавца: классификация, лог, ответ."""
    await _init_db(db_path)
    await _seed_seller(db_path, phone="77011234567")
    await _seed_product(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "reply01"})
    classifier = AsyncMock()
    classifier.classify = AsyncMock(return_value=ClassificationResult(
        classification=ClassificationType.DIDNT_KNOW, confidence=0.9
    ))
    notifier = AsyncMock()
    notifier.send_to_admins = AsyncMock()

    engine = _make_engine(db_path, wa_client=wa_client,
                          classifier=classifier, notifier=notifier)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    await engine.send_warn1(wf_id)

    wa_client.send_text.reset_mock()

    # Входящее от продавца
    await engine.handle_incoming_message(
        "77011234567", "Я не знал, сейчас уберу", "Test Shop"
    )

    # Классификатор вызван
    classifier.classify.assert_called_once()

    # Проверить лог — минимум 1 IN + 1 OUT (WARN1 + incoming reply)
    log_db = MessageLogDB(db_path)
    messages = await log_db.get_messages_for_workflow(wf_id)
    directions = [m["direction"] for m in messages]
    assert "IN" in directions

    # Статус должен стать DIALOG_ACTIVE
    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "DIALOG_ACTIVE"


@pytest.mark.asyncio
async def test_handle_incoming_already_removed(db_path):
    """При ALREADY_REMOVED — запускается микро-скан."""
    await _init_db(db_path)
    await _seed_seller(db_path, phone="77011234567")
    await _seed_product(db_path)
    await _seed_product_seller(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "reply02"})
    classifier = AsyncMock()
    classifier.classify = AsyncMock(return_value=ClassificationResult(
        classification=ClassificationType.ALREADY_REMOVED, confidence=0.9
    ))
    notifier = AsyncMock()
    notifier.send_to_admins = AsyncMock()

    # Scanner mock — возвращает True (всё ещё на карточке)
    scanner = AsyncMock()
    scanner.check_seller_on_product = AsyncMock(return_value=True)

    engine = _make_engine(db_path, wa_client=wa_client,
                          classifier=classifier, notifier=notifier,
                          scanner=scanner)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    await engine.send_warn1(wf_id)
    wa_client.send_text.reset_mock()

    await engine.handle_incoming_message(
        "77011234567", "Уже снял", "Test Shop"
    )

    # Должен был вызваться scanner для проверки
    scanner.check_seller_on_product.assert_called()

    # Workflow не закрыт (seller still attached)
    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] != "CLOSED"


@pytest.mark.asyncio
async def test_handle_incoming_already_removed_confirmed(db_path):
    """При ALREADY_REMOVED + подтверждение скана — workflow закрывается."""
    await _init_db(db_path)
    await _seed_seller(db_path, phone="77011234567")
    await _seed_product(db_path)
    await _seed_product_seller(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "reply03"})
    classifier = AsyncMock()
    classifier.classify = AsyncMock(return_value=ClassificationResult(
        classification=ClassificationType.ALREADY_REMOVED, confidence=0.9
    ))
    notifier = AsyncMock()
    notifier.send_to_admins = AsyncMock()

    # Scanner mock — возвращает False (уже убрал)
    scanner = AsyncMock()
    scanner.check_seller_on_product = AsyncMock(return_value=False)

    engine = _make_engine(db_path, wa_client=wa_client,
                          classifier=classifier, notifier=notifier,
                          scanner=scanner)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    await engine.send_warn1(wf_id)
    wa_client.send_text.reset_mock()

    await engine.handle_incoming_message(
        "77011234567", "Уже снял", "Test Shop"
    )

    # Workflow должен быть закрыт
    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "CLOSED"


# ---------------------------------------------------------------------------
# check_detachment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_detachment_all_gone(db_path):
    """Если продавец убрал все товары — check_detachment True."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)
    await _seed_product_seller(db_path)

    scanner = AsyncMock()
    scanner.check_seller_on_product = AsyncMock(return_value=False)

    engine = _make_engine(db_path, scanner=scanner)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

    result = await engine.check_detachment(wf_id)
    assert result is True


@pytest.mark.asyncio
async def test_check_detachment_still_there(db_path):
    """Если продавец всё ещё на карточке — check_detachment False."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)
    await _seed_product_seller(db_path)

    scanner = AsyncMock()
    scanner.check_seller_on_product = AsyncMock(return_value=True)

    engine = _make_engine(db_path, scanner=scanner)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

    result = await engine.check_detachment(wf_id)
    assert result is False


# ---------------------------------------------------------------------------
# escalate_to_legal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_escalate_to_legal(db_path):
    """Эскалация создаёт юрзаявку и меняет статус."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)

    notifier = AsyncMock()
    notifier.send_to_admins = AsyncMock()
    notifier.notify_legal_request = AsyncMock()

    engine = _make_engine(db_path, notifier=notifier)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

    request_id = await engine.escalate_to_legal(wf_id)
    assert isinstance(request_id, int)

    # Статус изменён
    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "LEGAL_REQUEST_CREATED"

    # Юрзаявка создана
    legal_db = LegalRequestsDB(db_path)
    req = await legal_db.get_request(request_id)
    assert req is not None
    assert req["seller_id"] == "M001"
    assert req["shop_name"] == "Test Shop"

    # Уведомление отправлено
    notifier.notify_legal_request.assert_called()


# ---------------------------------------------------------------------------
# close_workflow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_workflow(db_path):
    """close_workflow переводит в CLOSED."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)

    notifier = AsyncMock()
    notifier.send_to_admins = AsyncMock()

    engine = _make_engine(db_path, notifier=notifier)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

    await engine.close_workflow(wf_id, reason="test_close")

    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "CLOSED"


# ---------------------------------------------------------------------------
# handle_recidive
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_handle_recidive(db_path):
    """Рецидив: новый workflow + сразу WARN2."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "recid01"})
    notifier = AsyncMock()
    notifier.send_to_admins = AsyncMock()

    engine = _make_engine(db_path, wa_client=wa_client, notifier=notifier)

    wf_id = await engine.handle_recidive("M001", ["SKU001"])
    assert isinstance(wf_id, int)

    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    # Статус WARN2_SENT (RECIDIVE → WARN2_SENT)
    assert wf["status"] == "WARN2_SENT"

    # WhatsApp вызван
    wa_client.send_text.assert_called()


# ---------------------------------------------------------------------------
# antispam
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_antispam_limits(db_path):
    """Антиспам: после 3 сообщений в день — лимит."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)
    await _seed_product(db_path, "SKU002", title="Товар 2")
    await _seed_product(db_path, "SKU003", title="Товар 3")
    await _seed_product(db_path, "SKU004", title="Товар 4")

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "spam01"})
    notifier = AsyncMock()
    notifier.send_to_admins = AsyncMock()

    engine = _make_engine(db_path, wa_client=wa_client, notifier=notifier)

    # Записать 3 исходящих сообщения в лог (имитация)
    log_db = MessageLogDB(db_path)
    for i in range(3):
        await log_db.log_message(
            workflow_id=None,
            seller_id="M001",
            direction="OUT",
            text=f"Test message {i}",
        )

    # Создать workflow и попытаться отправить WARN1
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    result = await engine.send_warn1(wf_id)

    # Должен быть заблокирован антиспамом
    assert result is False
    wa_client.send_text.assert_not_called()


# ---------------------------------------------------------------------------
# has_closed_workflow (DAO)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_has_closed_workflow(db_path):
    """has_closed_workflow определяет рецидив."""
    await _init_db(db_path)
    await _seed_seller(db_path)

    wf_db = SellerWorkflowDB(db_path)

    # Нет закрытых
    assert await wf_db.has_closed_workflow("M001") is False

    # Создать и закрыть
    wf_id = await wf_db.create_workflow("M001")
    await wf_db.update_status(wf_id, "CLOSED")

    assert await wf_db.has_closed_workflow("M001") is True


# ---------------------------------------------------------------------------
# Full cycle: NEW → WARN1 → WARN2 → LEGAL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_escalation_cycle(db_path):
    """Полный цикл: создание → WARN1 → WARN2 → юрзаявка."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "cycle01"})
    notifier = AsyncMock()
    notifier.send_to_admins = AsyncMock()

    engine = _make_engine(db_path, wa_client=wa_client, notifier=notifier)

    # 1. Обнаружение
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "NEW_SELLER_ATTACH"

    # 2. WARN1
    await engine.send_warn1(wf_id)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "WARN1_SENT"

    # 3. WARN2
    await engine.send_warn2(wf_id)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "WARN2_SENT"

    # 4. Юрзаявка
    req_id = await engine.escalate_to_legal(wf_id)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "LEGAL_REQUEST_CREATED"
    assert req_id is not None

    # Проверить лог сообщений
    log_db = MessageLogDB(db_path)
    messages = await log_db.get_messages_for_workflow(wf_id)
    assert len(messages) >= 2  # WARN1 + WARN2


@pytest.mark.asyncio
async def test_detach_at_any_stage(db_path):
    """Отсоединение на любом этапе → CLOSED."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "d01"})
    notifier = AsyncMock()
    notifier.send_to_admins = AsyncMock()

    engine = _make_engine(db_path, wa_client=wa_client, notifier=notifier)

    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    await engine.send_warn1(wf_id)

    # Закрыть на этапе WARN1_SENT
    await engine.close_workflow(wf_id, reason="detached_during_warn1")

    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "CLOSED"
