"""
Data Processor — расчёт аналитических показателей (ROI, ROAS, CPC efficiency).

Бизнес-логика работает поверх DAO-слоя: получает агрегированные данные
из AdsDataDB, ProductsDB, ProductSellersDB и вычисляет производные метрики.

Формулы:
  ROI   = (Revenue - Ad Spend) / Ad Spend * 100  (%)
  ROAS  = Revenue / Ad Spend                     (ratio)
  CPC efficiency = avg_product_price / cpc        (ratio: >1 = допустимо)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from database.ads_data import AdsDataDB
    from database.products import ProductsDB
    from database.product_sellers import ProductSellersDB

logger = logging.getLogger(__name__)

# Нижний порог ROAS: считается приемлемым, если ROAS >= этого значения
_MIN_ACCEPTABLE_ROAS = 1.0

# Минимальные затраты для включения SKU в расчёты (фильтр «нулевого шума»)
_MIN_SPEND_THRESHOLD = 1.0


class AdsAnalyticsProcessor:
    """Расчёт ключевых рекламных метрик на основе данных маркетинга."""

    def __init__(
        self,
        ads_db: "AdsDataDB",
        products_db: "ProductsDB",
        product_sellers_db: "ProductSellersDB",
    ) -> None:
        self._ads_db = ads_db
        self._products_db = products_db
        self._product_sellers_db = product_sellers_db

    # -------------------------------------------------------------------------
    # Метрики на уровне одного SKU
    # -------------------------------------------------------------------------

    async def calculate_roi(self, sku: str, period_days: int = 30) -> dict:
        """Рассчитать ROI для конкретного SKU за период.

        Returns dict с полями:
            sku, period_days, spend, revenue, roi_percent,
            has_revenue_data (False если revenue = 0)
        """
        rows = await self._ads_db.get_spend_revenue_summary(period_days=period_days, sku=sku)
        if not rows:
            return {
                "sku": sku,
                "period_days": period_days,
                "spend": 0.0,
                "revenue": 0.0,
                "roi_percent": None,
                "has_revenue_data": False,
            }

        row = rows[0]
        spend = float(row["total_spend"] or 0)
        revenue = float(row["total_revenue"] or 0)

        roi_percent: float | None = None
        if spend > _MIN_SPEND_THRESHOLD:
            roi_percent = (revenue - spend) / spend * 100.0

        return {
            "sku": sku,
            "period_days": period_days,
            "spend": spend,
            "revenue": revenue,
            "roi_percent": round(roi_percent, 2) if roi_percent is not None else None,
            "has_revenue_data": revenue > 0,
        }

    async def calculate_roas(self, sku: str, period_days: int = 30) -> float | None:
        """Рассчитать ROAS для конкретного SKU за период.

        Returns ROAS (float) или None если нет данных о затратах/выручке.
        """
        rows = await self._ads_db.get_spend_revenue_summary(period_days=period_days, sku=sku)
        if not rows:
            return None

        row = rows[0]
        spend = float(row["total_spend"] or 0)
        revenue = float(row["total_revenue"] or 0)

        if spend <= _MIN_SPEND_THRESHOLD:
            return None
        if revenue <= 0:
            return None

        return round(revenue / spend, 3)

    async def get_cpc_efficiency(self, sku: str) -> dict:
        """Рассчитать эффективность CPC: соотношение средней цены к стоимости клика.

        Если CPC efficiency > 1 — клик дешевле цены товара (положительный знак).
        Returns dict: sku, avg_cpc, avg_product_price, efficiency_ratio, assessment
        """
        rows = await self._ads_db.get_spend_revenue_summary(period_days=30, sku=sku)
        avg_cpc = float(rows[0]["avg_cpc"] or 0) if rows else 0.0

        # Получаем среднюю цену из product_sellers
        sellers = await self._product_sellers_db.get_sellers_for_product(sku, active_only=True)
        prices = [float(s["price"]) for s in sellers if s.get("price") and float(s["price"]) > 0]
        avg_price = sum(prices) / len(prices) if prices else 0.0

        efficiency_ratio: float | None = None
        assessment = "no_data"

        if avg_cpc > 0 and avg_price > 0:
            efficiency_ratio = round(avg_price / avg_cpc, 3)
            if efficiency_ratio >= 10:
                assessment = "excellent"
            elif efficiency_ratio >= 5:
                assessment = "good"
            elif efficiency_ratio >= 2:
                assessment = "acceptable"
            else:
                assessment = "poor"

        return {
            "sku": sku,
            "avg_cpc": round(avg_cpc, 2),
            "avg_product_price": round(avg_price, 2),
            "efficiency_ratio": efficiency_ratio,
            "assessment": assessment,
        }

    # -------------------------------------------------------------------------
    # Метрики на уровне всей коллекции товаров
    # -------------------------------------------------------------------------

    async def get_wasted_budget(self, threshold_roi: float = 0.0) -> list[dict]:
        """Товары с ROI ниже порога (по умолчанию < 0 — убыточная реклама).

        Returns список dict: sku, spend, revenue, roi_percent, sorted by spend DESC.
        """
        summaries = await self._ads_db.get_spend_revenue_summary(period_days=30)
        wasted: list[dict] = []

        for row in summaries:
            spend = float(row["total_spend"] or 0)
            revenue = float(row["total_revenue"] or 0)

            if spend < _MIN_SPEND_THRESHOLD:
                continue

            roi_percent = (revenue - spend) / spend * 100.0
            if roi_percent < threshold_roi:
                wasted.append(
                    {
                        "sku": row["product_sku"],
                        "spend": round(spend, 2),
                        "revenue": round(revenue, 2),
                        "roi_percent": round(roi_percent, 2),
                        "clicks": int(row["total_clicks"] or 0),
                        "impressions": int(row["total_impressions"] or 0),
                        "has_revenue_data": revenue > 0,
                    }
                )

        # Сортировка по spend DESC (высшие затраты — наибольший риск)
        wasted.sort(key=lambda x: x["spend"], reverse=True)
        return wasted

    async def get_top_performers(self, limit: int = 20) -> list[dict]:
        """Топ товаров по ROAS (выручка / затраты).

        Включает только товары, у которых есть данные о выручке (revenue > 0).
        Returns список dict: sku, spend, revenue, roas, clicks, sorted by roas DESC.
        """
        summaries = await self._ads_db.get_spend_revenue_summary(period_days=30)
        performers: list[dict] = []

        for row in summaries:
            spend = float(row["total_spend"] or 0)
            revenue = float(row["total_revenue"] or 0)

            if spend < _MIN_SPEND_THRESHOLD or revenue <= 0:
                continue

            roas = round(revenue / spend, 3)
            performers.append(
                {
                    "sku": row["product_sku"],
                    "spend": round(spend, 2),
                    "revenue": round(revenue, 2),
                    "roas": roas,
                    "roi_percent": round((revenue - spend) / spend * 100.0, 2),
                    "clicks": int(row["total_clicks"] or 0),
                    "avg_cpc": round(float(row["avg_cpc"] or 0), 2),
                }
            )

        performers.sort(key=lambda x: x["roas"], reverse=True)
        return performers[:limit]

    async def get_no_bonus_products(self) -> list[dict]:
        """Товары без активных бонусов из последнего скрапинга.

        Обогащает SKU-список названиями из таблицы products.
        Returns список dict: sku, title.
        """
        raw = await self._ads_db.get_products_without_bonuses()
        result: list[dict] = []

        for item in raw:
            product = await self._products_db.get_product(item["product_sku"])
            result.append(
                {
                    "sku": item["product_sku"],
                    "title": product["title"] if product else item["product_sku"],
                }
            )

        return result

    async def get_most_clickable(self, limit: int = 20) -> list[dict]:
        """Топ товаров по CTR с обогащением названиями из products.

        Returns список dict: sku, title, avg_ctr, total_clicks,
                             total_impressions, total_spend.
        """
        raw = await self._ads_db.get_most_clickable(limit=limit)
        result: list[dict] = []

        for item in raw:
            product = await self._products_db.get_product(item["product_sku"])
            result.append(
                {
                    "sku": item["product_sku"],
                    "title": product["title"] if product else item["product_sku"],
                    "avg_ctr": round(float(item["avg_ctr"] or 0), 3),
                    "total_clicks": int(item["total_clicks"] or 0),
                    "total_impressions": int(item["total_impressions"] or 0),
                    "total_spend": round(float(item["total_spend"] or 0), 2),
                }
            )

        return result
