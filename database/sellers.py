"""
CRUD операции для работы с продавцами
"""
import aiosqlite
from typing import List, Optional, Dict, Any
import logging

from config import now_kz_str

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
                    INSERT INTO sellers (merchant_id, merchant_name, phone, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (merchant_id, merchant_name, phone, now_kz_str())
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
            List[{
                "merchant_id", "merchant_name", "phone", "created_at",
                "product_count", "manual_products_sent_at",
                "manual_products_initial_count"
            }]
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                
                query = """
                    SELECT 
                        s.*,
                        COUNT(ps.product_id) as product_count,
                        sw.manual_products_sent_at,
                        sw.manual_products_initial_count
                    FROM sellers s
                    LEFT JOIN product_sellers ps ON s.merchant_id = ps.seller_id 
                        AND ps.is_active = 1
                    LEFT JOIN seller_workflows sw ON sw.id = (
                        SELECT sw2.id
                        FROM seller_workflows sw2
                        WHERE sw2.seller_id = s.merchant_id
                          AND sw2.status NOT IN ('CLOSED', 'DETACHED')
                        ORDER BY sw2.id DESC
                        LIMIT 1
                    )
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

    async def search_sellers(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """
        Найти активных продавцов по названию, merchant_id или телефону.

        Возвращает те же поля, что и get_all_sellers_with_product_count().
        """
        query = query.strip()
        if len(query) < 2:
            return []

        query_lower = query.lower()
        text_like = f"%{query_lower}%"
        prefix_like = f"{query_lower}%"

        phone_digits = "".join(ch for ch in query if ch.isdigit())
        alt_phone_digits = phone_digits
        if len(phone_digits) == 11 and phone_digits.startswith("8"):
            alt_phone_digits = "7" + phone_digits[1:]
        elif len(phone_digits) == 11 and phone_digits.startswith("7"):
            alt_phone_digits = "8" + phone_digits[1:]

        phone_like = f"%{phone_digits}%" if phone_digits else "__no_phone_match__"
        alt_phone_like = (
            f"%{alt_phone_digits}%"
            if alt_phone_digits and alt_phone_digits != phone_digits
            else "__no_phone_match__"
        )

        phone_expr = """
            REPLACE(
                REPLACE(
                    REPLACE(
                        REPLACE(
                            REPLACE(COALESCE(s.phone, ''), '+', ''),
                            ' ', ''
                        ),
                        '-', ''
                    ),
                    '(', ''
                ),
                ')', ''
            )
        """

        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row

                query_sql = f"""
                    WITH active_counts AS (
                        SELECT seller_id, COUNT(*) AS product_count
                        FROM product_sellers
                        WHERE is_active = 1
                        GROUP BY seller_id
                    )
                    SELECT
                        s.*,
                        ac.product_count,
                        sw.manual_products_sent_at,
                        sw.manual_products_initial_count
                    FROM sellers s
                    JOIN active_counts ac ON ac.seller_id = s.merchant_id
                    LEFT JOIN seller_workflows sw ON sw.id = (
                        SELECT sw2.id
                        FROM seller_workflows sw2
                        WHERE sw2.seller_id = s.merchant_id
                          AND sw2.status NOT IN ('CLOSED', 'DETACHED')
                        ORDER BY sw2.id DESC
                        LIMIT 1
                    )
                    WHERE LOWER(s.merchant_name) LIKE ?
                       OR LOWER(s.merchant_id) LIKE ?
                       OR {phone_expr} LIKE ?
                       OR {phone_expr} LIKE ?
                    ORDER BY
                        CASE
                            WHEN LOWER(s.merchant_name) = ? THEN 0
                            WHEN LOWER(s.merchant_id) = ? THEN 0
                            WHEN LOWER(s.merchant_name) LIKE ? THEN 1
                            WHEN LOWER(s.merchant_id) LIKE ? THEN 1
                            ELSE 2
                        END,
                        ac.product_count DESC,
                        s.merchant_name ASC
                    LIMIT ?
                """

                params = (
                    text_like,
                    text_like,
                    phone_like,
                    alt_phone_like,
                    query_lower,
                    query_lower,
                    prefix_like,
                    prefix_like,
                    limit,
                )

                async with db.execute(query_sql, params) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Ошибка поиска продавцов по запросу {query}: {e}")
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
                        ps.last_seen,
                        ps.first_seen
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
