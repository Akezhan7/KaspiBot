"""
CRUD операции для работы с продавцами
"""
import aiosqlite
from typing import List, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class SellersDB:
    """Работа с таблицей sellers"""
    
    def __init__(self, db_path):
        self.db_path = db_path
    
    async def add_seller(
        self, 
        merchant_id: str, 
        merchant_name: str, 
        phone: Optional[str] = None
    ) -> bool:
        """
        Добавить нового продавца
        Возвращает True если продавец новый, False если уже существует
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute(
                    """
                    INSERT INTO sellers (merchant_id, merchant_name, phone)
                    VALUES (?, ?, ?)
                    """,
                    (merchant_id, merchant_name, phone)
                )
                await db.commit()
                logger.info(f"Добавлен продавец: {merchant_id} - {merchant_name}")
                return True
        except aiosqlite.IntegrityError:
            logger.debug(f"Продавец уже существует: {merchant_id}")
            return False
        except Exception as e:
            logger.error(f"Ошибка добавления продавца {merchant_id}: {e}")
            raise
    
    async def get_seller(self, merchant_id: str) -> Optional[Dict[str, Any]]:
        """Получить продавца по ID"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM sellers WHERE merchant_id = ?",
                    (merchant_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error(f"Ошибка получения продавца {merchant_id}: {e}")
            raise
    
    async def seller_exists(self, merchant_id: str) -> bool:
        """Проверить существование продавца"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute(
                    "SELECT 1 FROM sellers WHERE merchant_id = ?",
                    (merchant_id,)
                ) as cursor:
                    row = await cursor.fetchone()
                    return row is not None
        except Exception as e:
            logger.error(f"Ошибка проверки существования продавца {merchant_id}: {e}")
            raise
    
    async def update_phone(self, merchant_id: str, phone: str) -> None:
        """Обновить телефон продавца"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE sellers SET phone = ? WHERE merchant_id = ?",
                    (phone, merchant_id)
                )
                await db.commit()
                logger.info(f"Обновлен телефон для продавца {merchant_id}")
        except Exception as e:
            logger.error(f"Ошибка обновления телефона для {merchant_id}: {e}")
            raise
    
    async def get_all_sellers(self) -> List[Dict[str, Any]]:
        """Получить всех продавцов"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM sellers ORDER BY created_at DESC"
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Ошибка получения списка продавцов: {e}")
            raise
    
    async def get_all_sellers_with_product_count(self) -> List[Dict[str, Any]]:
        """
        Получить всех продавцов с количеством активных товаров
        
        Returns:
            List[{"merchant_id", "merchant_name", "phone", "created_at", "product_count"}]
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                
                query = """
                    SELECT 
                        s.*,
                        COUNT(ps.product_id) as product_count
                    FROM sellers s
                    LEFT JOIN product_sellers ps ON s.merchant_id = ps.seller_id 
                        AND ps.is_active = 1
                    GROUP BY s.merchant_id
                    HAVING product_count > 0
                    ORDER BY product_count DESC, s.merchant_name ASC
                """
                
                async with db.execute(query) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Ошибка получения продавцов с подсчетом: {e}")
            raise
    
    async def get_seller_with_products(self, merchant_id: str) -> Optional[Dict[str, Any]]:
        """
        Получить продавца со всеми его активными товарами
        
        Returns:
            {
                "merchant_id": str,
                "merchant_name": str,
                "phone": str,
                "created_at": str,
                "products": [{"product_id", "title", "url", "price", "last_seen"}]
            }
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                
                # Получаем информацию о продавце
                seller = await self.get_seller(merchant_id)
                if not seller:
                    return None
                
                # Получаем товары продавца
                query = """
                    SELECT 
                        p.master_sku as product_id,
                        p.title,
                        p.url,
                        ps.price,
                        ps.last_seen
                    FROM product_sellers ps
                    JOIN products p ON ps.product_id = p.master_sku
                    WHERE ps.seller_id = ? AND ps.is_active = 1
                    ORDER BY ps.last_seen DESC
                """
                
                async with db.execute(query, (merchant_id,)) as cursor:
                    products = await cursor.fetchall()
                    seller['products'] = [dict(row) for row in products]
                
                return seller
                
        except Exception as e:
            logger.error(f"Ошибка получения продавца с товарами {merchant_id}: {e}")
            raise
