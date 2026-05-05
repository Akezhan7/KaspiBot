"""
TMA API Server — aiohttp сервер для Telegram Mini App.

Запускается параллельно с Telegram-ботом (другой порт).
Валидация: HMAC-SHA256 от Telegram WebApp initData.
Доступ: только пользователи из ADMIN_USER_IDS.

Пример использования:
    server = TMAApiServer(
        processor=analytics_processor,
        aggregator=data_aggregator,
        ads_db=ads_data_db,
        products_db=products_db,
        scrape_logs_db=scrape_logs_db,
        scrape_trigger=scheduled_scrape_fn,
        bot_token=Config.TELEGRAM_BOT_TOKEN,
        admin_user_ids=set(Config.ADMIN_USER_IDS),
        host=Config.TMA_API_HOST,
        port=Config.TMA_API_PORT,
        cors_origins=Config.TMA_CORS_ORIGINS,
    )
    await server.start()
    # ... при завершении:
    await server.stop()
"""
import logging
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Set

from aiohttp import web

from .auth_middleware import create_auth_middleware
from .routes import register_routes, _register_tma_static
from ._keys import DEPS_KEY

if TYPE_CHECKING:
    from analytics import AdsAnalyticsProcessor, DataAggregator
    from database.ads_data import AdsDataDB, ScrapeLogsDB
    from database.products import ProductsDB

logger = logging.getLogger(__name__)

# Typed key for app[DEPS_KEY] to suppress aiohttp AppKey warning
_DEPS_KEY = DEPS_KEY  # re-exported for backwards compat

# Разрешённые CORS origins по умолчанию
_DEFAULT_CORS_ORIGINS = {
    "https://web.telegram.org",
    "https://t.me",
}


class TMAApiServer:
    """
    HTTP-сервер REST API для Telegram Mini App.

    Поднимается на отдельном порту (по умолчанию 8080),
    параллельно с WhatsApp webhook (:8443).
    """

    def __init__(
        self,
        processor: "AdsAnalyticsProcessor",
        aggregator: "DataAggregator",
        ads_db: "AdsDataDB",
        products_db: "ProductsDB",
        scrape_logs_db: "ScrapeLogsDB",
        bot_token: str,
        admin_user_ids: Set[int],
        host: str = "0.0.0.0",
        port: int = 8080,
        cors_origins: Optional[list[str]] = None,
        scrape_trigger: Optional[Callable[[], Coroutine[Any, Any, None]]] = None,
        tma_dist_path: Optional[Path] = None,
    ) -> None:
        self._processor = processor
        self._aggregator = aggregator
        self._ads_db = ads_db
        self._products_db = products_db
        self._scrape_logs_db = scrape_logs_db
        self._bot_token = bot_token
        self._admin_user_ids = admin_user_ids
        self._host = host
        self._port = port
        self._cors_origins: Set[str] = (
            set(cors_origins) if cors_origins else _DEFAULT_CORS_ORIGINS.copy()
        )
        self._scrape_trigger = scrape_trigger
        self._tma_dist_path = tma_dist_path
        self._runner: Optional[web.AppRunner] = None

    def set_scrape_trigger(self, fn: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Установить callback для ручного запуска скрапинга (вызывается из main.py)."""
        self._scrape_trigger = fn

    async def start(self) -> None:
        """Запустить API-сервер."""
        auth_mw = create_auth_middleware(
            bot_token=self._bot_token,
            admin_user_ids=self._admin_user_ids,
        )

        app = web.Application(middlewares=[auth_mw, self._cors_middleware])

        # Зависимости — доступны всем обработчикам через request.app[DEPS_KEY]
        app[DEPS_KEY] = {
            "processor": self._processor,
            "aggregator": self._aggregator,
            "ads_db": self._ads_db,
            "products_db": self._products_db,
            "scrape_logs_db": self._scrape_logs_db,
            "scrape_trigger": self._scrape_trigger,
        }

        register_routes(app)

        # Раздача статики TMA (если путь задан — регистрируем маршруты,
        # обработчик сам вернёт 503 если файлы отсутствуют)
        if self._tma_dist_path is not None:
            _register_tma_static(app, self._tma_dist_path)
            if self._tma_dist_path.is_dir():
                logger.info("TMA static: раздаётся из %s", self._tma_dist_path)
            else:
                logger.warning(
                    "TMA static: путь %s не существует, маршруты зарегистрированы (вернут 503)",
                    self._tma_dist_path,
                )

        self._runner = web.AppRunner(app)
        await self._runner.setup()

        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

        logger.info(
            "TMA API server запущен на http://%s:%d",
            self._host,
            self._port,
        )

    async def stop(self) -> None:
        """Остановить API-сервер."""
        if self._runner:
            await self._runner.cleanup()
            logger.info("TMA API server остановлен")

    @web.middleware
    async def _cors_middleware(
        self,
        request: web.Request,
        handler: Callable,
    ) -> web.Response:
        """CORS middleware: добавляет заголовки для разрешённых origins."""
        origin = request.headers.get("Origin", "")
        response = await handler(request)

        # Разрешаем настроенные origins; в dev-режиме localhost всегда пропускаем
        if origin in self._cors_origins or origin.startswith("http://localhost"):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
            response.headers["Access-Control-Max-Age"] = "3600"

        return response
