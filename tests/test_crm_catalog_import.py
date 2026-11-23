import sqlite3

import pytest

from database.migrations import DatabaseMigrations
from database.products import ProductsDB
from database.product_sellers import ProductSellersDB
from database.schema import DatabaseSchema
from tools.import_crm_catalog import import_catalog


@pytest.mark.asyncio
async def test_catalog_import_keeps_history_and_changes_only_monitoring(tmp_path):
    db = tmp_path / "bot.db"
    await DatabaseSchema.init_db(db)
    await DatabaseMigrations(db).run_migrations()
    products = ProductsDB(db)
    for sku in ("101", "102"):
        await products.add_product(sku, f"https://kaspi.kz/shop/p/old-{sku}/", f"Old {sku}")

    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO sellers (merchant_id,merchant_name) VALUES ('s1','Seller')")
        conn.execute("INSERT INTO product_sellers (product_id,seller_id,price) VALUES ('102','s1',100)")
        workflow = conn.execute("INSERT INTO seller_workflows (seller_id) VALUES ('s1')").lastrowid
        conn.execute("INSERT INTO workflow_products (workflow_id,product_id) VALUES (?,'102')", (workflow,))
        conn.execute("UPDATE products SET last_checked='2026-01-01' WHERE master_sku='101'")

    payload = {"schema_version": 1, "products": [
        {"sku": "101", "url": "https://kaspi.kz/shop/p/101/", "title": "New 101"},
        {"sku": "103", "url": "https://kaspi.kz/shop/p/103/", "title": "New 103"},
    ], "excluded": [{"crm_id": 99, "reason": "invalid_product_link"}]}
    preview = import_catalog(db, payload)
    assert preview["add"] == ["103"]
    assert preview["update"] == ["101"]
    assert preview["disable"] == ["102"]
    assert len(await products.get_all_products(monitored_only=True)) == 2
    assert (await products.get_product("101"))["title"] == "Old 101"

    with pytest.raises(ValueError, match="review"):
        import_catalog(db, payload, apply=True)

    incomplete = {**payload, "excluded": [{"crm_id": 99, "reason": "unresolved_short_link"}]}
    with pytest.raises(ValueError, match="unresolved short link"):
        import_catalog(db, incomplete, apply=True, reviewed_exclusions=1)

    with pytest.raises(ValueError, match="expected-disables"):
        import_catalog(db, payload, apply=True, reviewed_exclusions=1)
    assert import_catalog(db, payload, apply=True, reviewed_exclusions=1, expected_disables=1) == preview
    seller_products = ProductSellersDB(db)
    assert await seller_products.get_active_product_ids_for_seller("s1") == []
    assert await seller_products.get_other_products_for_seller("s1", "101") == []
    assert {p["master_sku"] for p in await products.get_all_products(monitored_only=True)} == {"101", "103"}
    assert (await products.get_product("101"))["last_checked"] == "2026-01-01"
    assert (await products.get_product("103"))["monitor_baseline_pending"] == 1
    await products.update_last_checked("103")
    assert not (await products.get_product("103"))["monitor_baseline_pending"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM product_sellers").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM workflow_products").fetchone()[0] == 1
    assert import_catalog(db, payload, apply=True, reviewed_exclusions=1)["disable"] == []


@pytest.mark.asyncio
async def test_first_imported_scan_records_sellers_without_notifications(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from config import Config
    from parser.scanner import ProductScanner

    db = tmp_path / "bot.db"
    await DatabaseSchema.init_db(db)
    await DatabaseMigrations(db).run_migrations()
    import_catalog(db, {"schema_version": 1, "products": [
        {"sku": "101", "url": "https://kaspi.kz/shop/p/101/", "title": "Product"},
    ]}, apply=True)
    monkeypatch.setattr(Config, "DB_PATH", db)
    scanner = ProductScanner(workflow_engine=object())
    scanner.proxy_manager = SimpleNamespace(
        increment_and_check_ip=AsyncMock(),
        delay_for_product=AsyncMock(),
        delay_for_merchant=AsyncMock(),
    )
    scanner._notify_workflow_engine = AsyncMock()
    offers = [{"merchant_id": "s1", "merchant_name": "New seller one", "price": 100.0}]
    scanner.parser = SimpleNamespace(
        get_product_offers=AsyncMock(return_value=(True, offers)),
        get_merchant_phone=AsyncMock(return_value=None),
        parse_offer=lambda offer: offer,
    )
    assert await scanner.scan_product(await scanner.products_db.get_product("101"))
    assert scanner.new_sellers == []
    scanner._notify_workflow_engine.assert_not_awaited()
    assert len(await scanner.product_sellers_db.get_sellers_for_product("101")) == 1

    offers.append({"merchant_id": "s2", "merchant_name": "New seller two", "price": 90.0})
    assert await scanner.scan_product(await scanner.products_db.get_product("101"))
    assert len(scanner.new_sellers) == 1
    scanner._notify_workflow_engine.assert_awaited_once_with("s2", "101", False)
