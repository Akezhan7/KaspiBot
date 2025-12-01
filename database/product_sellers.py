"""
CRUD операции для связи товар-продавец
"""
import aiosqlite
from typing import List, Optional, Dict, Any, Tuple
import logging

logger = logging.getLogger(__name__)


class ProductSellersDB:
    """Работа с таблицей product_sellers"""
    
    def __init__(self, db_path):
        self.db_path = db_path
    
    async def add_or_update_link(
        self, 
        product_id: str, 
        seller_id: str, 
        price: float
    ) -> Tuple[bool, bool]:
        """
        Добавить или обновить связь товар-продавец
        
        Returns:
            (is_new, was_inactive): 
            - is_new: True если связь новая
            - was_inactive: True если продавец вернулся после is_active=0
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                db.row_factory = aiosqlite.Row
                
                # Проверяем существование связи
                async with db.execute(
                    """
                    SELECT is_active FROM product_sellers 
                    WHERE product_id = ? AND seller_id = ?
                    """,
                    (product_id, seller_id)
                ) as cursor:
                    row = await cursor.fetchone()
                
                if row is None:
                    # Новая связь - INSERT
                    await db.execute(
                        """
                        INSERT INTO product_sellers 
                        (product_id, seller_id, price, is_active)
                        VALUES (?, ?, ?, 1)
                        """,
                        (product_id, seller_id, price)
                    )
                    await db.commit()
                    logger.info(f"Новая связь: товар={product_id}, продавец={seller_id}")
                    return (True, False)
                
                else:
                    # Существующая связь - UPDATE
                    was_inactive = row['is_active'] == 0
                    
                    await db.execute(
                        """
                        UPDATE product_sellers 
                        SET price = ?, 
                            last_seen = CURRENT_TIMESTAMP,
                            is_active = 1
                        WHERE product_id = ? AND seller_id = ?
                        """,
                        (price, product_id, seller_id)
                    )
                    await db.commit()
                    
                    if was_inactive:
                        logger.info(f"Продавец вернулся: товар={product_id}, продавец={seller_id}")
                    
                    return (False, was_inactive)
                    
        except Exception as e:
            logger.error(f"Ошибка добавления/обновления связи: {e}")
            raise
    
    async def deactivate_missing_sellers(
        self, 
        product_id: str, 
        active_seller_ids: List[str]
    ) -> int:
        """
        Деактивировать продавцов которых нет в новом списке
        
        Returns:
            Количество деактивированных записей
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                
                if active_seller_ids:
                    placeholders = ','.join('?' * len(active_seller_ids))
                    query = f"""
                        UPDATE product_sellers 
                        SET is_active = 0 
                        WHERE product_id = ? 
                        AND seller_id NOT IN ({placeholders})
                        AND is_active = 1
                    """
                    cursor = await db.execute(
                        query,
                        [product_id] + active_seller_ids
                    )
                else:
                    # Если нет активных продавцов - деактивировать всех
                    cursor = await db.execute(
                        """
                        UPDATE product_sellers 
                        SET is_active = 0 
                        WHERE product_id = ? AND is_active = 1
                        """,
                        (product_id,)
                    )
                
                await db.commit()
                count = cursor.rowcount
                
                if count > 0:
                    logger.info(f"Деактивировано {count} продавцов для товара {product_id}")
                
                return count
                
        except Exception as e:
            logger.error(f"Ошибка деактивации продавцов: {e}")
            raise
    
    async def get_sellers_for_product(
        self, 
        product_id: str, 
        active_only: bool = True
    ) -> List[Dict[str, Any]]:
        """Получить всех продавцов товара"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                
                query = """
                    SELECT ps.*, s.merchant_name, s.phone
                    FROM product_sellers ps
                    JOIN sellers s ON ps.seller_id = s.merchant_id
                    WHERE ps.product_id = ?
                """
                
                if active_only:
                    query += " AND ps.is_active = 1"
                
                query += " ORDER BY ps.last_seen DESC"
                
                async with db.execute(query, (product_id,)) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
                    
        except Exception as e:
            logger.error(f"Ошибка получения продавцов товара {product_id}: {e}")
            raise
    
    async def get_total_sellers_count(self) -> int:
        """Получить общее количество уникальных продавцов"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT COUNT(DISTINCT seller_id) FROM product_sellers WHERE is_active = 1"
                ) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
        except Exception as e:
            logger.error(f"Ошибка подсчета продавцов: {e}")
            raise
    
    async def get_active_links_count(self) -> int:
        """Получить количество активных связей"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT COUNT(*) FROM product_sellers WHERE is_active = 1"
                ) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
        except Exception as e:
            logger.error(f"Ошибка подсчета связей: {e}")
            raise
    
    async def get_other_products_for_seller(
        self, 
        seller_id: str, 
        exclude_product_id: str
    ) -> List[Dict[str, Any]]:
        """
        Получить другие товары, на которых продаёт этот продавец
        (исключая указанный товар)
        
        Returns:
            List[{"product_id": str, "title": str, "price": float}]
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                
                query = """
                    SELECT 
                        ps.product_id, 
                        p.title,
                        ps.price
                    FROM product_sellers ps
                    JOIN products p ON ps.product_id = p.master_sku
                    WHERE ps.seller_id = ? 
                    AND ps.product_id != ?
                    AND ps.is_active = 1
                    ORDER BY ps.last_seen DESC
                """
                
                async with db.execute(query, (seller_id, exclude_product_id)) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
                    
        except Exception as e:
            logger.error(f"Ошибка получения других товаров продавца {seller_id}: {e}")
            raise
