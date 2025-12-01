"""
Система уведомлений Telegram
"""
import logging
from typing import List
from aiogram import Bot

from config import Config
from parser import NewSellerInfo
from .utils import format_new_seller_notification, format_grouped_notifications

logger = logging.getLogger(__name__)


class NotificationService:
    """Сервис отправки уведомлений"""
    
    def __init__(self, bot: Bot):
        self.bot = bot
        self.admin_ids = Config.ADMIN_USER_IDS
    
    async def send_to_admins(self, text: str, parse_mode: str = "HTML") -> None:
        """Отправить сообщение всем админам"""
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(admin_id, text, parse_mode=parse_mode)
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения админу {admin_id}: {e}")
    
    async def notify_new_sellers(self, sellers: List[NewSellerInfo]) -> None:
        """
        Отправить уведомления о новых продавцах
        
        Если 10+ новых - группирует в одно сообщение
        Если меньше - отправляет по отдельности
        """
        if not sellers:
            return
        
        count = len(sellers)
        logger.info(f"Отправка уведомлений о {count} новых продавцах")
        
        try:
            if count >= 10:
                # Групповое уведомление
                message = format_grouped_notifications(sellers)
                await self.send_to_admins(message)
            else:
                # Индивидуальные уведомления
                for seller in sellers:
                    message = format_new_seller_notification(seller)
                    await self.send_to_admins(message)
        
        except Exception as e:
            logger.error(f"Ошибка отправки уведомлений: {e}", exc_info=True)
    
    async def notify_scan_complete(
        self, 
        total: int, 
        successful: int, 
        failed: int, 
        new_sellers_count: int
    ) -> None:
        """Уведомление о завершении сканирования"""
        text = (
            "<b>Сканирование завершено</b>\n\n"
            f"Проверено: {successful}/{total}\n"
            f"Ошибок: {failed}\n"
            f"Новых продавцов: {new_sellers_count}"
        )
        
        await self.send_to_admins(text)
    
    async def notify_scan_error(self, error_msg: str) -> None:
        """Уведомление об ошибке сканирования"""
        text = (
            "<b>Ошибка сканирования</b>\n\n"
            f"{error_msg}"
        )
        
        await self.send_to_admins(text)
