"""
Bot модуль - Telegram bot handlers
"""
from .handlers import router
from .admin_handlers import admin_router
from .scraper_handlers import scraper_router, set_auth_manager, set_marketing_scraper
from .notifications import NotificationService
from .utils import format_new_seller_notification, format_grouped_notifications

__all__ = [
    'router',
    'admin_router',
    'scraper_router',
    'set_auth_manager',
    'set_marketing_scraper',
    'NotificationService',
    'format_new_seller_notification',
    'format_grouped_notifications',
]
