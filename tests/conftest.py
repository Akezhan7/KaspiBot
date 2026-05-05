"""
Shared pytest configuration and fixtures for all test files.

Autouse patches:
- workflow.engine.asyncio.sleep → instantaneous (removes 5–10s WhatsApp send delays)
"""
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def patch_engine_sleep():
    """Заменяет asyncio.sleep внутри workflow.engine на мгновенную операцию.

    Без этого тесты, вызывающие send_warn1/send_warn2, спят 5–10 секунд
    из-за WHATSAPP_SEND_DELAY_MIN/MAX = 5/10 в engine.py.
    """
    with patch("workflow.engine.WHATSAPP_SEND_DELAY_MIN", 0), \
         patch("workflow.engine.WHATSAPP_SEND_DELAY_MAX", 0):
        yield
