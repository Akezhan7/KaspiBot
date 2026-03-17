"""
DAO для таблицы message_log.
Лог всех сообщений WhatsApp (входящие и исходящие).
"""
import aiosqlite
from typing import List, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class MessageLogDB:
    """Работа с таблицей message_log"""

    def __init__(self, db_path):
        self.db_path = db_path

    async def log_message(
        self,
        workflow_id: Optional[int],
        seller_id: str,
        direction: str,
        text: str,
        template_code: Optional[str] = None,
        wa_message_id: Optional[str] = None,
        classification: Optional[str] = None,
    ) -> int:
        """
        Записать сообщение в лог.
        direction: 'IN' (входящее) или 'OUT' (исходящее).
        Возвращает id записи.
        """
        if direction not in ("IN", "OUT"):
            raise ValueError(f"Недопустимое direction: {direction}")
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                cursor = await db.execute(
                    """
                    INSERT INTO message_log
                        (workflow_id, seller_id, direction, message_text,
                         template_code, wa_message_id, classification)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workflow_id,
                        seller_id,
                        direction,
                        text,
                        template_code,
                        wa_message_id,
                        classification,
                    ),
                )
                await db.commit()
                msg_id = cursor.lastrowid
                logger.debug(
                    f"Сообщение записано: id={msg_id}, workflow={workflow_id}, "
                    f"direction={direction}"
                )
                return msg_id
        except Exception as e:
            logger.error(
                f"Ошибка записи сообщения для seller {seller_id}: {e}"
            )
            raise

    async def get_messages_for_workflow(
        self, workflow_id: int
    ) -> List[Dict[str, Any]]:
        """Получить все сообщения для workflow, отсортированные по времени"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    """
                    SELECT * FROM message_log
                    WHERE workflow_id = ?
                    ORDER BY sent_at ASC
                    """,
                    (workflow_id,),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(
                f"Ошибка получения сообщений для workflow {workflow_id}: {e}"
            )
            raise

    async def get_messages_for_seller(
        self, seller_id: str
    ) -> List[Dict[str, Any]]:
        """Получить все сообщения для продавца"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    """
                    SELECT * FROM message_log
                    WHERE seller_id = ?
                    ORDER BY sent_at ASC
                    """,
                    (seller_id,),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(
                f"Ошибка получения сообщений для seller {seller_id}: {e}"
            )
            raise

    async def get_last_outgoing(
        self, workflow_id: int
    ) -> Optional[Dict[str, Any]]:
        """Получить последнее исходящее сообщение для workflow"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    """
                    SELECT * FROM message_log
                    WHERE workflow_id = ? AND direction = 'OUT'
                    ORDER BY sent_at DESC, id DESC
                    LIMIT 1
                    """,
                    (workflow_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error(
                f"Ошибка получения последнего исходящего для workflow {workflow_id}: {e}"
            )
            raise

    async def count_messages_today(
        self, seller_id: str, direction: str
    ) -> int:
        """
        Подсчёт сообщений за сегодня для продавца.
        Используется для антиспам-контроля.
        """
        if direction not in ("IN", "OUT"):
            raise ValueError(f"Недопустимое direction: {direction}")
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    """
                    SELECT COUNT(*) FROM message_log
                    WHERE seller_id = ?
                      AND direction = ?
                      AND date(sent_at) = date('now')
                    """,
                    (seller_id, direction),
                ) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
        except Exception as e:
            logger.error(
                f"Ошибка подсчёта сообщений за сегодня для {seller_id}: {e}"
            )
            raise
