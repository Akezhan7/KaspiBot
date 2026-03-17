"""
Тесты Фазы 5: Планировщик эскалации (EscalationScheduler).
Запуск: pytest tests/test_phase5_escalation.py -v

Тестируем автоматические переходы по таймеру через mock зависимости.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
from workflow.escalation import EscalationScheduler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Временная БД для каждого теста."""
    return tmp_path / "test_escalation.db"


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


async def _age_workflow(db_path: Path, workflow_id: int, hours: int) -> None:
    """Сдвинуть updated_at назад на N часов (для тестирования эскалации)."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            UPDATE seller_workflows
            SET updated_at = datetime('now', ? || ' hours')
            WHERE id = ?
            """,
            (f"-{hours}", workflow_id),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# process_new_sellers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_new_sellers_sends_warn1(db_path):
    """Новый продавец (NEW_SELLER_ATTACH) получает WARN1."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "msg001"})
    engine = _make_engine(db_path, wa_client=wa_client)

    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

    scheduler = EscalationScheduler(engine)
    await scheduler.process_new_sellers()

    wa_client.send_text.assert_called_once()

    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "WARN1_SENT"


@pytest.mark.asyncio
async def test_process_new_sellers_no_phone_skips(db_path):
    """Если нет телефона — WARN1 не отправляется, но ошибки нет."""
    await _init_db(db_path)
    await _seed_seller(db_path, phone=None)
    await _seed_product(db_path)

    engine = _make_engine(db_path)
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

    scheduler = EscalationScheduler(engine)
    await scheduler.process_new_sellers()

    wf_db = SellerWorkflowDB(db_path)
    wf = await wf_db.get_workflow(wf_id)
    # Остаётся в NEW_SELLER_ATTACH т.к. send_warn1 вернул False
    assert wf["status"] == "NEW_SELLER_ATTACH"


@pytest.mark.asyncio
async def test_process_new_sellers_empty(db_path):
    """Нет новых продавцов — ничего не делается."""
    await _init_db(db_path)

    engine = _make_engine(db_path)
    scheduler = EscalationScheduler(engine)
    # Не должен вызвать ошибку
    await scheduler.process_new_sellers()


# ---------------------------------------------------------------------------
# process_warn1_expiry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_warn1_expiry_sends_warn2(db_path):
    """Просроченный WARN1 (>24ч) → проверка + WARN2."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)
    await _seed_product_seller(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "msg002"})

    # Scanner всегда говорит «всё ещё прилеплен»
    scanner = AsyncMock()
    scanner.check_seller_on_product = AsyncMock(return_value=True)

    engine = _make_engine(db_path, wa_client=wa_client, scanner=scanner)

    # Создать workflow и установить WARN1_SENT
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    wf_db = SellerWorkflowDB(db_path)
    await wf_db.update_status(wf_id, "WARN1_SENT")

    # Сдвинуть updated_at на 25 часов назад
    await _age_workflow(db_path, wf_id, 25)

    scheduler = EscalationScheduler(engine)
    await scheduler.process_warn1_expiry()

    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "WARN2_SENT"
    assert wf["warn2_sent_at"] is not None


@pytest.mark.asyncio
async def test_warn1_expiry_detached_closes(db_path):
    """Просроченный WARN1, но продавец отсоединился → CLOSED."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)
    await _seed_product_seller(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "msg003"})

    # Scanner говорит «отсоединился»
    scanner = AsyncMock()
    scanner.check_seller_on_product = AsyncMock(return_value=False)

    engine = _make_engine(db_path, wa_client=wa_client, scanner=scanner)

    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    wf_db = SellerWorkflowDB(db_path)
    await wf_db.update_status(wf_id, "WARN1_SENT")
    await _age_workflow(db_path, wf_id, 25)

    scheduler = EscalationScheduler(engine)
    await scheduler.process_warn1_expiry()

    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "CLOSED"


@pytest.mark.asyncio
async def test_warn1_not_expired_no_action(db_path):
    """WARN1_SENT < 24ч — никаких действий."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)

    engine = _make_engine(db_path)

    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    wf_db = SellerWorkflowDB(db_path)
    await wf_db.update_status(wf_id, "WARN1_SENT")

    # НЕ сдвигаем время — 0 часов назад
    scheduler = EscalationScheduler(engine)
    await scheduler.process_warn1_expiry()

    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "WARN1_SENT"  # Без изменений


# ---------------------------------------------------------------------------
# process_warn2_expiry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_warn2_expiry_creates_legal(db_path):
    """Просроченный WARN2 (>24ч) → юрзаявка."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)
    await _seed_product_seller(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "msg004"})

    # Продавец всё ещё прилеплен
    scanner = AsyncMock()
    scanner.check_seller_on_product = AsyncMock(return_value=True)

    engine = _make_engine(db_path, wa_client=wa_client, scanner=scanner)

    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    wf_db = SellerWorkflowDB(db_path)
    await wf_db.update_status(wf_id, "WARN1_SENT")
    await wf_db.update_status(wf_id, "WARN2_SENT")
    await _age_workflow(db_path, wf_id, 25)

    scheduler = EscalationScheduler(engine)
    await scheduler.process_warn2_expiry()

    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "LEGAL_REQUEST_CREATED"

    # Проверить юрзаявку
    legal_db = LegalRequestsDB(db_path)
    request = await legal_db.get_request_by_workflow(wf_id)
    assert request is not None
    assert request["seller_id"] == "M001"


@pytest.mark.asyncio
async def test_warn2_expiry_detached_closes(db_path):
    """Просроченный WARN2, но продавец отсоединился → CLOSED."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)
    await _seed_product_seller(db_path)

    scanner = AsyncMock()
    scanner.check_seller_on_product = AsyncMock(return_value=False)

    engine = _make_engine(db_path, scanner=scanner)

    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    wf_db = SellerWorkflowDB(db_path)
    await wf_db.update_status(wf_id, "WARN1_SENT")
    await wf_db.update_status(wf_id, "WARN2_SENT")
    await _age_workflow(db_path, wf_id, 25)

    scheduler = EscalationScheduler(engine)
    await scheduler.process_warn2_expiry()

    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "CLOSED"


# ---------------------------------------------------------------------------
# process_dialog_timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dialog_timeout_after_warn1_sends_warn2(db_path):
    """DIALOG_ACTIVE > 24ч (после WARN1, без WARN2) → WARN2."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)
    await _seed_product_seller(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "msg005"})

    scanner = AsyncMock()
    scanner.check_seller_on_product = AsyncMock(return_value=True)

    engine = _make_engine(db_path, wa_client=wa_client, scanner=scanner)

    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    wf_db = SellerWorkflowDB(db_path)
    # WARN1 отправлен, потом диалог начался
    await wf_db.update_status(wf_id, "WARN1_SENT")
    await wf_db.update_status(wf_id, "DIALOG_ACTIVE")
    await _age_workflow(db_path, wf_id, 25)

    scheduler = EscalationScheduler(engine)
    await scheduler.process_dialog_timeout()

    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "WARN2_SENT"


@pytest.mark.asyncio
async def test_dialog_timeout_after_warn2_creates_legal(db_path):
    """DIALOG_ACTIVE > 24ч (после WARN2) → юрзаявка."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)
    await _seed_product_seller(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "msg006"})

    scanner = AsyncMock()
    scanner.check_seller_on_product = AsyncMock(return_value=True)

    engine = _make_engine(db_path, wa_client=wa_client, scanner=scanner)

    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    wf_db = SellerWorkflowDB(db_path)
    await wf_db.update_status(wf_id, "WARN1_SENT")
    await wf_db.update_status(wf_id, "WARN2_SENT")
    await wf_db.update_status(wf_id, "DIALOG_ACTIVE")
    await _age_workflow(db_path, wf_id, 25)

    scheduler = EscalationScheduler(engine)
    await scheduler.process_dialog_timeout()

    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "LEGAL_REQUEST_CREATED"


@pytest.mark.asyncio
async def test_dialog_timeout_detached_closes(db_path):
    """DIALOG_ACTIVE > 24ч, но продавец отсоединился → CLOSED."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)
    await _seed_product_seller(db_path)

    scanner = AsyncMock()
    scanner.check_seller_on_product = AsyncMock(return_value=False)

    engine = _make_engine(db_path, scanner=scanner)

    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    wf_db = SellerWorkflowDB(db_path)
    await wf_db.update_status(wf_id, "WARN1_SENT")
    await wf_db.update_status(wf_id, "DIALOG_ACTIVE")
    await _age_workflow(db_path, wf_id, 25)

    scheduler = EscalationScheduler(engine)
    await scheduler.process_dialog_timeout()

    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "CLOSED"


# ---------------------------------------------------------------------------
# Race condition / оптимистичная блокировка
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_warn1_expiry_race_condition_skips(db_path):
    """Если статус изменился между выборкой и действием → пропуск."""
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)
    await _seed_product_seller(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "msg007"})

    scanner = AsyncMock()
    scanner.check_seller_on_product = AsyncMock(return_value=True)

    engine = _make_engine(db_path, wa_client=wa_client, scanner=scanner)

    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
    wf_db = SellerWorkflowDB(db_path)
    await wf_db.update_status(wf_id, "WARN1_SENT")
    await _age_workflow(db_path, wf_id, 25)

    # Имитируем: между check_detachment и update_status_if
    # webhook изменил статус на DIALOG_ACTIVE
    original_check = engine.check_detachment

    async def check_detachment_and_change_status(wid):
        result = await original_check(wid)
        # Другой процесс (webhook) меняет статус
        await wf_db.update_status(wid, "DIALOG_ACTIVE")
        return result

    engine.check_detachment = check_detachment_and_change_status

    scheduler = EscalationScheduler(engine)
    await scheduler.process_warn1_expiry()

    wf = await wf_db.get_workflow(wf_id)
    # update_status_if не сработал, т.к. статус уже DIALOG_ACTIVE
    assert wf["status"] == "DIALOG_ACTIVE"


# ---------------------------------------------------------------------------
# Несколько workflow одновременно
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_multiple_new_sellers(db_path):
    """Несколько новых продавцов обрабатываются за один прогон."""
    await _init_db(db_path)

    # Создать 3 продавца
    for i in range(1, 4):
        await _seed_seller(db_path, f"M00{i}", f"Shop {i}", f"+7701123456{i}")
        await _seed_product(db_path, f"SKU00{i}", title=f"Товар {i}")

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "batch_msg"})
    engine = _make_engine(db_path, wa_client=wa_client)

    for i in range(1, 4):
        await engine.on_new_seller_detected(f"M00{i}", [f"SKU00{i}"])

    scheduler = EscalationScheduler(engine)
    await scheduler.process_new_sellers()

    assert wa_client.send_text.call_count == 3

    wf_db = SellerWorkflowDB(db_path)
    for i in range(1, 4):
        wf = await wf_db.get_active_workflow_for_seller(f"M00{i}")
        assert wf["status"] == "WARN1_SENT"


# ---------------------------------------------------------------------------
# Полный цикл эскалации
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_escalation_cycle(db_path):
    """
    Полный цикл: NEW → WARN1 → WARN2 → LEGAL.
    Продавец не отвечает, не отсоединяется.
    """
    await _init_db(db_path)
    await _seed_seller(db_path)
    await _seed_product(db_path)
    await _seed_product_seller(db_path)

    wa_client = AsyncMock()
    wa_client.send_text = AsyncMock(return_value={"idMessage": "cycle_msg"})

    scanner = AsyncMock()
    scanner.check_seller_on_product = AsyncMock(return_value=True)

    engine = _make_engine(db_path, wa_client=wa_client, scanner=scanner)
    wf_db = SellerWorkflowDB(db_path)

    # Шаг 1: Новый продавец
    wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

    scheduler = EscalationScheduler(engine)

    # Шаг 2: process_new_sellers → WARN1
    await scheduler.process_new_sellers()
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "WARN1_SENT"

    # Шаг 3: 25 часов проходит → WARN2
    await _age_workflow(db_path, wf_id, 25)
    await scheduler.process_warn1_expiry()
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "WARN2_SENT"

    # Шаг 4: ещё 25 часов → LEGAL
    await _age_workflow(db_path, wf_id, 25)
    await scheduler.process_warn2_expiry()
    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "LEGAL_REQUEST_CREATED"

    # Проверить юрзаявку
    legal_db = LegalRequestsDB(db_path)
    request = await legal_db.get_request_by_workflow(wf_id)
    assert request is not None
