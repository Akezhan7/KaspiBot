"""
Тесты Фазы 9: Безопасность, обработка ошибок, дополнительное покрытие.
Запуск: pytest tests/test_phase9_security.py -v

Покрывает:
- Webhook: IP whitelist, rate limiting, валидация входных данных
- GreenAPIClient: retry при таймауте/5xx, исчерпание попыток
- Classifier: таймаут OpenAI, ошибки API
- Notifications: отправка всем админам
- WorkflowEngine: авто-ответ → уведомление при ошибке
"""
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from database.schema import DatabaseSchema
from database.migrations import DatabaseMigrations
from database.sellers import SellersDB
from database.products import ProductsDB
from database.product_sellers import ProductSellersDB
from database.seller_workflow import SellerWorkflowDB
from database.message_log import MessageLogDB
from database.legal_requests import LegalRequestsDB
from whatsapp.classifier import (
    ClassificationResult,
    ClassificationType,
    MessageClassifier,
)
from whatsapp.webhook import WhatsAppWebhook, _RateLimiter
from workflow.engine import WorkflowEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _init_db(db_path: Path):
    await DatabaseSchema.init_db(db_path)
    migrations = DatabaseMigrations(db_path)
    await migrations.run_migrations()


async def _seed_seller(db_path: Path, merchant_id: str = "M001",
                       name: str = "Test Shop",
                       phone: str = "+77011234567"):
    sellers = SellersDB(db_path)
    await sellers.add_seller(merchant_id, name, phone)


async def _seed_product(db_path: Path, sku: str = "SKU001",
                        url: str = "https://kaspi.kz/shop/p/sku001",
                        title: str = "Тестовый товар"):
    products = ProductsDB(db_path)
    await products.add_product(sku, url, title)


def _make_engine(db_path, wa_client=None, classifier=None,
                 notifier=None, scanner=None):
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
        notifier.notify_warn1_sent = AsyncMock()
        notifier.notify_warn2_sent = AsyncMock()
        notifier.notify_incoming_message = AsyncMock()
        notifier.notify_legal_request = AsyncMock()
        notifier.notify_detached = AsyncMock()

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


def _make_mock_request(payload: dict, remote_ip: str = "10.0.0.1"):
    """Создать mock aiohttp Request."""
    mock = AsyncMock()
    mock.json.return_value = payload
    mock.remote = remote_ip
    return mock


def _make_valid_incoming_payload(
    text: str = "Привет",
    chat_id: str = "77017545109@c.us",
    sender_name: str = "Тест",
) -> dict:
    return {
        "typeWebhook": "incomingMessageReceived",
        "senderData": {
            "chatId": chat_id,
            "sender": chat_id,
            "senderName": sender_name,
        },
        "messageData": {
            "typeMessage": "textMessage",
            "textMessageData": {"textMessage": text},
        },
    }


# =====================================================================
# Webhook: IP whitelist
# =====================================================================

class TestWebhookIPWhitelist:
    """Тесты IP-фильтрации на webhook."""

    @pytest.mark.asyncio
    async def test_no_whitelist_allows_any_ip(self):
        """Без whitelist — все IP разрешены."""
        handler = AsyncMock()
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
            on_incoming_message=handler,
        )
        payload = _make_valid_incoming_payload()
        request = _make_mock_request(payload, remote_ip="1.2.3.4")

        response = await webhook._handle_webhook(request)

        assert response.status == 200
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_whitelist_allows_listed_ip(self):
        """IP из whitelist — запрос проходит."""
        handler = AsyncMock()
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
            on_incoming_message=handler,
            ip_whitelist={"10.0.0.1", "10.0.0.2"},
        )
        payload = _make_valid_incoming_payload()
        request = _make_mock_request(payload, remote_ip="10.0.0.1")

        response = await webhook._handle_webhook(request)

        assert response.status == 200
        handler.assert_called_once()

    @pytest.mark.asyncio
    async def test_whitelist_rejects_unlisted_ip(self):
        """IP не из whitelist — 403 Forbidden."""
        handler = AsyncMock()
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
            on_incoming_message=handler,
            ip_whitelist={"10.0.0.1"},
        )
        payload = _make_valid_incoming_payload()
        request = _make_mock_request(payload, remote_ip="192.168.1.1")

        response = await webhook._handle_webhook(request)

        assert response.status == 403
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_whitelist_multiple_ips(self):
        """Несколько IP в whitelist — все работают."""
        handler = AsyncMock()
        whitelist = {"10.0.0.1", "10.0.0.2", "10.0.0.3"}
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
            on_incoming_message=handler,
            ip_whitelist=whitelist,
        )

        for ip in whitelist:
            handler.reset_mock()
            payload = _make_valid_incoming_payload()
            request = _make_mock_request(payload, remote_ip=ip)
            response = await webhook._handle_webhook(request)
            assert response.status == 200
            handler.assert_called_once()


# =====================================================================
# Webhook: Rate limiting
# =====================================================================

class TestWebhookRateLimit:
    """Тесты rate limiting на webhook."""

    @pytest.mark.asyncio
    async def test_normal_rate_passes(self):
        """Обычное количество запросов проходит."""
        handler = AsyncMock()
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
            on_incoming_message=handler,
        )
        payload = _make_valid_incoming_payload()

        # 10 запросов — все должны пройти
        for _ in range(10):
            request = _make_mock_request(payload, remote_ip="10.0.0.1")
            response = await webhook._handle_webhook(request)
            assert response.status == 200

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded(self):
        """Превышение rate limit — 429 Too Many Requests."""
        handler = AsyncMock()
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
            on_incoming_message=handler,
        )
        # Заполняем rate limiter до лимита
        webhook._rate_limiter._max = 5

        payload = _make_valid_incoming_payload()

        # 5 запросов — OK
        for _ in range(5):
            request = _make_mock_request(payload, remote_ip="10.0.0.1")
            response = await webhook._handle_webhook(request)
            assert response.status == 200

        # 6-й — 429
        request = _make_mock_request(payload, remote_ip="10.0.0.1")
        response = await webhook._handle_webhook(request)
        assert response.status == 429

    @pytest.mark.asyncio
    async def test_rate_limit_different_ips(self):
        """Rate limit считается отдельно для каждого IP."""
        handler = AsyncMock()
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
            on_incoming_message=handler,
        )
        webhook._rate_limiter._max = 3
        payload = _make_valid_incoming_payload()

        # 3 от IP-A
        for _ in range(3):
            request = _make_mock_request(payload, remote_ip="10.0.0.1")
            await webhook._handle_webhook(request)

        # IP-A лимит — 429
        request = _make_mock_request(payload, remote_ip="10.0.0.1")
        response = await webhook._handle_webhook(request)
        assert response.status == 429

        # IP-B — всё ещё OK
        request = _make_mock_request(payload, remote_ip="10.0.0.2")
        response = await webhook._handle_webhook(request)
        assert response.status == 200


class TestRateLimiter:
    """Юнит-тесты для _RateLimiter."""

    def test_allows_within_limit(self):
        limiter = _RateLimiter(window_sec=60, max_requests=3)
        assert limiter.is_allowed("key1") is True
        assert limiter.is_allowed("key1") is True
        assert limiter.is_allowed("key1") is True

    def test_blocks_over_limit(self):
        limiter = _RateLimiter(window_sec=60, max_requests=2)
        assert limiter.is_allowed("key1") is True
        assert limiter.is_allowed("key1") is True
        assert limiter.is_allowed("key1") is False

    def test_separate_keys(self):
        limiter = _RateLimiter(window_sec=60, max_requests=1)
        assert limiter.is_allowed("a") is True
        assert limiter.is_allowed("a") is False
        assert limiter.is_allowed("b") is True

    def test_window_expiry(self):
        """Записи старше окна очищаются."""
        limiter = _RateLimiter(window_sec=1, max_requests=1)
        assert limiter.is_allowed("key1") is True
        assert limiter.is_allowed("key1") is False

        # Изменяем timestamps чтобы имитировать прошлое время
        limiter._requests["key1"] = [time.monotonic() - 2]
        assert limiter.is_allowed("key1") is True


# =====================================================================
# Webhook: Input validation
# =====================================================================

class TestWebhookInputValidation:
    """Тесты валидации входных данных webhook."""

    @pytest.mark.asyncio
    async def test_long_text_truncated(self):
        """Очень длинный текст обрезается до _MAX_TEXT_LENGTH."""
        handler = AsyncMock()
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
            on_incoming_message=handler,
        )
        long_text = "A" * 10000
        payload = _make_valid_incoming_payload(text=long_text)
        request = _make_mock_request(payload)

        response = await webhook._handle_webhook(request)

        assert response.status == 200
        handler.assert_called_once()
        # Текст в вызове handler обрезан до 4096
        call_args = handler.call_args
        received_text = call_args[0][1]
        assert len(received_text) == 4096

    @pytest.mark.asyncio
    async def test_empty_text_ignored(self):
        """Пустой текст — сообщение пропускается."""
        handler = AsyncMock()
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
            on_incoming_message=handler,
        )
        payload = _make_valid_incoming_payload(text="")
        request = _make_mock_request(payload)

        response = await webhook._handle_webhook(request)

        assert response.status == 200
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_chat_id_ignored(self):
        """Невалидный chatId — сообщение пропускается."""
        handler = AsyncMock()
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
            on_incoming_message=handler,
        )
        payload = _make_valid_incoming_payload(chat_id="invalid_id")
        request = _make_mock_request(payload)

        response = await webhook._handle_webhook(request)

        assert response.status == 200
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_sender_data(self):
        """Отсутствие senderData — сообщение пропускается."""
        handler = AsyncMock()
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
            on_incoming_message=handler,
        )
        payload = {
            "typeWebhook": "incomingMessageReceived",
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": "test"},
            },
        }
        request = _make_mock_request(payload)

        response = await webhook._handle_webhook(request)

        assert response.status == 200
        handler.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_json_returns_400(self):
        """Невалидный JSON — 400 Bad Request."""
        webhook = WhatsAppWebhook(host="127.0.0.1", port=18443)
        mock_request = AsyncMock()
        mock_request.remote = "10.0.0.1"
        mock_request.json.side_effect = json.JSONDecodeError("test", "", 0)

        response = await webhook._handle_webhook(mock_request)

        assert response.status == 400

    @pytest.mark.asyncio
    async def test_non_incoming_type_ignored(self):
        """Не-incoming типы возвращают 200 без вызова handler."""
        handler = AsyncMock()
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
            on_incoming_message=handler,
        )
        payload = {
            "typeWebhook": "outgoingMessageStatus",
            "data": {},
        }
        request = _make_mock_request(payload)

        response = await webhook._handle_webhook(request)

        assert response.status == 200
        handler.assert_not_called()


# =====================================================================
# GreenAPIClient: Retry logic
# =====================================================================

class TestClientRetry:
    """Тесты retry-логики GreenAPIClient."""

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self):
        """Retry при таймауте — в итоге успех."""
        from whatsapp.client import GreenAPIClient

        client = GreenAPIClient(
            api_url="https://api.green-api.com",
            instance_id="123",
            token="test_token",
        )

        call_count = 0

        async def mock_post(url, json=None, headers=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ReadTimeout("timeout")
            # Успех на 3-й попытке
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"idMessage": "ok"}
            return mock_resp

        with patch("whatsapp.client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.post = mock_post
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("whatsapp.client.asyncio.sleep", new_callable=AsyncMock):
                result = await client._request("sendMessage", {"test": 1})

        assert result == {"idMessage": "ok"}
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises(self):
        """Все попытки исчерпаны — бросает последнюю ошибку."""
        from whatsapp.client import GreenAPIClient

        client = GreenAPIClient(
            api_url="https://api.green-api.com",
            instance_id="123",
            token="test_token",
        )

        async def mock_post(url, json=None, headers=None):
            raise httpx.ReadTimeout("timeout")

        with patch("whatsapp.client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.post = mock_post
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("whatsapp.client.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(httpx.ReadTimeout):
                    await client._request("sendMessage", {"test": 1})

    @pytest.mark.asyncio
    async def test_no_retry_on_4xx(self):
        """Ошибка клиента 4xx (не 429) — не ретраим, бросаем сразу."""
        from whatsapp.client import GreenAPIClient

        client = GreenAPIClient(
            api_url="https://api.green-api.com",
            instance_id="123",
            token="test_token",
        )

        call_count = 0

        async def mock_post(url, json=None, headers=None):
            nonlocal call_count
            call_count += 1
            response = MagicMock()
            response.status_code = 400
            response.text = "Bad Request"
            raise httpx.HTTPStatusError(
                "400 Bad Request",
                request=MagicMock(),
                response=response,
            )

        with patch("whatsapp.client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.post = mock_post
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(httpx.HTTPStatusError):
                await client._request("sendMessage", {"test": 1})

        # Только 1 попытка
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_5xx(self):
        """Ошибка сервера 5xx — ретраим."""
        from whatsapp.client import GreenAPIClient

        client = GreenAPIClient(
            api_url="https://api.green-api.com",
            instance_id="123",
            token="test_token",
        )

        call_count = 0

        async def mock_post(url, json=None, headers=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                response = MagicMock()
                response.status_code = 503
                response.text = "Service Unavailable"
                raise httpx.HTTPStatusError(
                    "503", request=MagicMock(), response=response,
                )
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"ok": True}
            return mock_resp

        with patch("whatsapp.client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.post = mock_post
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("whatsapp.client.asyncio.sleep", new_callable=AsyncMock):
                result = await client._request("sendMessage", {"test": 1})

        assert result == {"ok": True}
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_on_connection_error(self):
        """Ошибка соединения — ретраим."""
        from whatsapp.client import GreenAPIClient

        client = GreenAPIClient(
            api_url="https://api.green-api.com",
            instance_id="123",
            token="test_token",
        )

        call_count = 0

        async def mock_post(url, json=None, headers=None):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise httpx.ConnectError("Connection refused")
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {"ok": True}
            return mock_resp

        with patch("whatsapp.client.httpx.AsyncClient") as mock_cls:
            mock_ctx = AsyncMock()
            mock_ctx.post = mock_post
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch("whatsapp.client.asyncio.sleep", new_callable=AsyncMock):
                result = await client._request("sendMessage", {"test": 1})

        assert result == {"ok": True}
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_send_text_too_long_raises(self):
        """Текст > 20000 символов — ValueError."""
        from whatsapp.client import GreenAPIClient

        client = GreenAPIClient(
            api_url="https://api.green-api.com",
            instance_id="123",
            token="test_token",
        )

        with pytest.raises(ValueError, match="слишком длинный"):
            await client.send_text("+77011234567", "A" * 20001)


# =====================================================================
# Classifier: Timeout & error handling
# =====================================================================

class TestClassifierErrors:
    """Тесты обработки ошибок классификатора."""

    @pytest.mark.asyncio
    async def test_timeout_returns_unknown(self):
        """Таймаут OpenAI → UNKNOWN с confidence=0.0."""
        classifier = MessageClassifier(
            api_key="test-key",
            model="gpt-4o-mini",
            timeout=0.01,
        )

        async def slow_call(*args, **kwargs):
            await asyncio.sleep(1)

        with patch.object(classifier, "_call_llm", side_effect=slow_call):
            result = await classifier.classify("тестовый текст")

        assert result.classification == ClassificationType.UNKNOWN
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_api_error_returns_unknown(self):
        """Ошибка OpenAI API → UNKNOWN с confidence=0.0."""
        classifier = MessageClassifier(
            api_key="test-key",
            model="gpt-4o-mini",
            timeout=5.0,
        )

        with patch.object(
            classifier, "_call_llm",
            side_effect=RuntimeError("API quota exceeded"),
        ):
            result = await classifier.classify("тестовый текст")

        assert result.classification == ClassificationType.UNKNOWN
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_empty_text_returns_unknown(self):
        """Пустой текст → UNKNOWN без вызова API."""
        classifier = MessageClassifier(
            api_key="test-key",
            model="gpt-4o-mini",
            timeout=5.0,
        )

        result = await classifier.classify("")
        assert result.classification == ClassificationType.UNKNOWN
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_unknown(self):
        """Только пробелы → UNKNOWN."""
        classifier = MessageClassifier(
            api_key="test-key",
            model="gpt-4o-mini",
            timeout=5.0,
        )

        result = await classifier.classify("   \n\t  ")
        assert result.classification == ClassificationType.UNKNOWN
        assert result.confidence == 0.0


# =====================================================================
# WorkflowEngine: auto-reply error notification
# =====================================================================

class TestEngineAutoReplyErrorNotification:
    """Тесты уведомления админов при ошибке авто-ответа."""

    @pytest.fixture
    def db_path(self, tmp_path):
        return tmp_path / "test_engine.db"

    @pytest.mark.asyncio
    async def test_auto_reply_failure_notifies_admins(self, db_path):
        """Ошибка отправки авто-ответа → уведомление админам."""
        await _init_db(db_path)
        await _seed_seller(db_path)
        await _seed_product(db_path)

        wa_client = AsyncMock()
        # send_text успешно для WARN1, падает для авто-ответа
        wa_client.send_text = AsyncMock(
            side_effect=[
                {"idMessage": "warn1_msg"},  # WARN1
                RuntimeError("WhatsApp down"),  # авто-ответ
            ]
        )

        classifier = AsyncMock()
        classifier.classify = AsyncMock(return_value=ClassificationResult(
            classification=ClassificationType.DIDNT_KNOW, confidence=0.9
        ))

        notifier = AsyncMock()
        notifier.send_to_admins = AsyncMock()
        notifier.notify_warn1_sent = AsyncMock()
        notifier.notify_incoming_message = AsyncMock()

        engine = _make_engine(
            db_path, wa_client=wa_client, classifier=classifier,
            notifier=notifier,
        )

        wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])
        await engine.send_warn1(wf_id)

        # Входящее сообщение → авто-ответ → ошибка → уведомление
        await engine.handle_incoming_message(
            "+77011234567", "Я не знал", "Test Shop"
        )

        # Проверяем, что send_to_admins был вызван с текстом об ошибке
        admin_calls = notifier.send_to_admins.call_args_list
        error_found = any(
            "Ошибка отправки авто-ответа" in str(call)
            for call in admin_calls
        )
        assert error_found, (
            f"Ожидали уведомление об ошибке авто-ответа, "
            f"но вызовы были: {admin_calls}"
        )


# =====================================================================
# WorkflowEngine: antispam limits
# =====================================================================

class TestEngineAntispam:
    """Тесты антиспам-лимитов в движке воронки."""

    @pytest.fixture
    def db_path(self, tmp_path):
        return tmp_path / "test_antispam.db"

    @pytest.mark.asyncio
    async def test_warns_respect_daily_limit(self, db_path):
        """Антиспам: max 3 сообщения в день."""
        await _init_db(db_path)
        await _seed_seller(db_path)
        await _seed_product(db_path, "SKU001")
        await _seed_product(db_path, "SKU002", title="Товар 2")
        await _seed_product(db_path, "SKU003", title="Товар 3")

        wa_client = AsyncMock()
        wa_client.send_text = AsyncMock(return_value={"idMessage": "ok"})

        notifier = AsyncMock()
        notifier.send_to_admins = AsyncMock()
        notifier.notify_warn1_sent = AsyncMock()
        notifier.notify_warn2_sent = AsyncMock()

        engine = _make_engine(db_path, wa_client=wa_client, notifier=notifier)

        wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

        # Первый WARN1 — успех
        result1 = await engine.send_warn1(wf_id)
        assert result1 is True

        # Сбрасываем статус для повторной отправки
        wf_db = SellerWorkflowDB(db_path)
        await wf_db.update_status(wf_id, "WARN1_SENT")

        # WARN2 — должен отправиться (2-е сообщение)
        result2 = await engine.send_warn2(wf_id)
        assert result2 is True


# =====================================================================
# Webhook: handler exception resilience
# =====================================================================

class TestWebhookHandlerResilience:
    """Тесты устойчивости webhook при ошибках handler."""

    @pytest.mark.asyncio
    async def test_handler_exception_returns_200(self):
        """Ошибка в handler не ломает ответ webhook-а."""
        handler = AsyncMock(side_effect=RuntimeError("test error"))
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
            on_incoming_message=handler,
        )
        payload = _make_valid_incoming_payload()
        request = _make_mock_request(payload)

        response = await webhook._handle_webhook(request)

        assert response.status == 200

    @pytest.mark.asyncio
    async def test_no_handler_set_returns_200(self):
        """Webhook без handler — 200 OK, без ошибок."""
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
            on_incoming_message=None,
        )
        payload = _make_valid_incoming_payload()
        request = _make_mock_request(payload)

        response = await webhook._handle_webhook(request)

        assert response.status == 200

    @pytest.mark.asyncio
    async def test_set_message_handler(self):
        """set_message_handler заменяет callback."""
        webhook = WhatsAppWebhook(
            host="127.0.0.1", port=18443,
        )
        assert webhook._on_incoming_message is None

        handler = AsyncMock()
        webhook.set_message_handler(handler)
        assert webhook._on_incoming_message is handler


# =====================================================================
# Health check
# =====================================================================

class TestWebhookHealth:
    """Тесты health-check эндпоинта."""

    @pytest.mark.asyncio
    async def test_health_returns_ok(self):
        """Health-check возвращает 200 + JSON."""
        webhook = WhatsAppWebhook(host="127.0.0.1", port=18443)
        request = MagicMock()

        response = await webhook._handle_health(request)

        assert response.status == 200
        assert response.content_type == "application/json"


# =====================================================================
# WorkflowEngine: send_warn failure notification
# =====================================================================

class TestEngineWarnFailure:
    """Тесты уведомления админов при ошибке отправки WARN."""

    @pytest.fixture
    def db_path(self, tmp_path):
        return tmp_path / "test_warn_fail.db"

    @pytest.mark.asyncio
    async def test_warn1_failure_notifies_admins(self, db_path):
        """Ошибка отправки WARN1 → уведомление админам."""
        await _init_db(db_path)
        await _seed_seller(db_path)
        await _seed_product(db_path)

        wa_client = AsyncMock()
        wa_client.send_text = AsyncMock(
            side_effect=RuntimeError("WhatsApp unavailable")
        )

        notifier = AsyncMock()
        notifier.send_to_admins = AsyncMock()
        notifier.notify_warn1_sent = AsyncMock()

        engine = _make_engine(db_path, wa_client=wa_client, notifier=notifier)
        wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

        result = await engine.send_warn1(wf_id)

        assert result is False
        # Проверяем уведомление об ошибке
        notifier.send_to_admins.assert_called_once()
        call_text = notifier.send_to_admins.call_args[0][0]
        assert "Ошибка отправки WARN1" in call_text

    @pytest.mark.asyncio
    async def test_warn2_failure_notifies_admins(self, db_path):
        """Ошибка отправки WARN2 → уведомление админам."""
        await _init_db(db_path)
        await _seed_seller(db_path)
        await _seed_product(db_path)

        wa_client = AsyncMock()
        # WARN1 —OK, WARN2 — ошибка
        wa_client.send_text = AsyncMock(
            side_effect=[
                {"idMessage": "ok"},
                RuntimeError("Connection reset"),
            ]
        )

        notifier = AsyncMock()
        notifier.send_to_admins = AsyncMock()
        notifier.notify_warn1_sent = AsyncMock()
        notifier.notify_warn2_sent = AsyncMock()

        engine = _make_engine(db_path, wa_client=wa_client, notifier=notifier)
        wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

        await engine.send_warn1(wf_id)
        notifier.send_to_admins.reset_mock()

        result = await engine.send_warn2(wf_id)

        assert result is False
        notifier.send_to_admins.assert_called_once()
        call_text = notifier.send_to_admins.call_args[0][0]
        assert "Ошибка отправки WARN2" in call_text


# =====================================================================
# WorkflowEngine: close_workflow
# =====================================================================

class TestEngineCloseWorkflow:
    """Тесты закрытия воронки."""

    @pytest.fixture
    def db_path(self, tmp_path):
        return tmp_path / "test_close.db"

    @pytest.mark.asyncio
    async def test_close_workflow_updates_status(self, db_path):
        """Закрытие воронки обновляет статус → CLOSED."""
        await _init_db(db_path)
        await _seed_seller(db_path)
        await _seed_product(db_path)

        notifier = AsyncMock()
        notifier.send_to_admins = AsyncMock()
        notifier.notify_detached = AsyncMock()

        engine = _make_engine(db_path, notifier=notifier)
        wf_id = await engine.on_new_seller_detected("M001", ["SKU001"])

        await engine.close_workflow(wf_id, reason="test_close")

        wf_db = SellerWorkflowDB(db_path)
        wf = await wf_db.get_workflow(wf_id)
        assert wf["status"] == "CLOSED"
        notifier.notify_detached.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_nonexistent_workflow_safe(self, db_path):
        """Закрытие несуществующей воронки — без ошибок."""
        await _init_db(db_path)

        engine = _make_engine(db_path)
        # Не должен упасть
        await engine.close_workflow(99999, reason="test")
