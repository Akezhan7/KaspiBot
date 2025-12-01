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
