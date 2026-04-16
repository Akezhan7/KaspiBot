"""
Webhook-сервер для приёма входящих сообщений от Green API.

Использует aiohttp — лёгкий, async, совместим с aiogram.
Запускается параллельно с Telegram-ботом в том же event loop.

Green API формат входящего webhook:
{
    "typeWebhook": "incomingMessageReceived",
    "senderData": {
        "chatId": "77017545109@c.us",
        "sender": "77017545109@c.us",
        "chatName": "...",
        "senderName": "..."
    },
    "messageData": {
        "typeMessage": "textMessage",
        "textMessageData": {
            "textMessage": "текст"
        }
    }
}
"""
import logging
import time
from collections import defaultdict
from typing import Callable, Awaitable, Dict, List, Optional, Set

from aiohttp import web

from .phone_utils import chat_id_to_phone

logger = logging.getLogger(__name__)

# Типы webhook-ов, которые содержат входящие сообщения
_INCOMING_MESSAGE_TYPES = {
    "incomingMessageReceived",
}

# Типы сообщений, из которых можно извлечь текст
_TEXT_MESSAGE_TYPES = {
    "textMessage",
    "extendedTextMessage",
}

# Callback: async def handler(sender_phone: str, text: str, sender_name: str, raw_data: dict)
IncomingMessageHandler = Callable[[str, str, str, dict], Awaitable[None]]

# Green API серверные IP (документация Green API)
# https://green-api.com/docs/api/
_GREEN_API_IP_WHITELIST: Set[str] = {
    # По умолчанию пустой — если не задан, пропускаем все IP (для dev-режима)
    # В production заполняется через Config
}

# Максимальная длина текста сообщения (WhatsApp лимит)
_MAX_TEXT_LENGTH = 4096

# Rate limiting: макс. запросов с одного IP за окно
_RATE_LIMIT_WINDOW_SEC = 60
_RATE_LIMIT_MAX_REQUESTS = 60


class _RateLimiter:
    """Простой in-memory rate limiter на основе скользящего окна."""

    def __init__(self, window_sec: int = _RATE_LIMIT_WINDOW_SEC,
                 max_requests: int = _RATE_LIMIT_MAX_REQUESTS) -> None:
        self._window = window_sec
        self._max = max_requests
        self._requests: Dict[str, List[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        timestamps = self._requests[key]
        # Очищаем устаревшие записи
        cutoff = now - self._window
        self._requests[key] = [t for t in timestamps if t > cutoff]
        if len(self._requests[key]) >= self._max:
            return False
        self._requests[key].append(now)
        return True


class WhatsAppWebhook:
    """
    HTTP-сервер для приёма webhook-ов от Green API.

    Пример запуска:
        webhook = WhatsAppWebhook(
            host="0.0.0.0",
            port=8443,
            on_incoming_message=my_handler
        )
        await webhook.start()
        # ... при завершении:
        await webhook.stop()
    """

    def __init__(
        self,
        host: str,
        port: int,
        on_incoming_message: Optional[IncomingMessageHandler] = None,
        ip_whitelist: Optional[Set[str]] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._on_incoming_message = on_incoming_message
        self._ip_whitelist: Set[str] = ip_whitelist or set()
        self._rate_limiter = _RateLimiter()
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        """Запустить webhook-сервер."""
        self._app = web.Application()
        self._app.router.add_post("/webhook", self._handle_webhook)
        self._app.router.add_post("/webhook/{suffix}", self._handle_webhook)
        self._app.router.add_get("/health", self._handle_health)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()

        logger.info(
            f"WhatsApp webhook сервер запущен на "
            f"http://{self._host}:{self._port}/webhook"
        )

    async def stop(self) -> None:
        """Остановить webhook-сервер."""
        if self._runner:
            await self._runner.cleanup()
            logger.info("WhatsApp webhook сервер остановлен")

    def set_message_handler(self, handler: IncomingMessageHandler) -> None:
        """Установить обработчик входящих сообщений (для отложенной инициализации)."""
        self._on_incoming_message = handler

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health-check эндпоинт."""
        return web.json_response({"status": "ok"})

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """
        Обработка входящего webhook от Green API.

        1. Проверяем IP отправителя (whitelist)
        2. Проверяем rate limit
        3. Парсим JSON payload
        4. Определяем тип webhook
        5. Извлекаем телефон отправителя и текст
        6. Вызываем обработчик
        7. Отвечаем 200 OK (Green API ожидает быстрый ответ)
        """
        # IP-whitelist (если задан)
        remote_ip = request.remote or ""
        if self._ip_whitelist and remote_ip not in self._ip_whitelist:
            logger.warning(f"Webhook: запрос от неразрешённого IP {remote_ip}")
            return web.Response(status=403, text="Forbidden")

        # Rate limiting по IP
        if not self._rate_limiter.is_allowed(remote_ip):
            logger.warning(f"Webhook: rate limit для IP {remote_ip}")
            return web.Response(status=429, text="Too Many Requests")

        try:
            data = await request.json()
        except Exception:
            logger.warning("Webhook: невалидный JSON")
            return web.Response(status=400, text="Invalid JSON")

        type_webhook = data.get("typeWebhook", "")

        # Обрабатываем только входящие сообщения
        if type_webhook not in _INCOMING_MESSAGE_TYPES:
            logger.debug(f"Webhook: пропускаем тип '{type_webhook}'")
            return web.Response(status=200, text="OK")

        # Извлекаем данные
        sender_data = data.get("senderData", {})
        message_data = data.get("messageData", {})

        chat_id = sender_data.get("chatId", "")
        sender_name = sender_data.get("senderName", "")
        type_message = message_data.get("typeMessage", "")

        # Извлекаем текст сообщения
        text = self._extract_text(message_data, type_message)
        if not text:
            logger.debug(
                f"Webhook: пропускаем нетекстовое сообщение "
                f"(тип: {type_message}) от {chat_id[:7]}***"
            )
            return web.Response(status=200, text="OK")

        # Обрезаем слишком длинный текст
        if len(text) > _MAX_TEXT_LENGTH:
            logger.warning(
                f"Webhook: текст обрезан с {len(text)} до {_MAX_TEXT_LENGTH} символов"
            )
            text = text[:_MAX_TEXT_LENGTH]

        # Нормализуем телефон из chatId
        sender_phone = chat_id_to_phone(chat_id)
        if not sender_phone:
            logger.warning(f"Webhook: невалидный chatId: {chat_id}")
            return web.Response(status=200, text="OK")

        logger.info(
            f"Webhook: входящее сообщение от {sender_phone[:4]}*** "
            f"({sender_name}): {text[:50]}..."
        )

        # Вызываем обработчик асинхронно (не блокируем ответ Green API)
        if self._on_incoming_message:
            try:
                await self._on_incoming_message(
                    sender_phone, text, sender_name, data
                )
            except Exception as e:
                logger.error(
                    f"Webhook: ошибка обработки сообщения от "
                    f"{sender_phone[:4]}***: {e}",
                    exc_info=True,
                )

        return web.Response(status=200, text="OK")

    @staticmethod
    def _extract_text(message_data: dict, type_message: str) -> str:
        """Извлечь текст из messageData в зависимости от типа сообщения."""
        if type_message == "textMessage":
            return (
                message_data
                .get("textMessageData", {})
                .get("textMessage", "")
            )

        if type_message == "extendedTextMessage":
            return (
                message_data
                .get("extendedTextMessageData", {})
                .get("text", "")
            )

        return ""
