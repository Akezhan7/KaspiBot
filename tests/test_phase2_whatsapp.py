"""
Тесты для Фазы 2: WhatsApp клиент (Green API)

Покрывает:
- phone_utils: нормализация, валидация, конвертация
- GreenAPIClient: send_text, check_phone_exists, mark_as_read (mock HTTP)
- WhatsAppWebhook: приём входящих сообщений (mock aiohttp)
"""
import asyncio
import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock

# === phone_utils тесты ===

from whatsapp.phone_utils import (
    normalize_phone,
    is_valid_kz_phone,
    phone_to_chat_id,
    chat_id_to_phone,
)


class TestNormalizePhone:
    """Тесты нормализации телефонных номеров."""

    def test_full_format_with_plus(self):
        assert normalize_phone("+7 (701) 754-51-09") == "77017545109"

    def test_full_format_no_plus(self):
        assert normalize_phone("7 (701) 754-51-09") == "77017545109"

    def test_eight_prefix(self):
        assert normalize_phone("8 701 754 51 09") == "77017545109"

    def test_already_normalized(self):
        assert normalize_phone("77017545109") == "77017545109"

    def test_with_plus_prefix(self):
        assert normalize_phone("+77017545109") == "77017545109"

    def test_ten_digits(self):
        assert normalize_phone("7017545109") == "77017545109"

    def test_empty_string(self):
        assert normalize_phone("") == ""

    def test_none_returns_empty(self):
        assert normalize_phone(None) == ""

    def test_invalid_short_number(self):
        assert normalize_phone("12345") == ""

    def test_invalid_long_number(self):
        assert normalize_phone("7701754510999999") == ""

    def test_non_phone_string(self):
        assert normalize_phone("hello world") == ""

    def test_eleven_digits_starting_with_8(self):
        assert normalize_phone("87017545109") == "77017545109"

    def test_spaces_and_dashes(self):
        assert normalize_phone("7-701-754-51-09") == "77017545109"


class TestIsValidKzPhone:
    """Тесты валидации казахстанских мобильных номеров."""

    def test_valid_mobile_701(self):
        assert is_valid_kz_phone("+7 701 123 45 67") is True

    def test_valid_mobile_777(self):
        assert is_valid_kz_phone("87771234567") is True

    def test_valid_mobile_747(self):
        assert is_valid_kz_phone("77471234567") is True

    def test_invalid_prefix(self):
        """Городские номера и несуществующие префиксы."""
        assert is_valid_kz_phone("77121234567") is False

    def test_invalid_format(self):
        assert is_valid_kz_phone("not a phone") is False

    def test_empty(self):
        assert is_valid_kz_phone("") is False


class TestPhoneToChatId:
    """Тесты конвертации телефона в chat ID."""

    def test_normalized_number(self):
        assert phone_to_chat_id("77017545109") == "77017545109@c.us"

    def test_raw_format(self):
        assert phone_to_chat_id("+7 (701) 754-51-09") == "77017545109@c.us"

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            phone_to_chat_id("invalid")


class TestChatIdToPhone:
    """Тесты извлечения телефона из chat ID."""

    def test_valid_chat_id(self):
        assert chat_id_to_phone("77017545109@c.us") == "77017545109"

    def test_group_chat_id(self):
        """Групповые чаты — невалидные номера."""
        assert chat_id_to_phone("79876543210-1581234048@g.us") == ""

    def test_invalid_format(self):
        assert chat_id_to_phone("not_a_chat_id") == ""

    def test_empty(self):
        assert chat_id_to_phone("") == ""


# === GreenAPIClient тесты (mock HTTP) ===

from whatsapp.client import GreenAPIClient


class TestGreenAPIClient:
    """Тесты клиента Green API с mock HTTP."""

    def _make_client(self) -> GreenAPIClient:
        return GreenAPIClient(
            api_url="https://api.green-api.com",
            instance_id="1234567890",
            token="test_token_abc123",
        )

    @pytest.mark.asyncio
    async def test_send_text_success(self):
        """Успешная отправка сообщения."""
        client = self._make_client()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"idMessage": "3EB0C767D097B7C7C030"}
        mock_response.raise_for_status = MagicMock()

        with patch("whatsapp.client.httpx.AsyncClient") as mock_httpx:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value = mock_instance

            result = await client.send_text("77017545109", "Тестовое сообщение")

            assert result["idMessage"] == "3EB0C767D097B7C7C030"
            mock_instance.post.assert_called_once()

            # Проверяем payload
            call_args = mock_instance.post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["chatId"] == "77017545109@c.us"
            assert payload["message"] == "Тестовое сообщение"

    @pytest.mark.asyncio
    async def test_send_text_invalid_phone(self):
        """Отправка на невалидный номер — ValueError."""
        client = self._make_client()
        with pytest.raises(ValueError, match="Невалидный номер"):
            await client.send_text("invalid", "test")

    @pytest.mark.asyncio
    async def test_send_text_too_long(self):
        """Слишком длинное сообщение — ValueError."""
        client = self._make_client()
        with pytest.raises(ValueError, match="слишком длинный"):
            await client.send_text("77017545109", "x" * 20001)

    @pytest.mark.asyncio
    async def test_check_phone_exists_true(self):
        """Номер существует в WhatsApp."""
        client = self._make_client()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"existsWhatsapp": True}
        mock_response.raise_for_status = MagicMock()

        with patch("whatsapp.client.httpx.AsyncClient") as mock_httpx:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value = mock_instance

            result = await client.check_phone_exists("+7 (701) 754-51-09")

            assert result is True

            # Проверяем что phoneNumber — integer
            call_args = mock_instance.post.call_args
            payload = call_args.kwargs.get("json") or call_args[1].get("json")
            assert payload["phoneNumber"] == 77017545109
            assert isinstance(payload["phoneNumber"], int)

    @pytest.mark.asyncio
    async def test_check_phone_exists_false(self):
        """Номер не существует в WhatsApp."""
        client = self._make_client()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"existsWhatsapp": False}
        mock_response.raise_for_status = MagicMock()

        with patch("whatsapp.client.httpx.AsyncClient") as mock_httpx:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value = mock_instance

            result = await client.check_phone_exists("77017545109")

            assert result is False

    @pytest.mark.asyncio
    async def test_check_phone_invalid_returns_false(self):
        """Невалидный номер — возвращает False (не выбрасывает исключение)."""
        client = self._make_client()
        result = await client.check_phone_exists("invalid")
        assert result is False

    @pytest.mark.asyncio
    async def test_mark_as_read_success(self):
        """Успешная пометка прочитанным."""
        client = self._make_client()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"setRead": True}
        mock_response.raise_for_status = MagicMock()

        with patch("whatsapp.client.httpx.AsyncClient") as mock_httpx:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value = mock_instance

            result = await client.mark_as_read("77017545109@c.us")

            assert result is True

    @pytest.mark.asyncio
    async def test_build_url(self):
        """Проверка формирования URL."""
        client = self._make_client()
        url = client._build_url("sendMessage")
        assert url == (
            "https://api.green-api.com/waInstance1234567890"
            "/sendMessage/test_token_abc123"
        )


# === WhatsAppWebhook тесты ===


class TestWebhookExtractText:
    """Тесты извлечения текста из messageData."""

    from whatsapp.webhook import WhatsAppWebhook

    def test_text_message(self):
        data = {
            "textMessageData": {"textMessage": "Привет!"}
        }
        assert self.WhatsAppWebhook._extract_text(data, "textMessage") == "Привет!"

    def test_extended_text_message(self):
        data = {
            "extendedTextMessageData": {"text": "Ссылка на товар"}
        }
        assert self.WhatsAppWebhook._extract_text(data, "extendedTextMessage") == "Ссылка на товар"

    def test_unknown_type_returns_empty(self):
        data = {"imageMessage": {"url": "..."}}
        assert self.WhatsAppWebhook._extract_text(data, "imageMessage") == ""

    def test_missing_data_returns_empty(self):
        assert self.WhatsAppWebhook._extract_text({}, "textMessage") == ""


class TestWebhookParsing:
    """Тесты обработки входящих webhook-ов."""

    @pytest.mark.asyncio
    async def test_incoming_text_message_calls_handler(self):
        """Входящее текстовое сообщение вызывает handler."""
        from whatsapp.webhook import WhatsAppWebhook

        handler = AsyncMock()
        webhook = WhatsAppWebhook(
            host="127.0.0.1",
            port=18443,
            on_incoming_message=handler,
        )

        # Формируем mock request
        payload = {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {
                "chatId": "77017545109@c.us",
                "sender": "77017545109@c.us",
                "senderName": "Тест Продавец",
            },
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {
                    "textMessage": "Я уже снял товар"
                },
            },
        }

        mock_request = AsyncMock()
        mock_request.json.return_value = payload

        response = await webhook._handle_webhook(mock_request)

        assert response.status == 200
        handler.assert_called_once_with(
            "77017545109",
            "Я уже снял товар",
            "Тест Продавец",
            payload,
        )

    @pytest.mark.asyncio
    async def test_non_incoming_type_ignored(self):
        """Не-входящие типы webhook игнорируются."""
        from whatsapp.webhook import WhatsAppWebhook

        handler = AsyncMock()
        webhook = WhatsAppWebhook(
            host="127.0.0.1",
            port=18443,
            on_incoming_message=handler,
        )

        mock_request = AsyncMock()
        mock_request.json.return_value = {
            "typeWebhook": "outgoingMessageStatus",
            "data": {},
        }

        response = await webhook._handle_webhook(mock_request)

        assert response.status == 200
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_image_message_ignored(self):
        """Нетекстовые сообщения (изображения) игнорируются."""
        from whatsapp.webhook import WhatsAppWebhook

        handler = AsyncMock()
        webhook = WhatsAppWebhook(
            host="127.0.0.1",
            port=18443,
            on_incoming_message=handler,
        )

        mock_request = AsyncMock()
        mock_request.json.return_value = {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {
                "chatId": "77017545109@c.us",
                "senderName": "Тест",
            },
            "messageData": {
                "typeMessage": "imageMessage",
                "fileMessageData": {"downloadUrl": "..."},
            },
        }

        response = await webhook._handle_webhook(mock_request)

        assert response.status == 200
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self):
        """Невалидный JSON — ответ 400."""
        from whatsapp.webhook import WhatsAppWebhook

        webhook = WhatsAppWebhook(host="127.0.0.1", port=18443)

        mock_request = AsyncMock()
        mock_request.json.side_effect = json.JSONDecodeError("test", "", 0)

        response = await webhook._handle_webhook(mock_request)

        assert response.status == 400

    @pytest.mark.asyncio
    async def test_handler_exception_doesnt_break_response(self):
        """Ошибка в handler не ломает ответ webhook-а."""
        from whatsapp.webhook import WhatsAppWebhook

        handler = AsyncMock(side_effect=RuntimeError("test error"))
        webhook = WhatsAppWebhook(
            host="127.0.0.1",
            port=18443,
            on_incoming_message=handler,
        )

        mock_request = AsyncMock()
        mock_request.json.return_value = {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {
                "chatId": "77017545109@c.us",
                "senderName": "Тест",
            },
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": "test"},
            },
        }

        response = await webhook._handle_webhook(mock_request)

        # Должен вернуть 200, несмотря на ошибку в handler
        assert response.status == 200
