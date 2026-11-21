from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiohttp import web

from bot.crm_notifications import SupplierResponseNotifications
from database.migrations import DatabaseMigrations
from database.schema import DatabaseSchema


@pytest.mark.asyncio
async def test_crm_notification_retries_only_pending_admins_after_restart(tmp_path):
    db_path = tmp_path / "test.db"
    await DatabaseSchema.init_db(db_path)
    await DatabaseMigrations(db_path).run_migrations()

    event = {
        "id": "event-test",
        "supplierName": "Крепёж <Люкс>",
        "orderNumber": "1042",
        "orderUrl": "https://crm.example.test/orders/42",
        "counts": {"price": 1, "quantity": 0, "unavailable": 0, "replacement": 0},
        "priceChanges": [{"name": "Хомут", "from": "80.00", "to": "95.00"}],
    }
    results = []

    async def pending(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        return web.json_response({"notifications": [event]})

    async def result(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        results.append(await request.json())
        return web.Response(status=204)

    app = web.Application()
    app.router.add_get("/supplier-responses", pending)
    app.router.add_post("/supplier-responses/event-test/result", result)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}"
    send = AsyncMock(side_effect=[None, TelegramNetworkError(method=None, message="offline"), None])
    bot = SimpleNamespace(send_message=send)
    try:
        await SupplierResponseNotifications(bot, db_path, url, "test-token", [1, 2, 1]).poll()
        await SupplierResponseNotifications(bot, db_path, url, "test-token", [1, 2, 1]).poll()
        await SupplierResponseNotifications(bot, db_path, url, "test-token", [1, 2, 1]).poll()
        assert [call.kwargs["chat_id"] for call in send.await_args_list] == [1, 2, 2]
        assert results == [{"retryAfterSeconds": 60}, {}, {}]
        message = send.await_args_list[0].kwargs
        assert "&lt;Люкс&gt;" in message["text"]
        assert "Хомут" in message["text"]
        assert not message["disable_notification"]
        assert message["reply_markup"].inline_keyboard[0][0].url == event["orderUrl"]
    finally:
        await runner.cleanup()
