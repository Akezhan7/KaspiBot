"""
CRUD операции для истории новых продавцов
"""
import aiosqlite
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


class RecentSellersDB:
    """Работа с таблицей recent_sellers"""
    
    def __init__(self, db_path):
        self.db_path = db_path
    
    async def add_recent_seller(
        self, 
        product_id: str, 
        seller_id: str, 
        price: float
    ) -> bool:
        """Добавить запись о новом продавце"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute(
                    """
                    INSERT INTO recent_sellers (product_id, seller_id, price)
                    VALUES (?, ?, ?)
                    """,
                    (product_id, seller_id, price)
                )
                await db.commit()
                logger.debug(f"Добавлена история: товар={product_id}, продавец={seller_id}")
                return True
        except Exception as e:
            logger.error(f"Ошибка добавления истории: {e}")
            return False
    
    async def get_recent_sellers(
        self, 
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Получить последних новых продавцов
        
        Returns:
            List с полной информацией о товаре, продавце, цене
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                
                query = """
                    SELECT 
                        rs.id,
                        rs.product_id,
                        rs.seller_id,
                        rs.price,
                        rs.detected_at,
                        p.title as product_title,
                        s.merchant_name,
                        s.phone
                    FROM recent_sellers rs
                    JOIN products p ON rs.product_id = p.master_sku
                    JOIN sellers s ON rs.seller_id = s.merchant_id
                    ORDER BY rs.detected_at DESC
                    LIMIT ? OFFSET ?
                """
                
                async with db.execute(query, (limit, offset)) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
                    
        except Exception as e:
            logger.error(f"Ошибка получения истории: {e}")
            return []
    
    async def get_recent_count(self) -> int:
        """Получить общее количество записей в истории"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT COUNT(*) FROM recent_sellers"
                ) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
        except Exception as e:
            logger.error(f"Ошибка подсчета истории: {e}")
            return 0
    
    async def clear_old_records(self, days: int = 30) -> int:
        """
        Удалить записи старше N дней
        
        Returns:
            Количество удаленных записей
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    """
                    DELETE FROM recent_sellers 
                    WHERE detected_at < datetime('now', ?)
                    """,
                    (f'-{days} days',)
                )
                await db.commit()
                count = cursor.rowcount
                
                if count > 0:
                    logger.info(f"Удалено {count} старых записей из истории")
                
                return count
        except Exception as e:
            logger.error(f"Ошибка очистки истории: {e}")
            return 0
