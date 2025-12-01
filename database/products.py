"""
CRUD операции для работы с продуктами
"""
import aiosqlite
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ProductsDB:
    """Работа с таблицей products"""
    
    def __init__(self, db_path):
        self.db_path = db_path
    
    async def add_product(self, master_sku: str, url: str, title: Optional[str] = None) -> bool:
        """Добавить новый товар"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                await db.execute(
                    """
                    INSERT INTO products (master_sku, url, title)
                    VALUES (?, ?, ?)
                    """,
                    (master_sku, url, title)
                )
                await db.commit()
                logger.info(f"Добавлен товар: {master_sku}")
                return True
        except aiosqlite.IntegrityError:
            logger.warning(f"Товар уже существует: {master_sku}")
            return False
        except Exception as e:
            logger.error(f"Ошибка добавления товара {master_sku}: {e}")
            raise
    
    async def get_product(self, master_sku: str) -> Optional[Dict[str, Any]]:
        """Получить товар по SKU"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM products WHERE master_sku = ?",
                    (master_sku,)
                ) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error(f"Ошибка получения товара {master_sku}: {e}")
            raise
    
    async def get_all_products(self) -> List[Dict[str, Any]]:
        """Получить все товары"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM products ORDER BY added_at DESC"
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Ошибка получения списка товаров: {e}")
            raise
    
    async def delete_product(self, master_sku: str) -> bool:
        """Удалить товар (каскадно удалит связи)"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("PRAGMA foreign_keys = ON")
                cursor = await db.execute(
                    "DELETE FROM products WHERE master_sku = ?",
                    (master_sku,)
                )
                await db.commit()
                
                if cursor.rowcount > 0:
                    logger.info(f"Удален товар: {master_sku}")
                    return True
                else:
                    logger.warning(f"Товар не найден: {master_sku}")
                    return False
        except Exception as e:
            logger.error(f"Ошибка удаления товара {master_sku}: {e}")
            raise
    
    async def update_last_checked(self, master_sku: str) -> None:
        """Обновить время последней проверки"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    UPDATE products 
                    SET last_checked = CURRENT_TIMESTAMP 
                    WHERE master_sku = ?
                    """,
                    (master_sku,)
                )
                await db.commit()
        except Exception as e:
            logger.error(f"Ошибка обновления last_checked для {master_sku}: {e}")
            raise
    
    async def update_product_title(self, master_sku: str, title: str) -> None:
        """Обновить название товара"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    UPDATE products 
                    SET title = ? 
                    WHERE master_sku = ?
                    """,
                    (title, master_sku)
                )
                await db.commit()
                logger.debug(f"Обновлено название товара {master_sku}: {title[:50]}")
        except Exception as e:
            logger.error(f"Ошибка обновления названия для {master_sku}: {e}")
            raise
    
    async def get_products_count(self) -> int:
        """Получить количество товаров"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("SELECT COUNT(*) FROM products") as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
        except Exception as e:
            logger.error(f"Ошибка подсчета товаров: {e}")
            raise
