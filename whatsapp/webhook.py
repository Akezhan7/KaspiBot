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
from typing import Callable, Awaitable, Optional

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
    ) -> None:
        self._host = host
        self._port = port
        self._on_incoming_message = on_incoming_message
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        """Запустить webhook-сервер."""
        self._app = web.Application()
        self._app.router.add_post("/webhook", self._handle_webhook)
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

        1. Парсим JSON payload
        2. Определяем тип webhook
        3. Извлекаем телефон отправителя и текст
        4. Вызываем обработчик
        5. Отвечаем 200 OK (Green API ожидает быстрый ответ)
        """
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
