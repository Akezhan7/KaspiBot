"""
Тесты Фазы 1: новые таблицы и DAO-классы.
Запуск: pytest tests/test_phase1_db.py -v
"""
import asyncio
import pytest
import tempfile
from pathlib import Path

from database.schema import DatabaseSchema
from database.migrations import DatabaseMigrations
from database.sellers import SellersDB
from database.products import ProductsDB
from database.seller_workflow import SellerWorkflowDB
from database.message_log import MessageLogDB
from database.legal_requests import LegalRequestsDB


@pytest.fixture
def db_path(tmp_path):
    """Временная in-memory-подобная БД для каждого теста"""
    return tmp_path / "test.db"


async def _init_db(db_path: Path):
    """Инициализация схемы + миграции"""
    await DatabaseSchema.init_db(db_path)
    migrations = DatabaseMigrations(db_path)
    await migrations.run_migrations()


# === Миграции ===

@pytest.mark.asyncio
async def test_migrations_run(db_path):
    """Миграции применяются без ошибок"""
    await DatabaseSchema.init_db(db_path)
    migrations = DatabaseMigrations(db_path)
    applied = await migrations.run_migrations()
    assert applied > 0

    # Повторный запуск — 0 новых
    applied2 = await migrations.run_migrations()
    assert applied2 == 0


@pytest.mark.asyncio
async def test_migration_version(db_path):
    """Версия схемы корректно обновляется"""
    await _init_db(db_path)
    migrations = DatabaseMigrations(db_path)
    version = await migrations.get_current_version()
    assert version == 3  # 3 миграции в текущей версии


# === SellerWorkflowDB ===

@pytest.mark.asyncio
async def test_workflow_crud(db_path):
    """Создание, получение, обновление workflow"""
    await _init_db(db_path)

    # Создаём продавца (prerequisite)
    sellers = SellersDB(db_path)
    await sellers.add_seller("M001", "Test Shop", "+77011234567")

    wf_db = SellerWorkflowDB(db_path)

    # Создание
    wf_id = await wf_db.create_workflow("M001")
    assert isinstance(wf_id, int)

    # Получение
    wf = await wf_db.get_workflow(wf_id)
    assert wf is not None
    assert wf["seller_id"] == "M001"
    assert wf["status"] == "NEW_SELLER_ATTACH"

    # Обновление статуса
    ok = await wf_db.update_status(wf_id, "WARN1_SENT")
    assert ok is True

    wf = await wf_db.get_workflow(wf_id)
    assert wf["status"] == "WARN1_SENT"
    assert wf["warn1_sent_at"] is not None


@pytest.mark.asyncio
async def test_workflow_active_for_seller(db_path):
    """Получение активного workflow для продавца"""
    await _init_db(db_path)

    sellers = SellersDB(db_path)
    await sellers.add_seller("M002", "Shop 2")

    wf_db = SellerWorkflowDB(db_path)
    wf_id = await wf_db.create_workflow("M002")

    active = await wf_db.get_active_workflow_for_seller("M002")
    assert active is not None
    assert active["id"] == wf_id

    # Закрываем — больше нет активного
    await wf_db.update_status(wf_id, "CLOSED")
    active = await wf_db.get_active_workflow_for_seller("M002")
    assert active is None


@pytest.mark.asyncio
async def test_workflow_update_status_if(db_path):
    """Оптимистичная блокировка при смене статуса"""
    await _init_db(db_path)

    sellers = SellersDB(db_path)
    await sellers.add_seller("M003", "Shop 3")

    wf_db = SellerWorkflowDB(db_path)
    wf_id = await wf_db.create_workflow("M003")

    # Успешный переход
    ok = await wf_db.update_status_if(wf_id, "WARN1_SENT", "NEW_SELLER_ATTACH")
    assert ok is True

    # Повторный — не сработает, статус уже WARN1_SENT
    ok = await wf_db.update_status_if(wf_id, "WARN1_SENT", "NEW_SELLER_ATTACH")
    assert ok is False


@pytest.mark.asyncio
async def test_workflow_products(db_path):
    """Привязка товаров к workflow"""
    await _init_db(db_path)

    sellers = SellersDB(db_path)
    await sellers.add_seller("M004", "Shop 4")

    products = ProductsDB(db_path)
    await products.add_product("SKU001", "https://kaspi.kz/product/SKU001", "Товар 1")
    await products.add_product("SKU002", "https://kaspi.kz/product/SKU002", "Товар 2")

    wf_db = SellerWorkflowDB(db_path)
    wf_id = await wf_db.create_workflow("M004")

    await wf_db.add_product_to_workflow(wf_id, "SKU001")
    await wf_db.add_product_to_workflow(wf_id, "SKU002")

    # Повторная привязка — без ошибки (INSERT OR IGNORE)
    await wf_db.add_product_to_workflow(wf_id, "SKU001")

    prods = await wf_db.get_workflow_products(wf_id)
    assert len(prods) == 2
    assert prods[0]["title"] in ("Товар 1", "Товар 2")

    # Обновление attached
    await wf_db.update_product_attached(wf_id, "SKU001", 0)
    prods = await wf_db.get_workflow_products(wf_id)
    detached = [p for p in prods if p["product_id"] == "SKU001"]
    assert detached[0]["still_attached"] == 0


@pytest.mark.asyncio
async def test_workflows_by_status(db_path):
    """Фильтрация workflow по статусу"""
    await _init_db(db_path)

    sellers = SellersDB(db_path)
    await sellers.add_seller("M005", "Shop 5")
    await sellers.add_seller("M006", "Shop 6")

    wf_db = SellerWorkflowDB(db_path)
    wf1 = await wf_db.create_workflow("M005")
    wf2 = await wf_db.create_workflow("M006")

    await wf_db.update_status(wf1, "WARN1_SENT")

    warn1_list = await wf_db.get_workflows_by_status("WARN1_SENT")
    assert len(warn1_list) == 1
    assert warn1_list[0]["id"] == wf1

    new_list = await wf_db.get_workflows_by_status("NEW_SELLER_ATTACH")
    assert len(new_list) == 1
    assert new_list[0]["id"] == wf2


# === MessageLogDB ===

@pytest.mark.asyncio
async def test_message_log_crud(db_path):
    """Запись и чтение сообщений"""
    await _init_db(db_path)

    sellers = SellersDB(db_path)
    await sellers.add_seller("M010", "MsgShop")

    wf_db = SellerWorkflowDB(db_path)
    wf_id = await wf_db.create_workflow("M010")

    log_db = MessageLogDB(db_path)

    # Исходящее
    msg_id = await log_db.log_message(
        workflow_id=wf_id,
        seller_id="M010",
        direction="OUT",
        text="Здравствуйте, просим отсоединиться",
        template_code="WARN1_SOFT_01",
    )
    assert isinstance(msg_id, int)

    # Входящее
    await log_db.log_message(
        workflow_id=wf_id,
        seller_id="M010",
        direction="IN",
        text="Я не знал, уже снимаю",
        classification="DIDNT_KNOW",
    )

    msgs = await log_db.get_messages_for_workflow(wf_id)
    assert len(msgs) == 2
    assert msgs[0]["direction"] == "OUT"
    assert msgs[1]["direction"] == "IN"


@pytest.mark.asyncio
async def test_message_log_last_outgoing(db_path):
    """Получение последнего исходящего"""
    await _init_db(db_path)

    sellers = SellersDB(db_path)
    await sellers.add_seller("M011", "LastOutShop")

    wf_db = SellerWorkflowDB(db_path)
    wf_id = await wf_db.create_workflow("M011")

    log_db = MessageLogDB(db_path)
    await log_db.log_message(wf_id, "M011", "OUT", "Первое предупреждение")
    await log_db.log_message(wf_id, "M011", "OUT", "Второе предупреждение")

    last = await log_db.get_last_outgoing(wf_id)
    assert last is not None
    assert last["message_text"] == "Второе предупреждение"


@pytest.mark.asyncio
async def test_message_count_today(db_path):
    """Антиспам: подсчёт сообщений за день"""
    await _init_db(db_path)

    sellers = SellersDB(db_path)
    await sellers.add_seller("M012", "SpamShop")

    wf_db = SellerWorkflowDB(db_path)
    wf_id = await wf_db.create_workflow("M012")

    log_db = MessageLogDB(db_path)
    await log_db.log_message(wf_id, "M012", "OUT", "msg1")
    await log_db.log_message(wf_id, "M012", "OUT", "msg2")
    await log_db.log_message(wf_id, "M012", "IN", "reply")

    out_count = await log_db.count_messages_today("M012", "OUT")
    assert out_count == 2

    in_count = await log_db.count_messages_today("M012", "IN")
    assert in_count == 1


@pytest.mark.asyncio
async def test_message_log_invalid_direction(db_path):
    """Ошибка при невалидном direction"""
    await _init_db(db_path)
    log_db = MessageLogDB(db_path)
    with pytest.raises(ValueError):
        await log_db.log_message(None, "M001", "INVALID", "text")


# === LegalRequestsDB ===

@pytest.mark.asyncio
async def test_legal_request_crud(db_path):
    """Создание и получение юрзаявки"""
    await _init_db(db_path)

    sellers = SellersDB(db_path)
    await sellers.add_seller("M020", "LegalShop", "+77015551234")

    wf_db = SellerWorkflowDB(db_path)
    wf_id = await wf_db.create_workflow("M020")

    lr_db = LegalRequestsDB(db_path)
    req_id = await lr_db.create_request(
        workflow_id=wf_id,
        seller_id="M020",
        shop_name="LegalShop",
        phone="+77015551234",
        product_links='["https://kaspi.kz/product/123"]',
    )
    assert isinstance(req_id, int)

    req = await lr_db.get_request(req_id)
    assert req is not None
    assert req["shop_name"] == "LegalShop"
    assert req["control_purchase_status"] == "PENDING"
    assert req["ready_for_lawsuit"] == 0


@pytest.mark.asyncio
async def test_legal_request_by_workflow(db_path):
    """Получение юрзаявки по workflow"""
    await _init_db(db_path)

    sellers = SellersDB(db_path)
    await sellers.add_seller("M021", "ByWfShop")

    wf_db = SellerWorkflowDB(db_path)
    wf_id = await wf_db.create_workflow("M021")

    lr_db = LegalRequestsDB(db_path)
    req_id = await lr_db.create_request(wf_id, "M021", shop_name="ByWfShop")

    by_wf = await lr_db.get_request_by_workflow(wf_id)
    assert by_wf is not None
    assert by_wf["id"] == req_id


@pytest.mark.asyncio
async def test_legal_purchase_flow(db_path):
    """Полный цикл: создание → назначение → заполнение → ready"""
    await _init_db(db_path)

    sellers = SellersDB(db_path)
    await sellers.add_seller("M022", "PurchaseShop")

    wf_db = SellerWorkflowDB(db_path)
    wf_id = await wf_db.create_workflow("M022")

    lr_db = LegalRequestsDB(db_path)
    req_id = await lr_db.create_request(wf_id, "M022", shop_name="PurchaseShop")

    # Назначить
    await lr_db.assign_purchase(req_id, "@admin_user")
    req = await lr_db.get_request(req_id)
    assert req["control_purchase_status"] == "ASSIGNED"
    assert req["assigned_to"] == "@admin_user"

    # Заполнить данные закупки
    await lr_db.update_purchase_info(
        req_id,
        bin_iin="123456789012",
        order_number="KZ-2026-001",
        notes="Закупка прошла успешно",
    )
    req = await lr_db.get_request(req_id)
    assert req["control_purchase_status"] == "COMPLETED"
    assert req["bin_iin"] == "123456789012"

    # Пометить ready
    await lr_db.mark_ready_for_lawsuit(req_id)
    req = await lr_db.get_request(req_id)
    assert req["ready_for_lawsuit"] == 1


@pytest.mark.asyncio
async def test_legal_pending_purchases(db_path):
    """Получение ожидающих закупок"""
    await _init_db(db_path)

    sellers = SellersDB(db_path)
    await sellers.add_seller("M023", "PendingShop")

    wf_db = SellerWorkflowDB(db_path)
    wf_id = await wf_db.create_workflow("M023")

    lr_db = LegalRequestsDB(db_path)
    await lr_db.create_request(wf_id, "M023", shop_name="PendingShop")

    pending = await lr_db.get_pending_purchases()
    assert len(pending) == 1
    assert pending[0]["merchant_name"] == "PendingShop"


@pytest.mark.asyncio
async def test_legal_all_requests_pagination(db_path):
    """Пагинация юрзаявок"""
    await _init_db(db_path)

    sellers = SellersDB(db_path)
    await sellers.add_seller("M024", "PageShop")

    wf_db = SellerWorkflowDB(db_path)
    lr_db = LegalRequestsDB(db_path)

    for i in range(5):
        wf_id = await wf_db.create_workflow("M024")
        await lr_db.create_request(wf_id, "M024", shop_name=f"PageShop-{i}")

    total = await lr_db.count_requests()
    assert total == 5

    page1 = await lr_db.get_all_requests(limit=2, offset=0)
    assert len(page1) == 2

    page2 = await lr_db.get_all_requests(limit=2, offset=2)
    assert len(page2) == 2
