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
    
    -- Воронка продавца (один продавец может быть в воронке по нескольким товарам)
    CREATE TABLE IF NOT EXISTS seller_workflows (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_id TEXT NOT NULL REFERENCES sellers(merchant_id),
        status TEXT NOT NULL DEFAULT 'NEW_SELLER_ATTACH',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        warn1_sent_at TIMESTAMP,
        warn2_sent_at TIMESTAMP,
        detached_at TIMESTAMP,
        closed_at TIMESTAMP,
        manual_products_sent_at TIMESTAMP,
        manual_products_initial_count INTEGER,
        notes TEXT
    );

    -- Привязка товаров к воронке
    CREATE TABLE IF NOT EXISTS workflow_products (
        workflow_id INTEGER NOT NULL REFERENCES seller_workflows(id),
        product_id TEXT NOT NULL REFERENCES products(master_sku),
        detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        still_attached INTEGER DEFAULT 1,
        PRIMARY KEY (workflow_id, product_id)
    );

    -- Лог всех сообщений (WhatsApp)
    CREATE TABLE IF NOT EXISTS message_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id INTEGER REFERENCES seller_workflows(id),
        seller_id TEXT NOT NULL REFERENCES sellers(merchant_id),
        direction TEXT NOT NULL,
        channel TEXT NOT NULL DEFAULT 'whatsapp',
        message_text TEXT NOT NULL,
        template_code TEXT,
        wa_message_id TEXT,
        classification TEXT,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Юридические заявки
    CREATE TABLE IF NOT EXISTS legal_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id INTEGER NOT NULL REFERENCES seller_workflows(id),
        seller_id TEXT NOT NULL REFERENCES sellers(merchant_id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        shop_name TEXT,
        phone TEXT,
        product_links TEXT,
        detection_dates TEXT,
        warn_timeline TEXT,
        dialog_log TEXT,
        bin_iin TEXT,
        purchase_order_number TEXT,
        purchase_notes TEXT,
        purchase_documents TEXT,
        control_purchase_status TEXT DEFAULT 'PENDING',
        ready_for_lawsuit INTEGER DEFAULT 0,
        assigned_to TEXT,
        completed_at TIMESTAMP
    );

    -- Индексы для оптимизации
    CREATE INDEX IF NOT EXISTS idx_product_sellers_product ON product_sellers(product_id);
    CREATE INDEX IF NOT EXISTS idx_product_sellers_seller ON product_sellers(seller_id);
    CREATE INDEX IF NOT EXISTS idx_product_sellers_active ON product_sellers(is_active);
    CREATE INDEX IF NOT EXISTS idx_products_last_checked ON products(last_checked);
    CREATE INDEX IF NOT EXISTS idx_recent_sellers_detected ON recent_sellers(detected_at DESC);

    -- Индексы для новых таблиц
    CREATE INDEX IF NOT EXISTS idx_seller_workflows_seller ON seller_workflows(seller_id);
    CREATE INDEX IF NOT EXISTS idx_seller_workflows_status ON seller_workflows(status);
    CREATE INDEX IF NOT EXISTS idx_seller_workflows_updated ON seller_workflows(updated_at);
    CREATE INDEX IF NOT EXISTS idx_workflow_products_product ON workflow_products(product_id);
    CREATE INDEX IF NOT EXISTS idx_message_log_workflow ON message_log(workflow_id);
    CREATE INDEX IF NOT EXISTS idx_message_log_seller ON message_log(seller_id);
    CREATE INDEX IF NOT EXISTS idx_message_log_sent ON message_log(sent_at);
    CREATE INDEX IF NOT EXISTS idx_legal_requests_workflow ON legal_requests(workflow_id);
    CREATE INDEX IF NOT EXISTS idx_legal_requests_seller ON legal_requests(seller_id);
    CREATE INDEX IF NOT EXISTS idx_legal_requests_status ON legal_requests(control_purchase_status);
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
