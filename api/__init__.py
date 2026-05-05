"""
REST API для Telegram Mini App (TMA).

Запускается параллельно с Telegram-ботом и WhatsApp-webhook
на отдельном порту (TMA_API_PORT, по умолчанию 8080).

Валидация запросов: HMAC-SHA256 проверка Telegram WebApp initData.
Доступ: только пользователи из ADMIN_USER_IDS.
"""
from .server import TMAApiServer

__all__ = ["TMAApiServer"]
