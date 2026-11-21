"""Delivery of CRM supplier responses to KaspiBot administrators."""
import asyncio
import logging
from html import escape
from pathlib import Path
from time import monotonic

import aiohttp
import aiosqlite
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


def format_supplier_response(event: dict) -> str:
    supplier = escape(event["supplierName"])
    order = escape(event["orderNumber"])
    counts = event["counts"]
    labels = (
        ("price", "Изменена цена"),
        ("quantity", "Изменено количество"),
        ("unavailable", "Нет в наличии"),
        ("replacement", "Предложена замена"),
    )
    summary = " · ".join(f"{label}: {counts[key]}" for key, label in labels if counts[key])
    lines = [
        f"<b>{supplier} · заявка {order}</b>",
        "Поставщик отправил ответ.",
        summary or "Цена и количество без изменений.",
    ]
    for change in event["priceChanges"]:
        name = escape(change["name"])
        before = escape(change["from"]).replace(".", ",")
        after = escape(change["to"]).replace(".", ",")
        lines.append(f"• {name} — {before} ₸ → {after} ₸")
    return "\n".join(lines)


class SupplierResponseNotifications:
    def __init__(self, bot: Bot, db_path: Path, url: str, token: str, admin_ids: list[int]):
        self.bot = bot
        self.db_path = db_path
        self.url = f"{url.rstrip('/')}/supplier-responses"
        self.token = token
        self.admin_ids = tuple(dict.fromkeys(admin_ids))
        self._telegram_resume_at = 0.0

    async def poll(self) -> None:
        """Process one batch. The scheduler runs only one copy of this job."""
        if not self.admin_ids or monotonic() < self._telegram_resume_at:
            return

        try:
            async with aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=aiohttp.ClientTimeout(total=5),
                raise_for_status=True,
            ) as session:
                async with session.get(self.url, allow_redirects=False) as response:
                    if response.status != 200:
                        logger.warning("CRM notification API returned HTTP %s", response.status)
                        return
                    batch = await response.json()
                for event in batch["notifications"]:
                    delay = await self._deliver(event)
                    result = {"retryAfterSeconds": delay} if delay else {}
                    result_url = f"{self.url}/{event['id']}/result"
                    async with session.post(result_url, json=result, allow_redirects=False) as response:
                        if response.status != 204:
                            logger.warning("CRM notification result returned HTTP %s", response.status)
                            return
                    if monotonic() < self._telegram_resume_at:
                        return
        except (aiohttp.ClientError, asyncio.TimeoutError) as error:
            logger.warning("CRM notification API unavailable: %s", type(error).__name__)

    async def _deliver(self, event: dict) -> int | None:
        event_id = event["id"]
        message = format_supplier_response(event)
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Открыть заявку в CRM", url=event["orderUrl"]),
        ]])
        delays = []

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT chat_id FROM crm_notification_deliveries WHERE event_id = ?",
                (event_id,),
            ) as cursor:
                sent = {row[0] for row in await cursor.fetchall()}
            for chat_id in self.admin_ids:
                if chat_id in sent:
                    continue
                try:
                    await self.bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode="HTML",
                        reply_markup=markup,
                        disable_notification=False,
                    )
                except TelegramRetryAfter as error:
                    self._telegram_resume_at = monotonic() + error.retry_after
                    logger.warning("Telegram rate limited CRM notification %s", event_id)
                    return max(10, min(error.retry_after, 3600))
                except TelegramAPIError as error:
                    delays.append(3600 if isinstance(error, TelegramForbiddenError) else 60)
                    logger.warning("CRM notification %s to admin %s failed: %s",
                                   event_id, chat_id, type(error).__name__)
                    continue

                await db.execute(
                    "INSERT INTO crm_notification_deliveries (event_id, chat_id) VALUES (?, ?)",
                    (event_id, chat_id),
                )
                await db.commit()

        return min(delays) if delays else None
