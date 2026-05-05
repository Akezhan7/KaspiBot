"""
Auth middleware для TMA API.

Валидирует заголовок Authorization: tma {initData}
используя HMAC-SHA256 алгоритм Telegram WebApp.

Алгоритм (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):
1. Извлечь initData из заголовка Authorization: tma {initData}
2. Проверить подпись через check_webapp_signature(bot_token, initData)
3. Распарсить данные через safe_parse_webapp_init_data(bot_token, initData)
4. Проверить свежесть auth_date (не старше 1 часа)
5. Проверить user.id в списке ADMIN_USER_IDS
"""
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Set

from aiohttp import web
from aiogram.utils.web_app import check_webapp_signature, safe_parse_webapp_init_data

logger = logging.getLogger(__name__)

# Максимальный возраст initData в секундах (1 час)
_INIT_DATA_MAX_AGE_SEC = 3600


def create_auth_middleware(
    bot_token: str,
    admin_user_ids: Set[int],
) -> "web.middleware":
    """
    Фабрика aiohttp middleware для проверки Telegram WebApp initData.

    Пропускает:
      - CORS preflight запросы (OPTIONS)

    Блокирует с 401:
      - Отсутствующий/невалидный заголовок Authorization
      - Невалидная подпись initData
      - Устаревшие данные (auth_date > 1 часа)

    Блокирует с 403:
      - Пользователь не входит в ADMIN_USER_IDS
    """

    @web.middleware
    async def auth_middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.Response]],
    ) -> web.Response:
        # CORS preflight не требует авторизации
        if request.method == "OPTIONS":
            return await handler(request)

        # Публичные маршруты без авторизации
        if request.path == "/health" or request.path.startswith("/tma"):
            return await handler(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("tma "):
            logger.warning(
                "TMA API: отсутствует заголовок Authorization от %s",
                request.remote,
            )
            return web.json_response({"error": "Unauthorized"}, status=401)

        init_data = auth_header[4:]

        # Проверка HMAC-подписи
        if not check_webapp_signature(bot_token, init_data):
            logger.warning(
                "TMA API: невалидная подпись initData от %s",
                request.remote,
            )
            return web.json_response({"error": "Unauthorized"}, status=401)

        # Парсинг и дополнительные проверки
        try:
            webapp_data = safe_parse_webapp_init_data(bot_token, init_data)
        except Exception as exc:
            logger.warning("TMA API: ошибка парсинга initData: %s", exc)
            return web.json_response({"error": "Unauthorized"}, status=401)

        # Проверка свежести auth_date
        auth_ts = webapp_data.auth_date.timestamp()
        now_ts = time.time()
        if now_ts - auth_ts > _INIT_DATA_MAX_AGE_SEC:
            logger.warning(
                "TMA API: initData устарела (age=%.0fs) от %s",
                now_ts - auth_ts,
                request.remote,
            )
            return web.json_response({"error": "Unauthorized: initData expired"}, status=401)

        # Проверка прав администратора
        user = webapp_data.user
        if user is None or user.id not in admin_user_ids:
            user_id = user.id if user else None
            logger.warning(
                "TMA API: доступ запрещён для user_id=%s от %s",
                user_id,
                request.remote,
            )
            return web.json_response({"error": "Forbidden"}, status=403)

        # Передаём идентификатор пользователя вниз по стеку
        request["tma_user_id"] = user.id
        request["tma_user_name"] = user.first_name

        return await handler(request)

    return auth_middleware
