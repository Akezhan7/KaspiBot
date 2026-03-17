"""
Система миграций базы данных.
Отслеживает версию схемы и применяет ALTER TABLE / CREATE TABLE по порядку.
"""
import aiosqlite
from pathlib import Path
from typing import List, Tuple
import logging

logger = logging.getLogger(__name__)

# Каждая миграция — (версия, описание, SQL-запросы)
# SQL-запросы выполняются последовательно внутри одной транзакции.
MIGRATIONS: List[Tuple[int, str, List[str]]] = [
    (
        1,
        "Создание таблицы schema_version",
        [
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                description TEXT
            )
            """,
        ],
    ),
    (
        2,
        "Добавление whatsapp_available в sellers",
        [
            # Проверяем через try — если колонка уже есть, ALTER TABLE упадёт,
            # но мы ловим это ниже в apply_migration.
            "ALTER TABLE sellers ADD COLUMN whatsapp_available INTEGER DEFAULT NULL",
        ],
    ),
    (
        3,
        "Новые таблицы: seller_workflows, workflow_products, message_log, legal_requests",
        [
            # Эти таблицы уже создаются в schema.py через CREATE TABLE IF NOT EXISTS.
            # Миграция нужна для существующих БД, где init_db мог не создать их ранее.
            """
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
                notes TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS workflow_products (
                workflow_id INTEGER NOT NULL REFERENCES seller_workflows(id),
                product_id TEXT NOT NULL REFERENCES products(master_sku),
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                still_attached INTEGER DEFAULT 1,
                PRIMARY KEY (workflow_id, product_id)
            )
            """,
            """
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
            )
            """,
            """
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
            )
            """,
            # Индексы для новых таблиц
            "CREATE INDEX IF NOT EXISTS idx_seller_workflows_seller ON seller_workflows(seller_id)",
            "CREATE INDEX IF NOT EXISTS idx_seller_workflows_status ON seller_workflows(status)",
            "CREATE INDEX IF NOT EXISTS idx_seller_workflows_updated ON seller_workflows(updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_workflow_products_product ON workflow_products(product_id)",
            "CREATE INDEX IF NOT EXISTS idx_message_log_workflow ON message_log(workflow_id)",
            "CREATE INDEX IF NOT EXISTS idx_message_log_seller ON message_log(seller_id)",
            "CREATE INDEX IF NOT EXISTS idx_message_log_sent ON message_log(sent_at)",
            "CREATE INDEX IF NOT EXISTS idx_legal_requests_workflow ON legal_requests(workflow_id)",
            "CREATE INDEX IF NOT EXISTS idx_legal_requests_seller ON legal_requests(seller_id)",
            "CREATE INDEX IF NOT EXISTS idx_legal_requests_status ON legal_requests(control_purchase_status)",
        ],
    ),
]


class DatabaseMigrations:
    """Управление миграциями БД"""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def get_current_version(self) -> int:
        """Получить текущую версию схемы. 0 если таблица не создана."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Проверяем существование таблицы schema_version
                async with db.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name='schema_version'
                    """
                ) as cursor:
                    if not await cursor.fetchone():
                        return 0

                async with db.execute(
                    "SELECT MAX(version) FROM schema_version"
                ) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row and row[0] is not None else 0
        except Exception as e:
            logger.error(f"Ошибка получения версии схемы: {e}")
            return 0

    async def _apply_migration(
        self, db: aiosqlite.Connection, version: int, description: str, queries: List[str]
    ) -> bool:
        """Применить одну миграцию. Возвращает True при успехе."""
        try:
            for query in queries:
                try:
                    await db.execute(query)
                except aiosqlite.OperationalError as e:
                    error_msg = str(e).lower()
                    # Пропускаем «duplicate column» — колонка уже есть
                    if "duplicate column" in error_msg:
                        logger.debug(
                            f"Миграция v{version}: колонка уже существует, пропускаем"
                        )
                        continue
                    raise

            # Записываем версию
            await db.execute(
                """
                INSERT INTO schema_version (version, description)
                VALUES (?, ?)
                """,
                (version, description),
            )

            logger.info(f"Миграция v{version} применена: {description}")
            return True
        except Exception as e:
            logger.error(f"Ошибка миграции v{version} ({description}): {e}")
            raise

    async def run_migrations(self) -> int:
        """
        Применить все непримённые миграции.
        Возвращает количество применённых миграций.
        """
        current = await self.get_current_version()
        applied_count = 0

        pending = [
            (ver, desc, queries)
            for ver, desc, queries in MIGRATIONS
            if ver > current
        ]

        if not pending:
            logger.debug("Все миграции уже применены")
            return 0

        logger.info(
            f"Текущая версия схемы: {current}. "
            f"Ожидающих миграций: {len(pending)}"
        )

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")

            for version, description, queries in pending:
                # Миграция v1 создаёт саму таблицу schema_version,
                # поэтому её выполняем перед записью.
                await self._apply_migration(db, version, description, queries)
                applied_count += 1

            await db.commit()

        logger.info(f"Применено миграций: {applied_count}")
        return applied_count
