"""
Тесты Фазы 13: REST API для Telegram Mini App.

Запуск: pytest tests/test_phase13_api.py -v

Покрывает:
- auth_middleware: отсутствие заголовка, неверная подпись, истёкший auth_date,
                   не-администратор, корректный администратор, OPTIONS preflight, /health
- API маршруты (пустая БД): структура ответов, коды статусов, пагинация
- API маршруты (наполненная БД): корректность вычислений ROI/ROAS
- analytics/processor: ROI, ROAS, CPC efficiency, wasted_budget, top_performers
- analytics/aggregator: daily/weekly/monthly summaries, trends, total_stats
"""
import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import aiohttp

from database.schema import DatabaseSchema
from database.migrations import DatabaseMigrations
from database.ads_data import AdsDataDB, ScrapeLogsDB
from database.products import ProductsDB
from database.product_sellers import ProductSellersDB
from analytics import AdsAnalyticsProcessor, DataAggregator
from api.server import TMAApiServer
from api.auth_middleware import create_auth_middleware


# ---------------------------------------------------------------------------
# Константы для тестов
# ---------------------------------------------------------------------------

TEST_BOT_TOKEN = "1234567890:AAFakeTokenForTestingPurposesOnly1234"
ADMIN_USER_ID = 777_000_001
NON_ADMIN_USER_ID = 999_999_999

# Свободный порт для тестового сервера (инкрементируется через фикстуру)
_PORT_COUNTER = [19100]


def _next_port() -> int:
    _PORT_COUNTER[0] += 1
    return _PORT_COUNTER[0]


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def make_init_data(
    bot_token: str,
    user_id: int,
    first_name: str = "TestUser",
    auth_offset_sec: int = 0,
) -> str:
    """
    Генерирует корректный Telegram WebApp initData для тестирования.

    auth_offset_sec < 0 — auth_date в прошлом (устаревшие данные).
    """
    auth_date = int(time.time()) + auth_offset_sec
    user_json = json.dumps({"id": user_id, "first_name": first_name}, separators=(",", ":"))

    params = {
        "auth_date": str(auth_date),
        "user": user_json,
    }

    data_check_string = "\n".join(
        f"{k}={v}" for k, v in sorted(params.items())
    )
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode(),
        digestmod=hashlib.sha256,
    )
    hash_value = hmac.new(
        key=secret_key.digest(),
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

    params["hash"] = hash_value
    # URL-encode вручную (aiohttp не имеет urlencode в тесте)
    from urllib.parse import urlencode
    return urlencode(params)


def admin_headers(auth_offset_sec: int = 0) -> dict:
    """Заголовки с валидным initData от пользователя-администратора."""
    init_data = make_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID, auth_offset_sec=auth_offset_sec)
    return {"Authorization": f"tma {init_data}"}


def non_admin_headers() -> dict:
    """Заголовки с валидным initData от не-администратора."""
    init_data = make_init_data(TEST_BOT_TOKEN, NON_ADMIN_USER_ID)
    return {"Authorization": f"tma {init_data}"}


async def _init_db(db_path: Path) -> None:
    await DatabaseSchema.init_db(db_path)
    migrations = DatabaseMigrations(db_path)
    await migrations.run_migrations()


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_api.db"


@pytest.fixture
async def initialized_db(db_path):
    """Инициализированная БД без данных."""
    await _init_db(db_path)
    return db_path


async def _make_server(db_path: Path, port: int, scrape_trigger=None) -> TMAApiServer:
    """Создать и запустить тестовый TMAApiServer."""
    ads_db = AdsDataDB(str(db_path))
    products_db = ProductsDB(str(db_path))
    ps_db = ProductSellersDB(str(db_path))
    scrape_logs_db = ScrapeLogsDB(str(db_path))

    processor = AdsAnalyticsProcessor(ads_db, products_db, ps_db)
    aggregator = DataAggregator(ads_db, products_db)

    server = TMAApiServer(
        processor=processor,
        aggregator=aggregator,
        ads_db=ads_db,
        products_db=products_db,
        scrape_logs_db=scrape_logs_db,
        bot_token=TEST_BOT_TOKEN,
        admin_user_ids={ADMIN_USER_ID},
        host="127.0.0.1",
        port=port,
        scrape_trigger=scrape_trigger,
    )
    await server.start()
    return server


# ===========================================================================
# Блок 1: Auth middleware
# ===========================================================================


@pytest.mark.asyncio
async def test_auth_no_header(initialized_db):
    """Запрос без Authorization → 401."""
    port = _next_port()
    server = await _make_server(initialized_db, port)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/api/dashboard") as r:
                assert r.status == 401
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_auth_wrong_scheme(initialized_db):
    """Authorization: Bearer token вместо tma → 401."""
    port = _next_port()
    server = await _make_server(initialized_db, port)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{port}/api/dashboard",
                headers={"Authorization": "Bearer some_token"},
            ) as r:
                assert r.status == 401
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_auth_invalid_signature(initialized_db):
    """Неверная подпись initData → 401."""
    port = _next_port()
    server = await _make_server(initialized_db, port)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{port}/api/dashboard",
                headers={"Authorization": "tma auth_date=12345&user=%7B%7D&hash=deadbeef"},
            ) as r:
                assert r.status == 401
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_auth_expired_init_data(initialized_db):
    """initData старше 1 часа → 401."""
    port = _next_port()
    server = await _make_server(initialized_db, port)
    try:
        # auth_date на 2 часа в прошлом
        headers = admin_headers(auth_offset_sec=-7300)
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{port}/api/dashboard",
                headers=headers,
            ) as r:
                assert r.status == 401
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_auth_non_admin(initialized_db):
    """Валидный initData от не-администратора → 403."""
    port = _next_port()
    server = await _make_server(initialized_db, port)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{port}/api/dashboard",
                headers=non_admin_headers(),
            ) as r:
                assert r.status == 403
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_auth_valid_admin(initialized_db):
    """Валидный initData от администратора → 200."""
    port = _next_port()
    server = await _make_server(initialized_db, port)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{port}/api/dashboard",
                headers=admin_headers(),
            ) as r:
                assert r.status == 200
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_auth_health_no_auth_required(initialized_db):
    """GET /health не требует авторизации → 200."""
    port = _next_port()
    server = await _make_server(initialized_db, port)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/health") as r:
                assert r.status == 200
                data = await r.json()
                assert data["status"] == "ok"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_auth_options_preflight_passthrough(initialized_db):
    """OPTIONS preflight не требует авторизации."""
    port = _next_port()
    server = await _make_server(initialized_db, port)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.options(
                f"http://127.0.0.1:{port}/api/dashboard",
                headers={"Origin": "http://localhost:5173"},
            ) as r:
                # aiohttp вернёт 405 Method Not Allowed (нет OPTIONS handler),
                # но точно не 401 — авторизация пройдена
                assert r.status != 401
    finally:
        await server.stop()


# ===========================================================================
# Блок 2: API маршруты — структура ответов (пустая БД)
# ===========================================================================


@pytest.fixture
async def api_server(initialized_db):
    """Тестовый сервер с пустой БД. Возвращает (server, port, headers)."""
    port = _next_port()
    server = await _make_server(initialized_db, port)
    yield server, port, admin_headers()
    await server.stop()


@pytest.mark.asyncio
async def test_dashboard_structure(api_server):
    """GET /api/dashboard возвращает корректную структуру."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{port}/api/dashboard", headers=headers) as r:
            assert r.status == 200
            data = await r.json()
            assert "total_stats" in data
            assert "today" in data
            assert "alerts" in data
            assert isinstance(data["alerts"], list)


@pytest.mark.asyncio
async def test_products_list_structure(api_server):
    """GET /api/products возвращает пагинированный список."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{port}/api/products", headers=headers) as r:
            assert r.status == 200
            data = await r.json()
            assert "total" in data
            assert "limit" in data
            assert "offset" in data
            assert "items" in data
            assert isinstance(data["items"], list)
            assert data["total"] == 0


@pytest.mark.asyncio
async def test_products_list_pagination_params(api_server):
    """GET /api/products?limit=5&offset=10 — параметры отражаются в ответе."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"http://127.0.0.1:{port}/api/products?limit=5&offset=10",
            headers=headers,
        ) as r:
            assert r.status == 200
            data = await r.json()
            assert data["limit"] == 5
            assert data["offset"] == 10


@pytest.mark.asyncio
async def test_product_detail_not_found(api_server):
    """GET /api/products/NONEXISTENT → 404."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"http://127.0.0.1:{port}/api/products/SKU_NONEXISTENT",
            headers=headers,
        ) as r:
            assert r.status == 404


@pytest.mark.asyncio
async def test_ads_top_spenders_structure(api_server):
    """GET /api/ads/top-spenders возвращает {items, count}."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{port}/api/ads/top-spenders", headers=headers) as r:
            assert r.status == 200
            data = await r.json()
            assert "items" in data
            assert "count" in data
            assert data["count"] == 0


@pytest.mark.asyncio
async def test_ads_top_performers_structure(api_server):
    """GET /api/ads/top-performers возвращает {items, count}."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{port}/api/ads/top-performers", headers=headers) as r:
            assert r.status == 200
            data = await r.json()
            assert "items" in data


@pytest.mark.asyncio
async def test_ads_no_bonus_structure(api_server):
    """GET /api/ads/no-bonus возвращает {items, count}."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{port}/api/ads/no-bonus", headers=headers) as r:
            assert r.status == 200
            data = await r.json()
            assert "items" in data
            assert "count" in data


@pytest.mark.asyncio
async def test_ads_most_clickable_structure(api_server):
    """GET /api/ads/most-clickable возвращает {items, count}."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{port}/api/ads/most-clickable", headers=headers) as r:
            assert r.status == 200
            data = await r.json()
            assert "items" in data


@pytest.mark.asyncio
async def test_ads_wasted_budget_structure(api_server):
    """GET /api/ads/wasted-budget возвращает {items, count, threshold}."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{port}/api/ads/wasted-budget", headers=headers) as r:
            assert r.status == 200
            data = await r.json()
            assert "items" in data
            assert "count" in data
            assert "threshold" in data


@pytest.mark.asyncio
async def test_ads_trends_structure(api_server):
    """GET /api/ads/trends/{sku} возвращает {sku, days, trends}."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"http://127.0.0.1:{port}/api/ads/trends/TEST_SKU?days=7",
            headers=headers,
        ) as r:
            assert r.status == 200
            data = await r.json()
            assert data["sku"] == "TEST_SKU"
            assert data["days"] == 7
            assert "trends" in data
            assert isinstance(data["trends"], list)


@pytest.mark.asyncio
async def test_summary_daily(api_server):
    """GET /api/summary/daily возвращает period='daily'."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{port}/api/summary/daily", headers=headers) as r:
            assert r.status == 200
            data = await r.json()
            assert data.get("period") == "daily"


@pytest.mark.asyncio
async def test_summary_weekly(api_server):
    """GET /api/summary/weekly возвращает period='weekly'."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{port}/api/summary/weekly", headers=headers) as r:
            assert r.status == 200
            data = await r.json()
            assert data.get("period") == "weekly"


@pytest.mark.asyncio
async def test_summary_monthly(api_server):
    """GET /api/summary/monthly возвращает period='monthly'."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{port}/api/summary/monthly", headers=headers) as r:
            assert r.status == 200
            data = await r.json()
            assert data.get("period") == "monthly"


@pytest.mark.asyncio
async def test_scrape_status_never_run(api_server):
    """GET /api/scrape/status без логов → never_run."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.get(f"http://127.0.0.1:{port}/api/scrape/status", headers=headers) as r:
            assert r.status == 200
            data = await r.json()
            assert data["status"] == "never_run"


@pytest.mark.asyncio
async def test_scrape_trigger_no_scraper(api_server):
    """POST /api/scrape/trigger без настроенного scraper → 503."""
    server, port, headers = api_server
    async with aiohttp.ClientSession() as s:
        async with s.post(
            f"http://127.0.0.1:{port}/api/scrape/trigger",
            headers=headers,
        ) as r:
            assert r.status == 503


@pytest.mark.asyncio
async def test_scrape_trigger_with_scraper(initialized_db):
    """POST /api/scrape/trigger с настроенным scraper → 200, {status: triggered}."""
    port = _next_port()
    trigger_called = []

    async def fake_trigger():
        trigger_called.append(True)

    server = await _make_server(initialized_db, port, scrape_trigger=fake_trigger)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(
                f"http://127.0.0.1:{port}/api/scrape/trigger",
                headers=admin_headers(),
            ) as r:
                assert r.status == 200
                data = await r.json()
                assert data["status"] == "triggered"
    finally:
        await server.stop()


# ===========================================================================
# Блок 3: API маршруты — наполненная БД
# ===========================================================================


async def _seed_ads_data(db_path: Path) -> None:
    """Заполнить тестовыми рекламными данными."""
    from config import now_kz_str

    ads_db = AdsDataDB(str(db_path))
    products_db = ProductsDB(str(db_path))
    ps_db = ProductSellersDB(str(db_path))

    # Добавить 2 товара
    await products_db.add_product("SKU001", "https://kaspi.kz/p/sku001", "Шуруповерт тестовый")
    await products_db.add_product("SKU002", "https://kaspi.kz/p/sku002", "Набор инструментов тестовый")

    scraped_at = now_kz_str()

    # SKU001 — прибыльная реклама (ROI > 0)
    await ads_db.save_campaign({
        "product_sku": "SKU001",
        "scraped_at": scraped_at,
        "source": "kaspi_marketing",
        "impressions": 10000,
        "clicks": 500,
        "ctr": 5.0,
        "spend": 1000.0,
        "cpc": 2.0,
        "revenue": 5000.0,
        "bonus_active": 1,
        "bonus_percent": 5.0,
    })

    await ads_db.save_campaign({
        "product_sku": "SKU001",
        "scraped_at": scraped_at,
        "source": "kaspi_bonus",
        "impressions": 0,
        "clicks": 0,
        "ctr": 0.0,
        "spend": 0.0,
        "cpc": 0.0,
        "revenue": 0.0,
        "bonus_active": 1,
        "bonus_percent": 5.0,
    })

    # SKU002 — убыточная реклама (ROI < 0)
    await ads_db.save_campaign({
        "product_sku": "SKU002",
        "scraped_at": scraped_at,
        "source": "kaspi_marketing",
        "impressions": 2000,
        "clicks": 50,
        "ctr": 2.5,
        "spend": 3000.0,
        "cpc": 60.0,
        "revenue": 1000.0,
        "bonus_active": 0,
        "bonus_percent": 0.0,
    })

    await ads_db.save_campaign({
        "product_sku": "SKU002",
        "scraped_at": scraped_at,
        "source": "kaspi_bonus",
        "impressions": 0,
        "clicks": 0,
        "ctr": 0.0,
        "spend": 0.0,
        "cpc": 0.0,
        "revenue": 0.0,
        "bonus_active": 0,
        "bonus_percent": 0.0,
    })


@pytest.fixture
async def seeded_db(tmp_path):
    """БД с тестовыми данными."""
    db_path = tmp_path / "seeded.db"
    await _init_db(db_path)
    await _seed_ads_data(db_path)
    return db_path


@pytest.mark.asyncio
async def test_products_list_with_data(seeded_db):
    """GET /api/products с данными возвращает товары."""
    port = _next_port()
    server = await _make_server(seeded_db, port)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{port}/api/products",
                headers=admin_headers(),
            ) as r:
                assert r.status == 200
                data = await r.json()
                assert data["total"] == 2
                assert len(data["items"]) == 2
                # Первый элемент — SKU002 (больше затрат) при sort=spend_desc
                skus = [item["sku"] for item in data["items"]]
                assert "SKU001" in skus
                assert "SKU002" in skus
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_product_detail_found(seeded_db):
    """GET /api/products/SKU001 → 200 с полным набором полей."""
    port = _next_port()
    server = await _make_server(seeded_db, port)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{port}/api/products/SKU001",
                headers=admin_headers(),
            ) as r:
                assert r.status == 200
                data = await r.json()
                assert data["sku"] == "SKU001"
                assert data["title"] == "Шуруповерт тестовый"
                assert "roi" in data
                assert "roas" in data
                assert "cpc_efficiency" in data
                assert "trends" in data
                assert "spend" in data["roi"]
                assert "revenue" in data["roi"]
                assert "total_spend" not in data["roi"]
                if data["trends"]:
                    assert "ctr" in data["trends"][0]
                    assert "cpc" in data["trends"][0]
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_wasted_budget_contains_sku002(seeded_db):
    """GET /api/ads/wasted-budget — SKU002 (ROI < 0) присутствует в ответе."""
    port = _next_port()
    server = await _make_server(seeded_db, port)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{port}/api/ads/wasted-budget",
                headers=admin_headers(),
            ) as r:
                assert r.status == 200
                data = await r.json()
                skus = [item["sku"] for item in data["items"]]
                assert "SKU002" in skus
                assert "SKU001" not in skus  # SKU001 прибылен
                sku002 = next(item for item in data["items"] if item["sku"] == "SKU002")
                assert sku002["title"] == "Набор инструментов тестовый"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_top_spenders_first_is_sku002(seeded_db):
    """GET /api/ads/top-spenders — SKU002 первый (затраты 3000 > 1000)."""
    port = _next_port()
    server = await _make_server(seeded_db, port)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{port}/api/ads/top-spenders",
                headers=admin_headers(),
            ) as r:
                assert r.status == 200
                data = await r.json()
                assert data["count"] == 2
                assert data["items"][0]["sku"] == "SKU002"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_no_bonus_contains_sku002(seeded_db):
    """GET /api/ads/no-bonus — SKU002 (bonus_active=0) в списке."""
    port = _next_port()
    server = await _make_server(seeded_db, port)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{port}/api/ads/no-bonus",
                headers=admin_headers(),
            ) as r:
                assert r.status == 200
                data = await r.json()
                skus = [item["sku"] for item in data["items"]]
                assert "SKU002" in skus
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_products_filters_missing_bonus_seller(seeded_db):
    """GET /api/products поддерживает актуальный фильтр missing=bonus_seller."""
    port = _next_port()
    server = await _make_server(seeded_db, port)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{port}/api/products?missing=bonus_seller",
                headers=admin_headers(),
            ) as r:
                assert r.status == 200
                data = await r.json()
                assert data["total"] == 1
                assert data["items"][0]["sku"] == "SKU002"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_scrape_status_after_log(seeded_db):
    """GET /api/scrape/status после создания лога отдаёт корректный статус."""
    # Создать лог напрямую через DAO
    sl_db = ScrapeLogsDB(str(seeded_db))
    log_id = await sl_db.create_log()
    await sl_db.update_log(log_id, status="completed", products_scraped=2)

    port = _next_port()
    server = await _make_server(seeded_db, port)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{port}/api/scrape/status",
                headers=admin_headers(),
            ) as r:
                assert r.status == 200
                data = await r.json()
                assert data["status"] == "completed"
                assert data["log"]["products_scraped"] == 2
    finally:
        await server.stop()


# ===========================================================================
# Блок 4: AdsAnalyticsProcessor unit-тесты
# ===========================================================================


@pytest.mark.asyncio
async def test_processor_roi_no_data(initialized_db):
    """calculate_roi без данных возвращает нулевые значения."""
    ads_db = AdsDataDB(str(initialized_db))
    products_db = ProductsDB(str(initialized_db))
    ps_db = ProductSellersDB(str(initialized_db))
    processor = AdsAnalyticsProcessor(ads_db, products_db, ps_db)

    result = await processor.calculate_roi("SKU_MISSING")
    assert result["spend"] == 0.0
    assert result["revenue"] == 0.0
    assert result["roi_percent"] is None
    assert result["has_revenue_data"] is False


@pytest.mark.asyncio
async def test_processor_roi_with_data(seeded_db):
    """calculate_roi для SKU001 (spend=1000, revenue=5000) → ROI=400%."""
    ads_db = AdsDataDB(str(seeded_db))
    products_db = ProductsDB(str(seeded_db))
    ps_db = ProductSellersDB(str(seeded_db))
    processor = AdsAnalyticsProcessor(ads_db, products_db, ps_db)

    result = await processor.calculate_roi("SKU001")
    assert result["spend"] == 1000.0
    assert result["revenue"] == 5000.0
    assert result["roi_percent"] == pytest.approx(400.0, abs=0.01)
    assert result["has_revenue_data"] is True


@pytest.mark.asyncio
async def test_processor_roas_with_data(seeded_db):
    """calculate_roas для SKU001 → ROAS=5.0."""
    ads_db = AdsDataDB(str(seeded_db))
    products_db = ProductsDB(str(seeded_db))
    ps_db = ProductSellersDB(str(seeded_db))
    processor = AdsAnalyticsProcessor(ads_db, products_db, ps_db)

    roas = await processor.calculate_roas("SKU001")
    assert roas == pytest.approx(5.0, abs=0.001)


@pytest.mark.asyncio
async def test_processor_roas_no_data(initialized_db):
    """calculate_roas без данных → None."""
    ads_db = AdsDataDB(str(initialized_db))
    products_db = ProductsDB(str(initialized_db))
    ps_db = ProductSellersDB(str(initialized_db))
    processor = AdsAnalyticsProcessor(ads_db, products_db, ps_db)

    roas = await processor.calculate_roas("SKU_MISSING")
    assert roas is None


@pytest.mark.asyncio
async def test_processor_wasted_budget(seeded_db):
    """get_wasted_budget — только SKU002 (ROI < 0)."""
    ads_db = AdsDataDB(str(seeded_db))
    products_db = ProductsDB(str(seeded_db))
    ps_db = ProductSellersDB(str(seeded_db))
    processor = AdsAnalyticsProcessor(ads_db, products_db, ps_db)

    wasted = await processor.get_wasted_budget(threshold_roi=0.0)
    assert len(wasted) == 1
    assert wasted[0]["sku"] == "SKU002"
    assert wasted[0]["roi_percent"] < 0


@pytest.mark.asyncio
async def test_processor_top_performers(seeded_db):
    """get_top_performers — только SKU001 (revenue > 0)."""
    ads_db = AdsDataDB(str(seeded_db))
    products_db = ProductsDB(str(seeded_db))
    ps_db = ProductSellersDB(str(seeded_db))
    processor = AdsAnalyticsProcessor(ads_db, products_db, ps_db)

    performers = await processor.get_top_performers(limit=10)
    # SKU001 и SKU002 имеют revenue > 0, SKU001 с лучшим ROAS первый
    assert performers[0]["sku"] == "SKU001"
    assert performers[0]["roas"] == pytest.approx(5.0, abs=0.01)


@pytest.mark.asyncio
async def test_processor_no_bonus_products(seeded_db):
    """get_no_bonus_products — только SKU002 (bonus_active=0)."""
    ads_db = AdsDataDB(str(seeded_db))
    products_db = ProductsDB(str(seeded_db))
    ps_db = ProductSellersDB(str(seeded_db))
    processor = AdsAnalyticsProcessor(ads_db, products_db, ps_db)

    no_bonus = await processor.get_no_bonus_products()
    skus = [item["sku"] for item in no_bonus]
    assert "SKU002" in skus
    assert "SKU001" not in skus


@pytest.mark.asyncio
async def test_processor_most_clickable(seeded_db):
    """get_most_clickable — SKU001 первый (CTR=5% > 2.5%)."""
    ads_db = AdsDataDB(str(seeded_db))
    products_db = ProductsDB(str(seeded_db))
    ps_db = ProductSellersDB(str(seeded_db))
    processor = AdsAnalyticsProcessor(ads_db, products_db, ps_db)

    clickable = await processor.get_most_clickable(limit=10)
    assert len(clickable) == 2
    assert clickable[0]["sku"] == "SKU001"
    assert clickable[0]["avg_ctr"] >= clickable[1]["avg_ctr"]


@pytest.mark.asyncio
async def test_processor_cpc_efficiency_no_sellers(seeded_db):
    """get_cpc_efficiency без продавцов — assessment='no_data'."""
    ads_db = AdsDataDB(str(seeded_db))
    products_db = ProductsDB(str(seeded_db))
    ps_db = ProductSellersDB(str(seeded_db))
    processor = AdsAnalyticsProcessor(ads_db, products_db, ps_db)

    result = await processor.get_cpc_efficiency("SKU001")
    assert result["sku"] == "SKU001"
    # Нет продавцов → avg_product_price=0 → no_data
    assert result["assessment"] == "no_data"
    assert result["avg_cpc"] == pytest.approx(2.0, abs=0.01)


# ===========================================================================
# Блок 5: DataAggregator unit-тесты
# ===========================================================================


@pytest.mark.asyncio
async def test_aggregator_daily_empty(initialized_db):
    """aggregate_daily на пустой БД → нулевые значения."""
    ads_db = AdsDataDB(str(initialized_db))
    products_db = ProductsDB(str(initialized_db))
    aggregator = DataAggregator(ads_db, products_db)

    result = await aggregator.aggregate_daily()
    assert result["period"] == "daily"
    assert result["total_spend"] == 0.0
    assert result["total_clicks"] == 0


@pytest.mark.asyncio
async def test_aggregator_weekly_empty(initialized_db):
    """aggregate_weekly на пустой БД → нулевые значения."""
    ads_db = AdsDataDB(str(initialized_db))
    products_db = ProductsDB(str(initialized_db))
    aggregator = DataAggregator(ads_db, products_db)

    result = await aggregator.aggregate_weekly()
    assert result["period"] == "weekly"
    assert result["total_spend"] == 0.0


@pytest.mark.asyncio
async def test_aggregator_monthly_empty(initialized_db):
    """aggregate_monthly на пустой БД → нулевые значения."""
    ads_db = AdsDataDB(str(initialized_db))
    products_db = ProductsDB(str(initialized_db))
    aggregator = DataAggregator(ads_db, products_db)

    result = await aggregator.aggregate_monthly()
    assert result["period"] == "monthly"
    assert result["total_spend"] == 0.0


@pytest.mark.asyncio
async def test_aggregator_total_stats_with_data(seeded_db):
    """get_total_stats с данными — products_with_ads=2, products_without_bonuses=1."""
    ads_db = AdsDataDB(str(seeded_db))
    products_db = ProductsDB(str(seeded_db))
    aggregator = DataAggregator(ads_db, products_db)

    stats = await aggregator.get_total_stats()
    assert stats["period_days"] == 30
    assert stats["products_with_ads"] == 2
    assert stats["products_without_bonuses"] == 1    # только SKU002
    assert stats["total_spend"] == pytest.approx(4000.0, abs=0.01)
    assert stats["total_revenue"] == pytest.approx(6000.0, abs=0.01)


@pytest.mark.asyncio
async def test_aggregator_trends_empty(initialized_db):
    """get_trends для несуществующего SKU → пустой список."""
    ads_db = AdsDataDB(str(initialized_db))
    products_db = ProductsDB(str(initialized_db))
    aggregator = DataAggregator(ads_db, products_db)

    trends = await aggregator.get_trends("SKU_MISSING", days=7)
    assert trends == []


@pytest.mark.asyncio
async def test_aggregator_trends_with_data(seeded_db):
    """get_trends для SKU001 возвращает список с полями day/spend/clicks."""
    ads_db = AdsDataDB(str(seeded_db))
    products_db = ProductsDB(str(seeded_db))
    aggregator = DataAggregator(ads_db, products_db)

    trends = await aggregator.get_trends("SKU001", days=30)
    assert len(trends) > 0
    # Хотя бы один день с ненулевыми данными
    non_zero = [t for t in trends if t["spend"] > 0]
    assert len(non_zero) >= 1
    # Проверяем структуру точки
    point = non_zero[0]
    assert "day" in point
    assert "spend" in point
    assert "clicks" in point
    assert "impressions" in point
    assert "ctr" in point
    assert "cpc" in point
