"""
Работа с логами сканирования
"""
import aiosqlite
from typing import Optional, Dict, Any, List
import logging

from config import now_kz_str

logger = logging.getLogger(__name__)


class ScanLogsDB:
    """Работа с таблицей scan_logs"""
    
    def __init__(self, db_path):
        self.db_path = db_path
    
    async def start_scan(self) -> int:
        """
        Создать запись о начале сканирования
        
        Returns:
            ID созданной записи
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "INSERT INTO scan_logs (started_at) VALUES (?)",
                    (now_kz_str(),),
                )
                await db.commit()
                scan_id = cursor.lastrowid
                logger.info(f"Начато сканирование ID={scan_id}")
                return scan_id
        except Exception as e:
            logger.error(f"Ошибка создания записи сканирования: {e}")
            raise
    
    async def finish_scan(
        self, 
        scan_id: int, 
        products_checked: int,
        new_sellers: int,
        errors: Optional[str] = None
    ) -> None:
        """Завершить сканирование с результатами"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    UPDATE scan_logs 
                    SET finished_at = ?,
                        products_checked = ?,
                        new_sellers = ?,
                        errors = ?
                    WHERE id = ?
                    """,
                    (now_kz_str(), products_checked, new_sellers, errors, scan_id)
                )
                await db.commit()
                logger.info(
                    f"Завершено сканирование ID={scan_id}: "
                    f"товаров={products_checked}, новых продавцов={new_sellers}"
                )
        except Exception as e:
            logger.error(f"Ошибка завершения сканирования {scan_id}: {e}")
            raise
    
    async def get_last_scan(self) -> Optional[Dict[str, Any]]:
        """Получить последнее сканирование"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT * FROM scan_logs 
                    ORDER BY started_at DESC 
                    LIMIT 1
                    """
                ) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
        except Exception as e:
            logger.error(f"Ошибка получения последнего сканирования: {e}")
            raise
    
    async def get_scan_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Получить историю сканирований"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT * FROM scan_logs 
                    ORDER BY started_at DESC 
                    LIMIT ?
                    """,
                    (limit,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Ошибка получения истории сканирований: {e}")
            raise
    
    async def get_total_scans(self) -> int:
        """Получить общее количество сканирований"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("SELECT COUNT(*) FROM scan_logs") as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
        except Exception as e:
            logger.error(f"Ошибка подсчета сканирований: {e}")
            raise
