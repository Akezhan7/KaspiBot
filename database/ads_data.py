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

    _INSERT_SQL = """
        INSERT OR REPLACE INTO ads_data
            (product_sku, product_name, scraped_at, period_start, period_end, source,
             impressions, clicks, ctr, spend, cpc,
             orders, revenue, bonus_active, bonus_percent, raw_data, period_days)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    @staticmethod
    def _row_tuple(d: dict) -> tuple:
        return (
            d["product_sku"],
            d.get("product_name") or None,
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
            int(d.get("period_days") or 7),
        )

    async def save_campaign(self, data: dict) -> int:
        """Сохранить одну запись рекламной кампании (UPSERT по дню). Возвращает id."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(self._INSERT_SQL, self._row_tuple(data))
            await db.commit()
            return cursor.lastrowid

    async def save_campaigns_batch(self, items: list[dict]) -> int:
        """Сохранить пакет записей (UPSERT по дню). Возвращает количество сохранённых."""
        if not items:
            return 0
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(self._INSERT_SQL, [self._row_tuple(d) for d in items])
            await db.commit()
        return len(items)

    async def get_latest_by_sku(
        self,
        sku: str,
        source: str | None = None,
        report_period: int | None = None,
    ) -> dict | None:
        """Последняя запись для данного SKU.

        Args:
            sku: артикул товара.
            source: фильтр по колонке source (опционально).
            report_period: фильтр по period_days (7 или 30). None = любой.
        """
        query = "SELECT * FROM ads_data WHERE product_sku = ?"
        params: list[Any] = [sku]
        if source:
            query += " AND source = ?"
            params.append(source)
        if report_period is not None:
            query += " AND period_days = ?"
            params.append(report_period)
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
        """Топ SKU по суммарным затратам за последние 30 дней (без дублей по дням)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                WITH deduped AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY product_sku, date(scraped_at), source
                               ORDER BY id DESC
                           ) AS rn
                    FROM ads_data
                    WHERE scraped_at >= datetime('now', '-30 days')
                )
                SELECT product_sku,
                       SUM(spend) AS total_spend,
                       SUM(clicks) AS total_clicks,
                       SUM(impressions) AS total_impressions,
                       AVG(ctr) AS avg_ctr,
                       AVG(cpc) AS avg_cpc
                FROM deduped
                WHERE rn = 1
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
        """Топ SKU по CTR за последние 30 дней (без дублей по дням)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                WITH deduped AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY product_sku, date(scraped_at), source
                               ORDER BY id DESC
                           ) AS rn
                    FROM ads_data
                    WHERE scraped_at >= datetime('now', '-30 days')
                      AND impressions > 0
                )
                SELECT product_sku,
                       AVG(ctr) AS avg_ctr,
                       SUM(clicks) AS total_clicks,
                       SUM(impressions) AS total_impressions,
                       SUM(spend) AS total_spend
                FROM deduped
                WHERE rn = 1
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

    async def get_all_recent_skus_with_names(
        self,
        period_days: int = 30,
    ) -> dict[str, str | None]:
        """Все уникальные SKU из ads_data за период + их самое свежее название.

        Используется чтобы расширить каталог `products` товарами, которые
        присутствуют в рекламном кабинете, но ещё не попали в основную
        таблицу products (обычно из-за того что каталог инициализируется
        парсером витрины отдельно и обновляется реже).

        Args:
            period_days: глубина истории в днях.

        Returns:
            Словарь {product_sku: product_name | None}. Берётся самое свежее
            непустое значение product_name по каждому SKU.
        """
        query = """
            WITH ranked AS (
                SELECT product_sku,
                       product_name,
                       ROW_NUMBER() OVER (
                           PARTITION BY product_sku
                           ORDER BY
                               CASE WHEN product_name IS NULL OR product_name = '' THEN 1 ELSE 0 END,
                               scraped_at DESC,
                               id DESC
                       ) AS rn
                FROM ads_data
                WHERE scraped_at >= datetime('now', ? || ' days')
            )
            SELECT product_sku, product_name
            FROM ranked
            WHERE rn = 1
        """
        result: dict[str, str | None] = {}
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, [f"-{period_days}"]) as cursor:
                async for row in cursor:
                    sku = row[0]
                    if not sku:
                        continue
                    result[sku] = row[1] or None
        return result

    async def get_active_skus_by_source(
        self,
        source: str,
        period_days: int = 30,
        require_active_bonus: bool = False,
        require_spend: bool = False,
        report_period: int | None = None,
    ) -> set[str]:
        """SKU, по которым есть свежие активные записи указанного источника.

        Args:
            source: значение колонки `ads_data.source`
                (`kaspi_marketing`, `kaspi_external_ads`, `kaspi_bonus_seller`,
                 `kaspi_bonus_review`, `kaspi_bonus`).
            period_days: глубина истории в днях (фильтр по scraped_at).
            require_active_bonus: True для бонусных источников — учитывать
                только записи с `bonus_active = 1`.
            require_spend: True для рекламных источников — учитывать только
                записи с `spend > 0` (= реклама реально крутилась).
            report_period: фильтр по period_days (7 или 30). None = любой
                report-период (берёт самые свежие данные независимо от выгрузки).

        Returns:
            Множество SKU. Используется для фильтров «есть/нет реклама/бонус».
        """
        conditions = [
            "source = ?",
            "scraped_at >= datetime('now', ? || ' days')",
        ]
        params: list[Any] = [source, f"-{period_days}"]

        if require_active_bonus:
            conditions.append("bonus_active = 1")
        if require_spend:
            conditions.append("spend > 0")
        if report_period is not None:
            conditions.append("period_days = ?")
            params.append(report_period)

        where = " AND ".join(conditions)
        query = f"""
            SELECT DISTINCT product_sku
            FROM ads_data
            WHERE {where}
        """

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params) as cursor:
                return {row[0] async for row in cursor if row[0]}

    async def get_spend_revenue_summary(
        self,
        period_days: int = 30,
        sku: str | None = None,
        report_period: int | None = None,
    ) -> list[dict]:
        """Свежайший XLSX-снапшот по каждому SKU.

        Семантика: каждая запись `ads_data` уже содержит агрегат за
        `period_days` дней (так его строит скрапер из отчёта Kaspi). Поэтому
        правильный «итог за period_days» — это **последний снапшот** по SKU,
        а не SUM по всем дням истории.

        Старая реализация суммировала `spend` по всем дневным снапшотам за
        `period_days` → искусственно надувала цифры в десятки раз.

        Args:
            period_days: глубина истории в днях (фильтр по scraped_at).
                Снапшот старше — игнорируется (товар, для которого парсинг
                давно не запускался, выпадает из агрегата).
            sku: ограничить выборку одним SKU.
            report_period: фильтр по period_days (7 или 30). None = берём
                самый свежий снапшот любого report-периода.

        Возвращает: product_sku, total_spend, total_revenue, total_clicks,
                    total_impressions, total_orders, avg_ctr, avg_cpc.
        Названия полей сохранены для совместимости с вызывающим кодом —
        фактически это значения из одной строки последнего снапшота.
        """
        sku_filter = "AND product_sku = ?" if sku else ""
        report_filter = "AND period_days = ?" if report_period is not None else ""
        params: list[Any] = [f"-{period_days}"]
        if sku:
            params.append(sku)
        if report_period is not None:
            params.append(report_period)

        query = f"""
            WITH ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY product_sku
                           ORDER BY scraped_at DESC, id DESC
                       ) AS rn
                FROM ads_data
                WHERE source = 'kaspi_marketing'
                  AND scraped_at >= datetime('now', ? || ' days')
                  {sku_filter}
                  {report_filter}
            )
            SELECT product_sku,
                   COALESCE(spend, 0)       AS total_spend,
                   COALESCE(revenue, 0)     AS total_revenue,
                   COALESCE(clicks, 0)      AS total_clicks,
                   COALESCE(impressions, 0) AS total_impressions,
                   COALESCE(orders, 0)      AS total_orders,
                   COALESCE(ctr, 0)         AS avg_ctr,
                   COALESCE(cpc, 0)         AS avg_cpc
            FROM ranked
            WHERE rn = 1
            ORDER BY total_spend DESC
        """

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                return [dict(row) async for row in cursor]

    async def get_daily_totals(self, start_date: str, end_date: str) -> list[dict]:
        """Суммарные метрики по всем SKU, сгруппированные по дням (без дублей).

        Используется агрегатором для дневных/недельных/месячных сводок.
        Возвращает: day (YYYY-MM-DD), total_spend, total_revenue, total_clicks,
                    total_impressions, total_orders, avg_ctr, avg_cpc, products_count
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                WITH deduped AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY product_sku, date(scraped_at)
                               ORDER BY id DESC
                           ) AS rn
                    FROM ads_data
                    WHERE source = 'kaspi_marketing'
                      AND scraped_at BETWEEN ? AND ?
                )
                SELECT date(scraped_at)   AS day,
                       SUM(spend)         AS total_spend,
                       SUM(revenue)       AS total_revenue,
                       SUM(clicks)        AS total_clicks,
                       SUM(impressions)   AS total_impressions,
                       SUM(orders)        AS total_orders,
                       AVG(ctr)           AS avg_ctr,
                       AVG(cpc)           AS avg_cpc,
                       COUNT(DISTINCT product_sku) AS products_count
                FROM deduped
                WHERE rn = 1
                GROUP BY date(scraped_at)
                ORDER BY day ASC
                """,
                (start_date, end_date),
            ) as cursor:
                return [dict(row) async for row in cursor]

    async def get_trends_by_sku(self, sku: str, days: int = 30) -> list[dict]:
        """Дневной тренд метрик для конкретного SKU (без дублей).

        Возвращает: day, spend, revenue, clicks, impressions, ctr, cpc
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                WITH deduped AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY product_sku, date(scraped_at)
                               ORDER BY id DESC
                           ) AS rn
                    FROM ads_data
                    WHERE product_sku = ?
                      AND source = 'kaspi_marketing'
                      AND scraped_at >= datetime('now', ? || ' days')
                )
                SELECT date(scraped_at)   AS day,
                       SUM(spend)         AS spend,
                       SUM(revenue)       AS revenue,
                       SUM(clicks)        AS clicks,
                       SUM(impressions)   AS impressions,
                       AVG(ctr)           AS ctr,
                       AVG(cpc)           AS cpc
                FROM deduped
                WHERE rn = 1
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
