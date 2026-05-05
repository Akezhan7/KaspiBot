"""
Тесты Фазы 14: TMA Frontend + Фаза 15.1 Static Serving.

Запуск: pytest tests/test_phase14_tma.py -v

Покрывает:
- Сборка TMA: dist/ содержит index.html и assets/
- auth_middleware: /tma/* маршруты публичны (не требуют авторизации)
- static serving: /tma/ → index.html, /tma/assets/* → файлы
- SPA fallback: /tma/products, /tma/wasted-budget и другие → index.html
- Обслуживание при отсутствии dist/ → 503
- API эндпоинты (deps для TMA): все маршруты возвращают ожидаемую структуру
- Config: TMA_DIST_PATH настроен корректно
- Routes: _register_tma_static корректно регистрирует маршруты
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
from api.routes import _register_tma_static
from config import Config

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

TEST_BOT_TOKEN = "1234567890:AAFakeTokenForTMA14Testing00000000000"
ADMIN_USER_ID = 777_000_014

_PORT_COUNTER = [19400]


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
    auth_date = int(time.time()) + auth_offset_sec
    user_json = json.dumps({"id": user_id, "first_name": first_name}, separators=(",", ":"))
    params = {
        "auth_date": str(auth_date),
        "user": user_json,
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
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
    from urllib.parse import urlencode
    return urlencode(params)


def admin_headers() -> dict:
    init_data = make_init_data(TEST_BOT_TOKEN, ADMIN_USER_ID)
    return {"Authorization": f"tma {init_data}"}


async def _init_db(db_path: Path) -> None:
    await DatabaseSchema.init_db(db_path)
    migrations = DatabaseMigrations(db_path)
    await migrations.run_migrations()


async def _make_server(
    db_path: Path,
    port: int,
    tma_dist_path: Path | None = None,
) -> TMAApiServer:
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
        tma_dist_path=tma_dist_path,
    )
    await server.start()
    return server


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test_tma14.db"


@pytest.fixture
async def initialized_db(db_path):
    await _init_db(db_path)
    return db_path


@pytest.fixture
def fake_dist(tmp_path) -> Path:
    """Создаёт фиктивную dist/ структуру как после npm run build."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><html><head><title>TMA Test</title></head>"
        "<body><div id='root'></div></body></html>",
        encoding="utf-8",
    )
    assets = dist / "assets"
    assets.mkdir()
    (assets / "index-test.js").write_text("// vendor bundle", encoding="utf-8")
    (assets / "index-test.css").write_text("body { margin: 0; }", encoding="utf-8")
    return dist


# ===========================================================================
# Блок 1: TMA dist/ артефакты
# ===========================================================================


class TestTmaDistArtifacts:
    """Проверяем, что npm run build создал корректную структуру."""

    def test_dist_directory_exists(self):
        """tma/dist/ существует после сборки."""
        assert Config.TMA_DIST_PATH.is_dir(), (
            "tma/dist/ не существует. Выполните: cd tma && npm run build"
        )

    def test_dist_index_html_exists(self):
        """tma/dist/index.html существует."""
        index = Config.TMA_DIST_PATH / "index.html"
        assert index.exists(), "tma/dist/index.html не найден"

    def test_dist_index_html_has_root_div(self):
        """index.html содержит div#root для React."""
        content = (Config.TMA_DIST_PATH / "index.html").read_text(encoding="utf-8")
        assert "id=\"root\"" in content or "id='root'" in content, (
            "index.html не содержит div#root"
        )

    def test_dist_index_html_has_telegram_sdk(self):
        """index.html содержит ссылку на telegram-web-app.js."""
        content = (Config.TMA_DIST_PATH / "index.html").read_text(encoding="utf-8")
        assert "telegram-web-app.js" in content, (
            "index.html не подключает Telegram WebApp SDK"
        )

    def test_dist_assets_directory_exists(self):
        """tma/dist/assets/ существует и содержит JS-чанки."""
        assets = Config.TMA_DIST_PATH / "assets"
        assert assets.is_dir(), "tma/dist/assets/ не найден"
        js_files = list(assets.glob("*.js"))
        assert len(js_files) > 0, "В assets/ нет JS-файлов"

    def test_dist_has_css_assets(self):
        """В assets/ есть CSS файл."""
        assets = Config.TMA_DIST_PATH / "assets"
        css_files = list(assets.glob("*.css"))
        assert len(css_files) > 0, "В assets/ нет CSS-файлов"

    def test_dist_has_vendor_chunk(self):
        """В assets/ есть vendor чанк (React, react-router-dom)."""
        assets = Config.TMA_DIST_PATH / "assets"
        vendor_files = list(assets.glob("vendor-*.js"))
        assert len(vendor_files) > 0, "Vendor chunk не найден в assets/"

    def test_dist_has_charts_chunk(self):
        """В assets/ есть charts чанк (recharts)."""
        assets = Config.TMA_DIST_PATH / "assets"
        charts_files = list(assets.glob("charts-*.js"))
        assert len(charts_files) > 0, "Charts chunk не найден в assets/"


# ===========================================================================
# Блок 2: Config — TMA_DIST_PATH
# ===========================================================================


class TestConfig:
    """Проверяем конфигурацию TMA."""

    def test_tma_dist_path_is_configured(self):
        """Config.TMA_DIST_PATH указан."""
        assert Config.TMA_DIST_PATH is not None

    def test_tma_dist_path_points_to_tma_dist(self):
        """Config.TMA_DIST_PATH ведёт к tma/dist."""
        assert "tma" in str(Config.TMA_DIST_PATH)
        assert "dist" in str(Config.TMA_DIST_PATH)

    def test_tma_api_port_configured(self):
        """Config.TMA_API_PORT задан (по умолчанию 8080)."""
        assert Config.TMA_API_PORT > 0

    def test_tma_api_host_configured(self):
        """Config.TMA_API_HOST задан."""
        assert Config.TMA_API_HOST


# ===========================================================================
# Блок 3: _register_tma_static — юнит-тесты
# ===========================================================================


class TestRegisterTmaStatic:
    """Тесты функции _register_tma_static из api/routes.py."""

    @pytest.mark.asyncio
    async def test_registers_tma_routes(self, fake_dist):
        """После вызова _register_tma_static в роутере есть маршруты /tma."""
        from aiohttp import web
        app = web.Application()
        _register_tma_static(app, fake_dist)

        route_paths = [r.resource.canonical for r in app.router.routes()]
        # Должны быть /tma и /tma/
        assert any("/tma" in p for p in route_paths), (
            f"Маршрут /tma не зарегистрирован. Маршруты: {route_paths}"
        )

    @pytest.mark.asyncio
    async def test_registers_assets_static(self, fake_dist):
        """После вызова _register_tma_static зарегистрирован /tma/assets."""
        from aiohttp import web
        app = web.Application()
        _register_tma_static(app, fake_dist)

        route_paths = [r.resource.canonical for r in app.router.routes()]
        assert any("tma/assets" in p for p in route_paths), (
            f"Маршрут /tma/assets не зарегистрирован. Маршруты: {route_paths}"
        )

    @pytest.mark.asyncio
    async def test_no_assets_if_dir_missing(self, tmp_path):
        """Если assets/ нет — assets маршрут не добавляется (нет ошибки)."""
        from aiohttp import web
        dist = tmp_path / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html><html></html>", encoding="utf-8")
        # assets/ намеренно не создаём

        app = web.Application()
        # Не должно бросить исключение
        _register_tma_static(app, dist)

        route_paths = [r.resource.canonical for r in app.router.routes()]
        assert not any("tma/assets" in p for p in route_paths)


# ===========================================================================
# Блок 4: Static serving через HTTP (с fake_dist)
# ===========================================================================


@pytest.mark.asyncio
async def test_tma_root_returns_index_html(initialized_db, fake_dist):
    """/tma/ возвращает index.html (200, text/html)."""
    port = _next_port()
    server = await _make_server(initialized_db, port, tma_dist_path=fake_dist)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/tma/") as r:
                assert r.status == 200
                assert "text/html" in r.content_type
                html = await r.text()
                assert "TMA Test" in html
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_tma_without_slash_returns_index_html(initialized_db, fake_dist):
    """/tma (без слэша) возвращает index.html."""
    port = _next_port()
    server = await _make_server(initialized_db, port, tma_dist_path=fake_dist)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/tma", allow_redirects=True) as r:
                assert r.status == 200
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_tma_spa_products_route_returns_index(initialized_db, fake_dist):
    """/tma/products (SPA-маршрут) возвращает index.html для React Router."""
    port = _next_port()
    server = await _make_server(initialized_db, port, tma_dist_path=fake_dist)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/tma/products") as r:
                assert r.status == 200
                html = await r.text()
                assert "TMA Test" in html
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_tma_spa_wasted_budget_route(initialized_db, fake_dist):
    """/tma/wasted-budget SPA-маршрут → index.html."""
    port = _next_port()
    server = await _make_server(initialized_db, port, tma_dist_path=fake_dist)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/tma/wasted-budget") as r:
                assert r.status == 200
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_tma_assets_served(initialized_db, fake_dist):
    """/tma/assets/*.js отдаёт статический JS-файл."""
    port = _next_port()
    server = await _make_server(initialized_db, port, tma_dist_path=fake_dist)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/tma/assets/index-test.js") as r:
                assert r.status == 200
                content = await r.text()
                assert "vendor bundle" in content
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_tma_assets_css_served(initialized_db, fake_dist):
    """/tma/assets/*.css отдаёт CSS-файл."""
    port = _next_port()
    server = await _make_server(initialized_db, port, tma_dist_path=fake_dist)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/tma/assets/index-test.css") as r:
                assert r.status == 200
                assert "text/css" in r.content_type
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_tma_routes_no_auth_required(initialized_db, fake_dist):
    """/tma/* маршруты не требуют заголовка Authorization."""
    port = _next_port()
    server = await _make_server(initialized_db, port, tma_dist_path=fake_dist)
    try:
        async with aiohttp.ClientSession() as s:
            # Запрос без заголовков
            async with s.get(f"http://127.0.0.1:{port}/tma/") as r:
                # Не должно быть 401 или 403
                assert r.status not in (401, 403)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_tma_missing_dist_returns_503(initialized_db, tmp_path):
    """/tma/ возвращает 503 если dist/ не существует."""
    nonexistent = tmp_path / "does_not_exist"
    port = _next_port()
    server = await _make_server(initialized_db, port, tma_dist_path=nonexistent)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/tma/") as r:
                assert r.status == 503
    finally:
        await server.stop()


# ===========================================================================
# Блок 5: Static serving с реальным build dist/
# ===========================================================================


@pytest.mark.asyncio
async def test_real_dist_index_served(initialized_db):
    """Если dist/ собран (npm run build) — возвращает настоящий index.html."""
    if not Config.TMA_DIST_PATH.is_dir():
        pytest.skip("tma/dist/ не существует, пропускаем тест реального dist")

    port = _next_port()
    server = await _make_server(initialized_db, port, tma_dist_path=Config.TMA_DIST_PATH)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/tma/") as r:
                assert r.status == 200
                html = await r.text()
                assert "telegram-web-app.js" in html
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_real_dist_vendor_chunk_served(initialized_db):
    """Vendor JS-чанк раздаётся из реального dist/assets/."""
    if not Config.TMA_DIST_PATH.is_dir():
        pytest.skip("tma/dist/ не существует")

    assets = Config.TMA_DIST_PATH / "assets"
    vendors = list(assets.glob("vendor-*.js"))
    if not vendors:
        pytest.skip("vendor chunk не найден")

    port = _next_port()
    server = await _make_server(initialized_db, port, tma_dist_path=Config.TMA_DIST_PATH)
    try:
        async with aiohttp.ClientSession() as s:
            url = f"http://127.0.0.1:{port}/tma/assets/{vendors[0].name}"
            async with s.get(url) as r:
                assert r.status == 200
                assert r.content_type in ("application/javascript", "text/javascript")
    finally:
        await server.stop()


# ===========================================================================
# Блок 6: API + TMA совместная работа (авторизованные запросы)
# ===========================================================================


@pytest.mark.asyncio
async def test_api_still_requires_auth_with_tma_enabled(initialized_db, fake_dist):
    """/api/* всё ещё требует auth, даже когда TMA включён."""
    port = _next_port()
    server = await _make_server(initialized_db, port, tma_dist_path=fake_dist)
    try:
        async with aiohttp.ClientSession() as s:
            # Запрос без авторизации к API
            async with s.get(f"http://127.0.0.1:{port}/api/dashboard") as r:
                assert r.status == 401
            # Запрос с авторизацией — 200
            async with s.get(
                f"http://127.0.0.1:{port}/api/dashboard", headers=admin_headers()
            ) as r:
                assert r.status == 200
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_dashboard_and_tma_coexist(initialized_db, fake_dist):
    """Dashboard API и TMA статика работают на одном сервере."""
    port = _next_port()
    server = await _make_server(initialized_db, port, tma_dist_path=fake_dist)
    try:
        async with aiohttp.ClientSession() as s:
            # TMA без авторизации
            async with s.get(f"http://127.0.0.1:{port}/tma/") as r:
                assert r.status == 200
            # API с авторизацией
            async with s.get(
                f"http://127.0.0.1:{port}/api/dashboard", headers=admin_headers()
            ) as r:
                assert r.status == 200
                data = await r.json()
                assert "total_stats" in data
                assert "alerts" in data
    finally:
        await server.stop()


# ===========================================================================
# Блок 7: TMAApiServer без dist_path — нет /tma маршрутов
# ===========================================================================


@pytest.mark.asyncio
async def test_server_without_tma_dist_no_tma_route(initialized_db):
    """Если tma_dist_path не задан, /tma/ возвращает 404."""
    port = _next_port()
    server = await _make_server(initialized_db, port, tma_dist_path=None)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"http://127.0.0.1:{port}/tma/") as r:
                assert r.status == 404
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_server_without_tma_dist_api_still_works(initialized_db):
    """Без tma_dist_path API-маршруты работают нормально."""
    port = _next_port()
    server = await _make_server(initialized_db, port, tma_dist_path=None)
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"http://127.0.0.1:{port}/api/dashboard", headers=admin_headers()
            ) as r:
                assert r.status == 200
    finally:
        await server.stop()
