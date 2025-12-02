"""
SQLite схема базы данных
"""
import aiosqlite
from pathlib import Path
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class DatabaseSchema:
    """Схема базы данных"""
    
    SCHEMA = """
    -- Таблица товаров
    CREATE TABLE IF NOT EXISTS products (
        master_sku TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        title TEXT,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_checked TIMESTAMP
    );
    
    -- Таблица продавцов
    CREATE TABLE IF NOT EXISTS sellers (
        merchant_id TEXT PRIMARY KEY,
        merchant_name TEXT NOT NULL,
        phone TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    
    -- Связь товар-продавец (многие ко многим)
    CREATE TABLE IF NOT EXISTS product_sellers (
        product_id TEXT NOT NULL,
        seller_id TEXT NOT NULL,
        price REAL NOT NULL,
        first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_active INTEGER DEFAULT 1,
        PRIMARY KEY (product_id, seller_id),
        FOREIGN KEY (product_id) REFERENCES products(master_sku) ON DELETE CASCADE,
        FOREIGN KEY (seller_id) REFERENCES sellers(merchant_id) ON DELETE CASCADE
    );
    
    -- Логи сканирования
    CREATE TABLE IF NOT EXISTS scan_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        finished_at TIMESTAMP,
        products_checked INTEGER DEFAULT 0,
        new_sellers INTEGER DEFAULT 0,
        errors TEXT
    );
    
    -- История новых продавцов (для команды /recent)
    CREATE TABLE IF NOT EXISTS recent_sellers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id TEXT NOT NULL,
        seller_id TEXT NOT NULL,
        price REAL NOT NULL,
        detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (product_id) REFERENCES products(master_sku) ON DELETE CASCADE,
        FOREIGN KEY (seller_id) REFERENCES sellers(merchant_id) ON DELETE CASCADE
    );
    
    -- Индексы для оптимизации
    CREATE INDEX IF NOT EXISTS idx_product_sellers_product ON product_sellers(product_id);
    CREATE INDEX IF NOT EXISTS idx_product_sellers_seller ON product_sellers(seller_id);
    CREATE INDEX IF NOT EXISTS idx_product_sellers_active ON product_sellers(is_active);
    CREATE INDEX IF NOT EXISTS idx_products_last_checked ON products(last_checked);
    CREATE INDEX IF NOT EXISTS idx_recent_sellers_detected ON recent_sellers(detected_at DESC);
    """
    
    @staticmethod
    async def init_db(db_path: Path) -> None:
        """
        Инициализация базы данных
        Создает все таблицы и индексы
        """
        try:
            # Создаем директорию если не существует
            db_path.parent.mkdir(parents=True, exist_ok=True)
            
            async with aiosqlite.connect(db_path) as db:
                # Включаем foreign keys
                await db.execute("PRAGMA foreign_keys = ON")
                
                # Выполняем схему
                await db.executescript(DatabaseSchema.SCHEMA)
                await db.commit()
                
                logger.info(f"База данных инициализирована: {db_path}")
                
        except Exception as e:
            logger.error(f"Ошибка инициализации БД: {e}")
            raise
    
    @staticmethod
    async def get_connection(db_path: Path) -> aiosqlite.Connection:
        """Получить подключение к БД с включенными foreign keys"""
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        return conn
