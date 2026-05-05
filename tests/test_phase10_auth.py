"""
Тесты Фазы 10: Auth & Session Manager (KaspiAuthManager).

Покрывает (без Playwright):
- SMS-код: submit_sms_code / _wait_for_sms_code (asyncio.Event механизм)
- Notify callback: установка, вызов, обработка исключений
- ensure_authenticated: логика делегирования к is_session_valid / login
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from scraper.auth import KaspiAuthManager
from scraper.browser_manager import BrowserManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manager(has_context: bool = True) -> KaspiAuthManager:
    """Создать KaspiAuthManager с mock BrowserManager."""
    bm = MagicMock(spec=BrowserManager)
    bm.context = MagicMock() if has_context else None
    return KaspiAuthManager(bm)


# ===========================================================================
# Блок 1: SMS-код (asyncio.Event механизм)
# ===========================================================================

@pytest.mark.asyncio
async def test_submit_sms_code_sets_code_and_event():
    """submit_sms_code должен сохранить код и разбудить asyncio.Event."""
    manager = _make_manager()

    assert manager._sms_code is None
    assert not manager._sms_event.is_set()

    manager.submit_sms_code("112233")

    assert manager._sms_code == "112233"
    assert manager._sms_event.is_set()


@pytest.mark.asyncio
async def test_wait_for_sms_code_returns_submitted_code():
    """_wait_for_sms_code должен вернуть код, поданный через submit_sms_code."""
    manager = _make_manager()

    async def _submit_later():
        await asyncio.sleep(0.05)
        manager.submit_sms_code("998877")

    asyncio.create_task(_submit_later())
    code = await manager._wait_for_sms_code(timeout_seconds=3)

    assert code == "998877"


@pytest.mark.asyncio
async def test_wait_for_sms_code_timeout_returns_none():
    """_wait_for_sms_code с нулевым таймаутом должен вернуть None."""
    manager = _make_manager()
    # timeout=0 — asyncio немедленно отменяет ожидание
    code = await manager._wait_for_sms_code(timeout_seconds=0)
    assert code is None


@pytest.mark.asyncio
async def test_wait_clears_event_on_each_call():
    """_wait_for_sms_code очищает event и _sms_code перед ожиданием."""
    manager = _make_manager()
    # Вставляем «старый» код вручную
    manager._sms_code = "old"
    manager._sms_event.set()

    async def _submit_new():
        await asyncio.sleep(0.05)
        manager.submit_sms_code("new_code")

    asyncio.create_task(_submit_new())
    code = await manager._wait_for_sms_code(timeout_seconds=3)

    assert code == "new_code"


# ===========================================================================
# Блок 2: Notify callback
# ===========================================================================

@pytest.mark.asyncio
async def test_set_notify_callback_stores_reference():
    """set_notify_callback должен сохранить callback в _notify_callback."""
    manager = _make_manager()
    cb = AsyncMock()

    manager.set_notify_callback(cb)

    assert manager._notify_callback is cb


@pytest.mark.asyncio
async def test_notify_calls_callback_with_text():
    """_notify должен вызвать callback с переданным текстом."""
    manager = _make_manager()
    cb = AsyncMock()
    manager.set_notify_callback(cb)

    await manager._notify("тест уведомление")

    cb.assert_called_once_with("тест уведомление")


@pytest.mark.asyncio
async def test_notify_no_callback_does_not_raise():
    """_notify без установленного callback не должен бросать исключение."""
    manager = _make_manager()
    # callback не установлен — метод должен просто вернуться без ошибки
    await manager._notify("silent")  # should not raise


@pytest.mark.asyncio
async def test_notify_callback_exception_is_handled():
    """Если callback бросает исключение — _notify его поглощает."""
    manager = _make_manager()
    bad_cb = AsyncMock(side_effect=RuntimeError("callback exploded"))
    manager.set_notify_callback(bad_cb)

    # Не должно пробрасывать RuntimeError наружу
    await manager._notify("test")  # should not raise


# ===========================================================================
# Блок 3: ensure_authenticated
# ===========================================================================

@pytest.mark.asyncio
async def test_ensure_authenticated_valid_session_skips_login():
    """Если сессия валидна — ensure_authenticated возвращает True без вызова login."""
    manager = _make_manager()
    manager.is_session_valid = AsyncMock(return_value=True)
    manager.login = AsyncMock(return_value=True)

    result = await manager.ensure_authenticated()

    assert result is True
    manager.login.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_authenticated_invalid_session_calls_login():
    """Если сессия невалидна — ensure_authenticated вызывает login()."""
    manager = _make_manager()
    manager.is_session_valid = AsyncMock(return_value=False)
    manager.login = AsyncMock(return_value=True)

    result = await manager.ensure_authenticated()

    assert result is True
    manager.login.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_authenticated_login_failure_returns_false():
    """Если login() возвращает False — ensure_authenticated тоже False."""
    manager = _make_manager()
    manager.is_session_valid = AsyncMock(return_value=False)
    manager.login = AsyncMock(return_value=False)

    result = await manager.ensure_authenticated()

    assert result is False
