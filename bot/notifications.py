"""
Система уведомлений Telegram
"""
import logging
from typing import Any, Dict, List, Optional

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import Config
from parser import NewSellerInfo
from .utils import format_new_seller_notification, format_grouped_notifications

logger = logging.getLogger(__name__)


class NotificationService:
    """Сервис отправки уведомлений"""
    
    def __init__(self, bot: Bot):
        self.bot = bot
        self.admin_ids = Config.ADMIN_USER_IDS
    
    async def send_to_admins(
        self,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> None:
        """Отправить сообщение всем админам"""
        for admin_id in self.admin_ids:
            try:
                await self.bot.send_message(
                    admin_id,
                    text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                )
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения админу {admin_id}: {e}")
    
    async def notify_new_sellers(self, sellers: List[NewSellerInfo]) -> None:
        """
        Отправить уведомления о новых продавцах
        
        Если 30+ новых - группирует в одно сообщение
        Если меньше - отправляет по отдельности
        """
        if not sellers:
            return
        
        count = len(sellers)
        logger.info(f"Отправка уведомлений о {count} новых продавцах")
        
        try:
            if count >= 30:
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

    # ------------------------------------------------------------------
    # Уведомления воронки (Фаза 8.3)
    # ------------------------------------------------------------------

    async def notify_warn1_sent(
        self, workflow_id: int, seller: Dict[str, Any]
    ) -> None:
        """Уведомление: WARN1 отправлен"""
        text = (
            f"⚠️ <b>WARN1 отправлен</b>\n\n"
            f"Магазин: {seller.get('merchant_name', '?')}\n"
            f"Workflow: #{workflow_id}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Воронка",
                    callback_data=f"wf_view_{workflow_id}",
                ),
            ],
        ])
        await self.send_to_admins(text, reply_markup=keyboard)

    async def notify_warn2_sent(
        self, workflow_id: int, seller: Dict[str, Any]
    ) -> None:
        """Уведомление: WARN2 отправлен"""
        text = (
            f"⚠️⚠️ <b>WARN2 отправлен</b>\n\n"
            f"Магазин: {seller.get('merchant_name', '?')}\n"
            f"Workflow: #{workflow_id}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Воронка",
                    callback_data=f"wf_view_{workflow_id}",
                ),
                InlineKeyboardButton(
                    text="⚖️ Юрзаявка",
                    callback_data=f"wf_escalate_{workflow_id}",
                ),
            ],
        ])
        await self.send_to_admins(text, reply_markup=keyboard)

    async def notify_incoming_message(
        self,
        workflow_id: int,
        seller: Dict[str, Any],
        text_body: str,
        classification: str,
    ) -> None:
        """Уведомление: входящее сообщение от продавца"""
        text = (
            f"💬 <b>Ответ от продавца</b>\n\n"
            f"Магазин: {seller.get('merchant_name', '?')}\n"
            f"Тип: {classification}\n"
            f"Текст: {text_body[:200]}\n"
            f"Workflow: #{workflow_id}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Воронка",
                    callback_data=f"wf_view_{workflow_id}",
                ),
            ],
        ])
        await self.send_to_admins(text, reply_markup=keyboard)

    async def notify_legal_request(
        self,
        request_id: int,
        workflow_id: int,
        seller: Dict[str, Any],
        products_count: int,
        workflow: Dict[str, Any],
    ) -> None:
        """Уведомление: юрзаявка создана"""
        text = (
            f"⚖️ <b>Юридическая заявка #{request_id}</b>\n\n"
            f"Магазин: {seller.get('merchant_name', '?')}\n"
            f"Телефон: {seller.get('phone', 'нет')}\n"
            f"Товаров: {products_count}\n"
            f"WARN1: {workflow.get('warn1_sent_at', '—')}\n"
            f"WARN2: {workflow.get('warn2_sent_at', '—')}\n\n"
            f"Статус: Требуется контрольная закупка\n"
            f"👉 Назначить: /assign_purchase {request_id}"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Воронка",
                    callback_data=f"wf_view_{workflow_id}",
                ),
                InlineKeyboardButton(
                    text="📦 Экспорт",
                    callback_data=f"wf_export_{request_id}",
                ),
            ],
        ])
        await self.send_to_admins(text, reply_markup=keyboard)

    async def notify_purchase_required(
        self, request_id: int, seller: Dict[str, Any]
    ) -> None:
        """Уведомление: требуется контрольная закупка"""
        text = (
            f"🛒 <b>Требуется контрольная закупка</b>\n\n"
            f"Заявка: #{request_id}\n"
            f"Магазин: {seller.get('merchant_name', '?')}\n\n"
            f"👉 Назначить: /assign_purchase {request_id}"
        )
        await self.send_to_admins(text)

    async def notify_detached(
        self, workflow_id: int, seller: Dict[str, Any], reason: str = "detached"
    ) -> None:
        """Уведомление: продавец отсоединился"""
        text = (
            f"✅ <b>Продавец отсоединился</b>\n\n"
            f"Магазин: {seller.get('merchant_name', '?')}\n"
            f"Workflow: #{workflow_id}\n"
            f"Причина: {reason}"
        )
        await self.send_to_admins(text)
