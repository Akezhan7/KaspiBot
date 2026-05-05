"""
Data Aggregator — агрегация рекламных данных по временным периодам.

Формирует сводки (дневные / недельные / месячные) и тренды по SKU.
Используется API-сервером для эндпоинтов /api/summary/* и /api/ads/trends/*.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from config import now_kz

if TYPE_CHECKING:
    from database.ads_data import AdsDataDB
    from database.products import ProductsDB

logger = logging.getLogger(__name__)


def _safe_float(value) -> float:
    """Безопасное приведение к float (None → 0.0)."""
    return float(value) if value is not None else 0.0


def _safe_int(value) -> int:
    return int(value) if value is not None else 0


class DataAggregator:
    """Агрегация данных Kaspi Marketing по периодам и SKU."""

    def __init__(self, ads_db: "AdsDataDB", products_db: "ProductsDB") -> None:
        self._ads_db = ads_db
        self._products_db = products_db

    # -------------------------------------------------------------------------
    # Периодические сводки
    # -------------------------------------------------------------------------

    async def aggregate_daily(self, target_date: date | None = None) -> dict:
        """Сводка за один день.

        Если target_date не указана — используется сегодня (по Алматы).
        """
        if target_date is None:
            target_date = now_kz().date()

        start = target_date.isoformat()
        end = f"{target_date.isoformat()} 23:59:59"

        rows = await self._ads_db.get_daily_totals(start, end)
        if not rows:
            return self._empty_period_summary("daily", start, start)

        row = rows[0]
        return {
            "period": "daily",
            "date": start,
            "total_spend": round(_safe_float(row["total_spend"]), 2),
            "total_revenue": round(_safe_float(row["total_revenue"]), 2),
            "total_clicks": _safe_int(row["total_clicks"]),
            "total_impressions": _safe_int(row["total_impressions"]),
            "total_orders": _safe_int(row["total_orders"]),
            "avg_ctr": round(_safe_float(row["avg_ctr"]), 3),
            "avg_cpc": round(_safe_float(row["avg_cpc"]), 2),
            "products_count": _safe_int(row["products_count"]),
        }

    async def aggregate_weekly(self) -> dict:
        """Сводка за последние 7 дней (относительно текущего момента по Алматы)."""
        today = now_kz().date()
        week_ago = today - timedelta(days=7)
        return await self._aggregate_period("weekly", week_ago, today)

    async def aggregate_monthly(self) -> dict:
        """Сводка за последние 30 дней."""
        today = now_kz().date()
        month_ago = today - timedelta(days=30)
        return await self._aggregate_period("monthly", month_ago, today)

    async def get_trends(self, sku: str, days: int = 30) -> list[dict]:
        """Дневные тренды метрик для конкретного SKU.

        Возвращает список точек: day, spend, revenue, clicks, impressions, ctr, cpc.
        Заполняет пропущенные дни нулевыми значениями для построения непрерывного графика.
        """
        raw = await self._ads_db.get_trends_by_sku(sku, days=days)

        if not raw:
            return []

        # Заполняем пропущенные дни нулями
        raw_by_day: dict[str, dict] = {row["day"]: row for row in raw}

        today = now_kz().date()
        result: list[dict] = []

        for offset in range(days, -1, -1):
            target_day = (today - timedelta(days=offset)).isoformat()
            row = raw_by_day.get(target_day)
            if row:
                result.append(
                    {
                        "day": target_day,
                        "spend": round(_safe_float(row["spend"]), 2),
                        "revenue": round(_safe_float(row["revenue"]), 2),
                        "clicks": _safe_int(row["clicks"]),
                        "impressions": _safe_int(row["impressions"]),
                        "ctr": round(_safe_float(row["ctr"]), 3),
                        "cpc": round(_safe_float(row["cpc"]), 2),
                    }
                )
            else:
                result.append(
                    {
                        "day": target_day,
                        "spend": 0.0,
                        "revenue": 0.0,
                        "clicks": 0,
                        "impressions": 0,
                        "ctr": 0.0,
                        "cpc": 0.0,
                    }
                )

        return result

    async def get_total_stats(self) -> dict:
        """Общие метрики по всем товарам за последние 30 дней.

        Includes:
          total_spend, avg_cpc, avg_ctr,
          products_with_ads (уникальных SKU с рекламой),
          products_without_bonuses
        """
        summaries = await self._ads_db.get_spend_revenue_summary(period_days=30)
        no_bonus = await self._ads_db.get_products_without_bonuses()

        if not summaries:
            return {
                "period_days": 30,
                "total_spend": 0.0,
                "total_revenue": 0.0,
                "avg_cpc": 0.0,
                "avg_ctr": 0.0,
                "products_with_ads": 0,
                "products_without_bonuses": len(no_bonus),
            }

        total_spend = sum(_safe_float(r["total_spend"]) for r in summaries)
        total_revenue = sum(_safe_float(r["total_revenue"]) for r in summaries)
        avg_cpc = (
            sum(_safe_float(r["avg_cpc"]) for r in summaries) / len(summaries)
        )
        avg_ctr = (
            sum(_safe_float(r["avg_ctr"]) for r in summaries) / len(summaries)
        )

        return {
            "period_days": 30,
            "total_spend": round(total_spend, 2),
            "total_revenue": round(total_revenue, 2),
            "avg_cpc": round(avg_cpc, 2),
            "avg_ctr": round(avg_ctr, 3),
            "products_with_ads": len(summaries),
            "products_without_bonuses": len(no_bonus),
        }

    # -------------------------------------------------------------------------
    # Внутренние вспомогательные методы
    # -------------------------------------------------------------------------

    async def _aggregate_period(self, period_name: str, start: date, end: date) -> dict:
        """Агрегация за произвольный период (суммирование по дням)."""
        start_str = start.isoformat()
        end_str = f"{end.isoformat()} 23:59:59"

        rows = await self._ads_db.get_daily_totals(start_str, end_str)

        if not rows:
            return self._empty_period_summary(period_name, start_str, end.isoformat())

        total_spend = sum(_safe_float(r["total_spend"]) for r in rows)
        total_revenue = sum(_safe_float(r["total_revenue"]) for r in rows)
        total_clicks = sum(_safe_int(r["total_clicks"]) for r in rows)
        total_impressions = sum(_safe_int(r["total_impressions"]) for r in rows)
        total_orders = sum(_safe_int(r["total_orders"]) for r in rows)

        # Взвешенный средний CTR и CPC (по дням с ненулевыми показами)
        days_with_data = [r for r in rows if _safe_int(r["total_impressions"]) > 0]
        avg_ctr = (
            sum(_safe_float(r["avg_ctr"]) for r in days_with_data) / len(days_with_data)
            if days_with_data
            else 0.0
        )
        avg_cpc = (
            sum(_safe_float(r["avg_cpc"]) for r in days_with_data) / len(days_with_data)
            if days_with_data
            else 0.0
        )

        # Максимальное кол-во уникальных товаров за любой из дней
        max_products = max((_safe_int(r["products_count"]) for r in rows), default=0)

        return {
            "period": period_name,
            "date_from": start_str,
            "date_to": end.isoformat(),
            "days_with_data": len(rows),
            "total_spend": round(total_spend, 2),
            "total_revenue": round(total_revenue, 2),
            "total_clicks": total_clicks,
            "total_impressions": total_impressions,
            "total_orders": total_orders,
            "avg_ctr": round(avg_ctr, 3),
            "avg_cpc": round(avg_cpc, 2),
            "max_products_per_day": max_products,
        }

    @staticmethod
    def _empty_period_summary(period_name: str, date_from: str, date_to: str) -> dict:
        """Пустая сводка — возвращается когда данных за период нет."""
        return {
            "period": period_name,
            "date_from": date_from,
            "date_to": date_to,
            "days_with_data": 0,
            "total_spend": 0.0,
            "total_revenue": 0.0,
            "total_clicks": 0,
            "total_impressions": 0,
            "total_orders": 0,
            "avg_ctr": 0.0,
            "avg_cpc": 0.0,
            "max_products_per_day": 0,
        }
