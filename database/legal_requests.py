"""
DAO для таблицы legal_requests.
Управление юридическими заявками.
"""
import aiosqlite
from typing import List, Optional, Dict, Any
import logging

from config import now_kz_str

logger = logging.getLogger(__name__)


class LegalRequestsDB:
    """Работа с таблицей legal_requests"""

    def __init__(self, db_path):
        self.db_path = db_path

    async def create_request(
        self,
        workflow_id: int,
        seller_id: str,
        shop_name: Optional[str] = None,
        phone: Optional[str] = None,
        product_links: Optional[str] = None,
        detection_dates: Optional[str] = None,
        warn_timeline: Optional[str] = None,
        dialog_log: Optional[str] = None,
    ) -> int:
        """
        Создать юридическую заявку.
        product_links, detection_dates, warn_timeline — JSON-строки.
        Возвращает id заявки.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                cursor = await db.execute(
                    """
                    INSERT INTO legal_requests
                        (workflow_id, seller_id, shop_name, phone,
                         product_links, detection_dates, warn_timeline, dialog_log,
                         created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workflow_id,
                        seller_id,
                        shop_name,
                        phone,
                        product_links,
                        detection_dates,
                        warn_timeline,
                        dialog_log,
                        now_kz_str(),
                    ),
                )
                await db.commit()
                request_id = cursor.lastrowid
                logger.info(
                    f"Создана юрзаявка {request_id} для workflow {workflow_id}, "
                    f"seller {seller_id}"
                )
                return request_id
        except Exception as e:
            logger.error(
                f"Ошибка создания юрзаявки для workflow {workflow_id}: {e}"
            )
            raise

    async def get_request(self, request_id: int) -> Optional[Dict[str, Any]]:
        """Получить юрзаявку по ID"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    "SELECT * FROM legal_requests WHERE id = ?",
                    (request_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error(f"Ошибка получения юрзаявки {request_id}: {e}")
            raise

    async def get_request_by_workflow(
        self, workflow_id: int
    ) -> Optional[Dict[str, Any]]:
        """Получить юрзаявку по workflow_id"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    """
                    SELECT * FROM legal_requests
                    WHERE workflow_id = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (workflow_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error(
                f"Ошибка получения юрзаявки по workflow {workflow_id}: {e}"
            )
            raise

    async def update_purchase_info(
        self,
        request_id: int,
        bin_iin: Optional[str] = None,
        order_number: Optional[str] = None,
        notes: Optional[str] = None,
        documents: Optional[str] = None,
    ) -> None:
        """
        Обновить данные контрольной закупки.
        documents — JSON-строка с массивом путей к файлам.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute(
                    """
                    UPDATE legal_requests
                    SET bin_iin = COALESCE(?, bin_iin),
                        purchase_order_number = COALESCE(?, purchase_order_number),
                        purchase_notes = COALESCE(?, purchase_notes),
                        purchase_documents = COALESCE(?, purchase_documents),
                        control_purchase_status = 'COMPLETED',
                        completed_at = ?
                    WHERE id = ?
                    """,
                    (bin_iin, order_number, notes, documents, now_kz_str(), request_id),
                )
                await db.commit()
                logger.info(
                    f"Обновлены данные закупки для юрзаявки {request_id}"
                )
        except Exception as e:
            logger.error(
                f"Ошибка обновления закупки для юрзаявки {request_id}: {e}"
            )
            raise

    async def mark_ready_for_lawsuit(self, request_id: int) -> None:
        """Пометить заявку как готовую к подаче иска"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute(
                    """
                    UPDATE legal_requests
                    SET ready_for_lawsuit = 1
                    WHERE id = ?
                    """,
                    (request_id,),
                )
                await db.commit()
                logger.info(f"Юрзаявка {request_id} готова к подаче иска")
        except Exception as e:
            logger.error(
                f"Ошибка отметки готовности юрзаявки {request_id}: {e}"
            )
            raise

    async def assign_purchase(
        self, request_id: int, assigned_to: str
    ) -> None:
        """Назначить контрольную закупку"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute(
                    """
                    UPDATE legal_requests
                    SET assigned_to = ?, control_purchase_status = 'ASSIGNED'
                    WHERE id = ?
                    """,
                    (assigned_to, request_id),
                )
                await db.commit()
                logger.info(
                    f"Закупка для юрзаявки {request_id} назначена: {assigned_to}"
                )
        except Exception as e:
            logger.error(
                f"Ошибка назначения закупки для юрзаявки {request_id}: {e}"
            )
            raise

    async def get_pending_purchases(self) -> List[Dict[str, Any]]:
        """Получить заявки, ожидающие контрольную закупку"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    """
                    SELECT lr.*, s.merchant_name
                    FROM legal_requests lr
                    JOIN sellers s ON lr.seller_id = s.merchant_id
                    WHERE lr.control_purchase_status IN ('PENDING', 'ASSIGNED')
                    ORDER BY lr.created_at ASC
                    """
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Ошибка получения ожидающих закупок: {e}")
            raise

    async def get_all_requests(
        self, limit: int = 20, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Получить все юрзаявки с пагинацией"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    """
                    SELECT lr.*, s.merchant_name
                    FROM legal_requests lr
                    JOIN sellers s ON lr.seller_id = s.merchant_id
                    ORDER BY lr.created_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Ошибка получения списка юрзаявок: {e}")
            raise

    async def count_requests(self) -> int:
        """Общее количество юрзаявок"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    "SELECT COUNT(*) FROM legal_requests"
                ) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
        except Exception as e:
            logger.error(f"Ошибка подсчёта юрзаявок: {e}")
            raise
