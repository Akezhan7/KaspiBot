"""
DAO для таблиц рекламных данных и логов скрапинга.

Таблицы: ads_data, scrape_logs, browser_sessions.
"""
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from config import now_kz_str

logger = logging.getLogger(__name__)


class AdsDataDB:
    """CRUD для таблицы ads_data (рекламные метрики по SKU)."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def save_campaign(self, data: dict) -> int:
        """Сохранить одну запись рекламной кампании. Возвращает id."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO ads_data
                    (product_sku, scraped_at, period_start, period_end, source,
                     impressions, clicks, ctr, spend, cpc,
                     orders, revenue, bonus_active, bonus_percent, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data["product_sku"],
                    data.get("scraped_at", now_kz_str()),
                    data.get("period_start"),
                    data.get("period_end"),
                    data.get("source", "kaspi_marketing"),
                    data.get("impressions", 0),
                    data.get("clicks", 0),
                    data.get("ctr", 0),
                    data.get("spend", 0),
                    data.get("cpc", 0),
                    data.get("orders", 0),
                    data.get("revenue", 0),
                    data.get("bonus_active", 0),
                    data.get("bonus_percent", 0),
                    json.dumps(data.get("raw_data")) if data.get("raw_data") else None,
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def save_campaigns_batch(self, items: list[dict]) -> int:
        """Сохранить пакет записей. Возвращает количество вставленных."""
        if not items:
            return 0
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(
                """
                INSERT INTO ads_data
                    (product_sku, scraped_at, period_start, period_end, source,
                     impressions, clicks, ctr, spend, cpc,
                     orders, revenue, bonus_active, bonus_percent, raw_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        d["product_sku"],
                        d.get("scraped_at", now_kz_str()),
                        d.get("period_start"),
                        d.get("period_end"),
                        d.get("source", "kaspi_marketing"),
                        d.get("impressions", 0),
                        d.get("clicks", 0),
                        d.get("ctr", 0),
                        d.get("spend", 0),
                        d.get("cpc", 0),
                        d.get("orders", 0),
                        d.get("revenue", 0),
                        d.get("bonus_active", 0),
                        d.get("bonus_percent", 0),
                        json.dumps(d.get("raw_data")) if d.get("raw_data") else None,
                    )
                    for d in items
                ],
            )
            await db.commit()
            return len(items)

    async def get_latest_by_sku(self, sku: str, source: str | None = None) -> dict | None:
        """Последняя запись для данного SKU.

        Если указан source, выбирается последняя запись только по этому источнику.
        """
        query = "SELECT * FROM ads_data WHERE product_sku = ?"
        params: list[Any] = [sku]
        if source:
            query += " AND source = ?"
            params.append(source)
        query += " ORDER BY scraped_at DESC, id DESC LIMIT 1"

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                query,
                params,
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_campaigns_for_period(
        self,
        start: str,
        end: str,
        sku: str | None = None,
    ) -> list[dict]:
        """Записи за период. sku опционален."""
        query = "SELECT * FROM ads_data WHERE scraped_at BETWEEN ? AND ?"
        params: list[Any] = [start, end]
        if sku:
            query += " AND product_sku = ?"
            params.append(sku)
        query += " ORDER BY scraped_at DESC"

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                return [dict(row) async for row in cursor]

    async def get_top_spenders(self, limit: int = 20) -> list[dict]:
        """Топ SKU по суммарным затратам за последние 30 дней."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT product_sku,
                       SUM(spend) AS total_spend,
                       SUM(clicks) AS total_clicks,
                       SUM(impressions) AS total_impressions,
                       AVG(ctr) AS avg_ctr,
                       AVG(cpc) AS avg_cpc
                FROM ads_data
                WHERE scraped_at >= datetime('now', '-30 days')
                GROUP BY product_sku
                ORDER BY total_spend DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                return [dict(row) async for row in cursor]

    async def get_products_without_bonuses(self) -> list[dict]:
        """Товары без активных бонусов (по последним данным source='kaspi_bonus')."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                WITH ranked_bonus AS (
                    SELECT product_sku,
                           bonus_active,
                           bonus_percent,
                           scraped_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY product_sku
                               ORDER BY scraped_at DESC, id DESC
                           ) AS rn
                    FROM ads_data
                    WHERE source = 'kaspi_bonus'
                )
                SELECT product_sku,
                       bonus_active,
                       bonus_percent,
                       scraped_at
                FROM ranked_bonus
                WHERE rn = 1
                  AND bonus_active = 0
                ORDER BY product_sku
                """,
            ) as cursor:
                return [dict(row) async for row in cursor]

    async def get_most_clickable(self, limit: int = 20) -> list[dict]:
        """Топ SKU по CTR за последние 30 дней."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT product_sku,
                       AVG(ctr) AS avg_ctr,
                       SUM(clicks) AS total_clicks,
                       SUM(impressions) AS total_impressions,
                       SUM(spend) AS total_spend
                FROM ads_data
                WHERE scraped_at >= datetime('now', '-30 days')
                  AND impressions > 0
                GROUP BY product_sku
                ORDER BY avg_ctr DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                return [dict(row) async for row in cursor]

    async def get_bonuses_status(self) -> list[dict]:
        """Актуальный статус бонусов: последняя запись по каждому SKU из source='kaspi_bonus'."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                WITH ranked_bonus AS (
                    SELECT product_sku,
                           bonus_active,
                           bonus_percent,
                           scraped_at,
                           ROW_NUMBER() OVER (
                               PARTITION BY product_sku
                               ORDER BY scraped_at DESC, id DESC
                           ) AS rn
                    FROM ads_data
                    WHERE source = 'kaspi_bonus'
                )
                SELECT product_sku,
                       bonus_active,
                       bonus_percent,
                       scraped_at
                FROM ranked_bonus
                WHERE rn = 1
                ORDER BY product_sku
                """,
            ) as cursor:
                return [dict(row) async for row in cursor]

    async def get_spend_revenue_summary(
        self,
        period_days: int = 30,
        sku: str | None = None,
    ) -> list[dict]:
        """Агрегированные spend/revenue/clicks/impressions по SKU за период.

        Используется для расчёта ROI и ROAS в аналитическом процессоре.
        Возвращает: product_sku, total_spend, total_revenue, total_clicks,
                    total_impressions, total_orders, avg_ctr, avg_cpc
        """
        query = """
            SELECT product_sku,
                   SUM(spend)       AS total_spend,
                   SUM(revenue)     AS total_revenue,
                   SUM(clicks)      AS total_clicks,
                   SUM(impressions) AS total_impressions,
                   SUM(orders)      AS total_orders,
                   AVG(ctr)         AS avg_ctr,
                   AVG(cpc)         AS avg_cpc
            FROM ads_data
            WHERE source = 'kaspi_marketing'
              AND scraped_at >= datetime('now', ? || ' days')
        """
        params: list[Any] = [f"-{period_days}"]
        if sku:
            query += " AND product_sku = ?"
            params.append(sku)
        query += " GROUP BY product_sku ORDER BY total_spend DESC"

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                return [dict(row) async for row in cursor]

    async def get_daily_totals(self, start_date: str, end_date: str) -> list[dict]:
        """Суммарные метрики по всем SKU, сгруппированные по дням.

        Используется агрегатором для дневных/недельных/месячных сводок.
        Возвращает: day (YYYY-MM-DD), total_spend, total_revenue, total_clicks,
                    total_impressions, total_orders, avg_ctr, avg_cpc, products_count
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT date(scraped_at)   AS day,
                       SUM(spend)         AS total_spend,
                       SUM(revenue)       AS total_revenue,
                       SUM(clicks)        AS total_clicks,
                       SUM(impressions)   AS total_impressions,
                       SUM(orders)        AS total_orders,
                       AVG(ctr)           AS avg_ctr,
                       AVG(cpc)           AS avg_cpc,
                       COUNT(DISTINCT product_sku) AS products_count
                FROM ads_data
                WHERE source = 'kaspi_marketing'
                  AND scraped_at BETWEEN ? AND ?
                GROUP BY date(scraped_at)
                ORDER BY day ASC
                """,
                (start_date, end_date),
            ) as cursor:
                return [dict(row) async for row in cursor]

    async def get_trends_by_sku(self, sku: str, days: int = 30) -> list[dict]:
        """Дневной тренд метрик для конкретного SKU.

        Возвращает: day, spend, revenue, clicks, impressions, ctr, cpc
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT date(scraped_at)   AS day,
                       SUM(spend)         AS spend,
                       SUM(revenue)       AS revenue,
                       SUM(clicks)        AS clicks,
                       SUM(impressions)   AS impressions,
                       AVG(ctr)           AS ctr,
                       AVG(cpc)           AS cpc
                FROM ads_data
                WHERE product_sku = ?
                  AND source = 'kaspi_marketing'
                  AND scraped_at >= datetime('now', ? || ' days')
                GROUP BY date(scraped_at)
                ORDER BY day ASC
                """,
                (sku, f"-{days}"),
            ) as cursor:
                return [dict(row) async for row in cursor]


class ScrapeLogsDB:
    """CRUD для таблицы scrape_logs."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def create_log(self) -> int:
        """Создать запись о начале скрапинга. Возвращает id."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO scrape_logs (started_at, status) VALUES (?, 'running')",
                (now_kz_str(),),
            )
            await db.commit()
            return cursor.lastrowid

    async def update_log(
        self,
        log_id: int,
        status: str,
        products_scraped: int = 0,
        errors: str | None = None,
    ) -> None:
        """Обновить запись после завершения скрапинга."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE scrape_logs
                SET finished_at = ?, status = ?, products_scraped = ?, errors = ?
                WHERE id = ?
                """,
                (now_kz_str(), status, products_scraped, errors, log_id),
            )
            await db.commit()

    async def get_latest(self) -> dict | None:
        """Последний лог скрапинга."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM scrape_logs ORDER BY id DESC LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None


class BrowserSessionsDB:
    """CRUD для таблицы browser_sessions."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def create_session(self, storage_state_path: str) -> int:
        """Зарегистрировать новую сессию."""
        async with aiosqlite.connect(self.db_path) as db:
            # Инвалидировать предыдущие сессии
            await db.execute("UPDATE browser_sessions SET is_valid = 0")
            cursor = await db.execute(
                """
                INSERT INTO browser_sessions (storage_state_path, created_at, is_valid, last_used_at)
                VALUES (?, ?, 1, ?)
                """,
                (storage_state_path, now_kz_str(), now_kz_str()),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_active_session(self) -> dict | None:
        """Получить текущую активную сессию."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM browser_sessions WHERE is_valid = 1 ORDER BY id DESC LIMIT 1"
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def invalidate_all(self) -> None:
        """Инвалидировать все сессии."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE browser_sessions SET is_valid = 0")
            await db.commit()

    async def update_last_used(self, session_id: int) -> None:
        """Обновить время последнего использования."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE browser_sessions SET last_used_at = ? WHERE id = ?",
                (now_kz_str(), session_id),
            )
            await db.commit()
