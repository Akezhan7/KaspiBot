"""
DAO для таблицы seller_workflows и workflow_products.
Управление воронкой продавцов: статусы, переходы, привязка товаров.
"""
import aiosqlite
from typing import List, Optional, Dict, Any
import logging

from config import now_kz_str

logger = logging.getLogger(__name__)

VALID_STATUSES = {
    "NEW_SELLER_ATTACH",
    "WARN1_SENT",
    "WARN2_SENT",
    "DIALOG_ACTIVE",
    "LEGAL_REQUEST_CREATED",
    "CONTROL_PURCHASE_REQUIRED",
    "READY_FOR_LAWSUIT",
    "DETACHED",
    "CLOSED",
    "RECIDIVE",
}


class SellerWorkflowDB:
    """Работа с таблицами seller_workflows и workflow_products"""

    def __init__(self, db_path):
        self.db_path = db_path

    async def create_workflow(self, seller_id: str) -> int:
        """
        Создать новый workflow для продавца.
        Возвращает workflow_id.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                cursor = await db.execute(
                    """
                    INSERT INTO seller_workflows (seller_id, status, created_at, updated_at)
                    VALUES (?, 'NEW_SELLER_ATTACH', ?, ?)
                    """,
                    (seller_id, now_kz_str(), now_kz_str()),
                )
                await db.commit()
                workflow_id = cursor.lastrowid
                logger.info(f"Создан workflow {workflow_id} для продавца {seller_id}")
                return workflow_id
        except Exception as e:
            logger.error(f"Ошибка создания workflow для {seller_id}: {e}")
            raise

    async def get_workflow(self, workflow_id: int) -> Optional[Dict[str, Any]]:
        """Получить workflow по ID"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    "SELECT * FROM seller_workflows WHERE id = ?",
                    (workflow_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error(f"Ошибка получения workflow {workflow_id}: {e}")
            raise

    async def get_active_workflow_for_seller(
        self, seller_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Получить активный workflow продавца.
        Активный = не CLOSED и не READY_FOR_LAWSUIT.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    """
                    SELECT * FROM seller_workflows
                    WHERE seller_id = ? AND status NOT IN ('CLOSED', 'READY_FOR_LAWSUIT')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (seller_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error(
                f"Ошибка получения активного workflow для {seller_id}: {e}"
            )
            raise

    async def update_status(self, workflow_id: int, new_status: str) -> bool:
        """
        Обновить статус workflow.
        Использует оптимистичную блокировку — обновляет updated_at.
        Возвращает True если обновление произошло.
        """
        if new_status not in VALID_STATUSES:
            raise ValueError(f"Недопустимый статус: {new_status}")
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")

                # Определяем дополнительные timestamp-поля
                now = now_kz_str()
                extra_set = ""
                params: list = [new_status, now]
                if new_status == "WARN1_SENT":
                    extra_set = ", warn1_sent_at = ?"
                    params.append(now)
                elif new_status == "WARN2_SENT":
                    extra_set = ", warn2_sent_at = ?"
                    params.append(now)
                elif new_status == "DETACHED":
                    extra_set = ", detached_at = ?"
                    params.append(now)
                elif new_status == "CLOSED":
                    extra_set = ", closed_at = ?"
                    params.append(now)
                params.append(workflow_id)

                cursor = await db.execute(
                    f"""
                    UPDATE seller_workflows
                    SET status = ?, updated_at = ?{extra_set}
                    WHERE id = ?
                    """,
                    params,
                )
                await db.commit()
                updated = cursor.rowcount > 0
                if updated:
                    logger.info(
                        f"Workflow {workflow_id} переведён в статус {new_status}"
                    )
                return updated
        except Exception as e:
            logger.error(
                f"Ошибка обновления статуса workflow {workflow_id}: {e}"
            )
            raise

    async def update_status_if(
        self, workflow_id: int, new_status: str, expected_status: str
    ) -> bool:
        """
        Оптимистичная блокировка: обновить статус только если текущий == expected_status.
        Возвращает True если обновление произошло (rows_affected > 0).
        """
        if new_status not in VALID_STATUSES:
            raise ValueError(f"Недопустимый статус: {new_status}")
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")

                now = now_kz_str()
                extra_set = ""
                params: list = [new_status, now]
                if new_status == "WARN1_SENT":
                    extra_set = ", warn1_sent_at = ?"
                    params.append(now)
                elif new_status == "WARN2_SENT":
                    extra_set = ", warn2_sent_at = ?"
                    params.append(now)
                elif new_status == "DETACHED":
                    extra_set = ", detached_at = ?"
                    params.append(now)
                elif new_status == "CLOSED":
                    extra_set = ", closed_at = ?"
                    params.append(now)
                params.extend([workflow_id, expected_status])

                cursor = await db.execute(
                    f"""
                    UPDATE seller_workflows
                    SET status = ?, updated_at = ?{extra_set}
                    WHERE id = ? AND status = ?
                    """,
                    params,
                )
                await db.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(
                f"Ошибка условного обновления workflow {workflow_id} "
                f"({expected_status} → {new_status}): {e}"
            )
            raise

    async def get_workflows_by_status(
        self, status: str
    ) -> List[Dict[str, Any]]:
        """Получить все workflow с указанным статусом"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    """
                    SELECT sw.*, s.merchant_name, s.phone
                    FROM seller_workflows sw
                    JOIN sellers s ON sw.seller_id = s.merchant_id
                    WHERE sw.status = ?
                    ORDER BY sw.updated_at ASC
                    """,
                    (status,),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Ошибка получения workflows по статусу {status}: {e}")
            raise

    async def get_workflows_for_escalation(
        self, status: str, older_than_hours: int
    ) -> List[Dict[str, Any]]:
        """
        Получить workflow с указанным статусом, которые не обновлялись
        дольше older_than_hours часов. Для автоэскалации.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    """
                    SELECT sw.*, s.merchant_name, s.phone
                    FROM seller_workflows sw
                    JOIN sellers s ON sw.seller_id = s.merchant_id
                    WHERE sw.status = ?
                      AND sw.updated_at <= datetime(?, ? || ' hours')
                    ORDER BY sw.updated_at ASC
                    """,
                    (status, now_kz_str(), f"-{older_than_hours}"),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(
                f"Ошибка получения workflows для эскалации "
                f"({status}, {older_than_hours}ч): {e}"
            )
            raise

    async def add_product_to_workflow(
        self, workflow_id: int, product_id: str
    ) -> None:
        """Привязать товар к workflow"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute(
                    """
                    INSERT OR IGNORE INTO workflow_products (workflow_id, product_id, detected_at)
                    VALUES (?, ?, ?)
                    """,
                    (workflow_id, product_id, now_kz_str()),
                )
                await db.commit()
                logger.debug(
                    f"Товар {product_id} привязан к workflow {workflow_id}"
                )
        except Exception as e:
            logger.error(
                f"Ошибка привязки товара {product_id} к workflow {workflow_id}: {e}"
            )
            raise

    async def get_workflow_products(
        self, workflow_id: int
    ) -> List[Dict[str, Any]]:
        """Получить товары, привязанные к workflow"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    """
                    SELECT wp.*, p.title, p.url, ps.first_seen
                    FROM workflow_products wp
                    JOIN products p ON wp.product_id = p.master_sku
                    JOIN seller_workflows sw ON wp.workflow_id = sw.id
                    LEFT JOIN product_sellers ps ON wp.product_id = ps.product_id AND sw.seller_id = ps.seller_id
                    WHERE wp.workflow_id = ?
                    ORDER BY wp.detected_at DESC
                    """,
                    (workflow_id,),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(
                f"Ошибка получения товаров workflow {workflow_id}: {e}"
            )
            raise

    async def update_product_attached(
        self, workflow_id: int, product_id: str, still_attached: int
    ) -> None:
        """Обновить флаг still_attached для товара в workflow"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute(
                    """
                    UPDATE workflow_products
                    SET still_attached = ?
                    WHERE workflow_id = ? AND product_id = ?
                    """,
                    (still_attached, workflow_id, product_id),
                )
                await db.commit()
        except Exception as e:
            logger.error(
                f"Ошибка обновления attached для {product_id} "
                f"в workflow {workflow_id}: {e}"
            )
            raise

    async def get_all_active_workflows(
        self, limit: int = 20, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Получить все активные workflow с пагинацией"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    """
                    SELECT sw.*, s.merchant_name, s.phone,
                           COUNT(wp.product_id) as products_count
                    FROM seller_workflows sw
                    JOIN sellers s ON sw.seller_id = s.merchant_id
                    LEFT JOIN workflow_products wp ON sw.id = wp.workflow_id
                    WHERE sw.status NOT IN ('CLOSED', 'READY_FOR_LAWSUIT')
                    GROUP BY sw.id
                    ORDER BY sw.updated_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Ошибка получения активных workflows: {e}")
            raise

    async def count_active_workflows(self) -> int:
        """Общее количество активных workflow"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    """
                    SELECT COUNT(*) FROM seller_workflows
                    WHERE status NOT IN ('CLOSED', 'READY_FOR_LAWSUIT')
                    """
                ) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
        except Exception as e:
            logger.error(f"Ошибка подсчёта активных workflows: {e}")
            raise

    async def has_closed_workflow(self, seller_id: str) -> bool:
        """
        Проверить, был ли продавец ранее в закрытом workflow.
        Используется для определения рецидива.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                async with db.execute(
                    """
                    SELECT COUNT(*) FROM seller_workflows
                    WHERE seller_id = ? AND status = 'CLOSED'
                    """,
                    (seller_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    return (row[0] if row else 0) > 0
        except Exception as e:
            logger.error(
                f"Ошибка проверки закрытых workflows для {seller_id}: {e}"
            )
            raise

    async def has_completed_workflow_recently(
        self, seller_id: str, cooldown_days: int = 30
    ) -> bool:
        """
        Проверить, есть ли у продавца завершённая воронка
        (CLOSED, READY_FOR_LAWSUIT, LEGAL_REQUEST_CREATED) за последние N дней.

        Если есть — новую воронку создавать не нужно.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                cutoff = now_kz_str()
                async with db.execute(
                    """
                    SELECT COUNT(*) FROM seller_workflows
                    WHERE seller_id = ?
                      AND status IN ('CLOSED', 'READY_FOR_LAWSUIT', 'LEGAL_REQUEST_CREATED')
                      AND updated_at >= datetime(?, ?)
                    """,
                    (seller_id, cutoff, f"-{cooldown_days} days"),
                ) as cursor:
                    row = await cursor.fetchone()
                    return (row[0] if row else 0) > 0
        except Exception as e:
            logger.error(
                f"Ошибка проверки недавних workflows для {seller_id}: {e}"
            )
            raise
