"""
API маршруты TMA Dashboard.

Все обработчики читают зависимости из request.app[_DEPS_KEY]:
  - processor: AdsAnalyticsProcessor
  - aggregator: DataAggregator
  - ads_db: AdsDataDB
  - products_db: ProductsDB
  - scrape_logs_db: ScrapeLogsDB
  - scrape_trigger: Callable[[], Coroutine] | None

Таблица маршрутов:
  GET  /api/dashboard              — главная сводка
  GET  /api/products               — список товаров с метриками (пагинация, сортировка)
  GET  /api/products/{sku}         — карточка товара: метрики, тренды, история
  GET  /api/ads/top-spenders       — топ по затратам
  GET  /api/ads/top-performers     — топ по ROAS
  GET  /api/ads/no-bonus           — товары без бонусов
  GET  /api/ads/most-clickable     — лучший CTR
  GET  /api/ads/wasted-budget      — ROI < 0
  GET  /api/ads/trends/{sku}       — тренды по конкретному SKU
  GET  /api/summary/daily          — сводка за сегодня
  GET  /api/summary/weekly         — сводка за неделю
  GET  /api/summary/monthly        — сводка за месяц
  POST /api/scrape/trigger         — запуск ручного скрапинга
  GET  /api/scrape/status          — статус последнего скрапинга
  GET  /tma                        — TMA frontend (SPA index.html)
  GET  /tma/                       — TMA frontend (SPA index.html)
  GET  /tma/{path:.*}              — TMA SPA fallback / статические файлы
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web

from parser.title_utils import clean_product_title
from ._keys import DEPS_KEY as _DEPS_KEY

if TYPE_CHECKING:
    from analytics import AdsAnalyticsProcessor, DataAggregator
    from database.ads_data import AdsDataDB, ScrapeLogsDB
    from database.products import ProductsDB

logger = logging.getLogger(__name__)


async def _enrich_title(
    sku: str,
    products_db: "ProductsDB",
    ads_db: "AdsDataDB",
    default: str | None = None,
) -> str:
    """Получить название товара: products.title → ads_data.product_name → sku."""
    product = await products_db.get_product(sku)

    if product:
        title = clean_product_title(product.get("title"))
        if title:
            return title

    latest = await ads_db.get_latest_by_sku(sku)
    if latest:
        # Сначала новая колонка product_name (заполняется с v5 миграции)
        title = clean_product_title(latest.get("product_name"))
        if title:
            return title
        # Fallback на raw_data JSON для старых записей
        if latest.get("raw_data"):
            try:
                raw = json.loads(latest["raw_data"])
                title = clean_product_title(raw.get("product_name"))
                if title:
                    return title
            except Exception:
                pass

    return default if default is not None else sku


# Допустимые значения sort для /api/products
_ALLOWED_SORT_KEYS = {
    "spend_desc",
    "spend_asc",
    "ctr_desc",
    "clicks_desc",
    "roi_desc",
    "roi_asc",
}

# Максимальный limit в одном запросе
_MAX_PAGE_LIMIT = 100


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _safe_float(value) -> float:
    """Безопасное приведение к float."""
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _parse_int_param(request: web.Request, name: str, default: int, min_val: int = 0, max_val: int = 10000) -> int:
    raw = request.rel_url.query.get(name, str(default))
    try:
        value = int(raw)
        return max(min_val, min(max_val, value))
    except ValueError:
        return default


def _parse_sort_param(request: web.Request, default: str = "spend_desc") -> str:
    sort = request.rel_url.query.get("sort", default)
    return sort if sort in _ALLOWED_SORT_KEYS else default


def _sort_items_by(items: list[dict], sort_key: str) -> list[dict]:
    """Сортировка списка товаров по ключу."""
    sort_map: dict[str, tuple] = {
        "spend_desc": ("spend",    True),
        "spend_asc":  ("spend",    False),
        "ctr_desc":   ("avg_ctr",  True),
        "clicks_desc":("clicks",   True),
        "roi_desc":   ("roi_percent", True),
        "roi_asc":    ("roi_percent", False),
    }
    field, reverse = sort_map.get(sort_key, ("spend", True))
    # None-значения всегда уходят в конец
    return sorted(items, key=lambda x: (x.get(field) is None, x.get(field) or 0), reverse=reverse)


# ---------------------------------------------------------------------------
# Обработчики
# ---------------------------------------------------------------------------

async def _handle_dashboard(request: web.Request) -> web.Response:
    """GET /api/dashboard — главная сводка: общие метрики + сигналы."""
    deps = request.app[_DEPS_KEY]
    processor: "AdsAnalyticsProcessor" = deps["processor"]
    aggregator: "DataAggregator" = deps["aggregator"]

    try:
        total_stats = await aggregator.get_total_stats()
        daily = await aggregator.aggregate_daily()
        wasted = await processor.get_wasted_budget(threshold_roi=0.0)
        no_bonus = await processor.get_no_bonus_products()

        alerts = []
        if wasted:
            alerts.append({
                "type": "wasted_budget",
                "message": f"{len(wasted)} товар(ов) сливают бюджет (ROI < 0)",
                "count": len(wasted),
            })
        if no_bonus:
            alerts.append({
                "type": "no_bonus",
                "message": f"{len(no_bonus)} товар(ов) без активных бонусов",
                "count": len(no_bonus),
            })

        return web.json_response({
            "total_stats": total_stats,
            "today": daily,
            "alerts": alerts,
        })
    except Exception as exc:
        logger.error("dashboard: ошибка: %s", exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_products_list(request: web.Request) -> web.Response:
    """GET /api/products?sort=spend_desc&limit=20&offset=0&period=30&q=&ads=with|without — каталог товаров.

    Первичный источник — таблица products (реальные названия).
    Ads-метрики наслаиваются где SKU совпадает с ads_data.
    Параметр ads=with: только товары с данными рекламы; ads=without: без рекламы.
    """
    deps = request.app[_DEPS_KEY]
    ads_db: "AdsDataDB" = deps["ads_db"]
    products_db: "ProductsDB" = deps["products_db"]

    sort = _parse_sort_param(request)
    limit = _parse_int_param(request, "limit", 20, 1, _MAX_PAGE_LIMIT)
    offset = _parse_int_param(request, "offset", 0, 0)
    period = _parse_int_param(request, "period", 30, 1, 365)
    query_text = request.rel_url.query.get("q", "").strip()
    ads_filter = request.rel_url.query.get("ads", "").strip().lower()  # with | without | ""

    try:
        # Первичный источник: таблица products (реальные названия)
        if query_text:
            catalog = await products_db.search_products(query_text)
        else:
            catalog = await products_db.get_all_products()

        # Рекламные метрики по SKU за период
        ads_summaries: dict[str, dict] = {
            row["product_sku"]: row
            for row in await ads_db.get_spend_revenue_summary(period_days=period)
        }

        # Сборка: каталожный товар + наслоение рекламных данных
        items: list[dict] = []
        for product in catalog:
            sku = product["master_sku"]
            ads = ads_summaries.get(sku, {})
            has_ads = sku in ads_summaries
            spend = _safe_float(ads.get("total_spend"))
            items.append({
                "sku": sku,
                "title": product.get("title") or sku,
                "url": product.get("url"),
                "has_ads": has_ads,
                "spend": round(spend, 2),
                "revenue": 0.0,
                "clicks": _safe_int(ads.get("total_clicks")),
                "impressions": _safe_int(ads.get("total_impressions")),
                "avg_ctr": round(_safe_float(ads.get("avg_ctr")), 3),
                "avg_cpc": round(_safe_float(ads.get("avg_cpc")), 2),
                "roi_percent": None,
            })

        if ads_filter == "with":
            items = [i for i in items if i["has_ads"]]
        elif ads_filter == "without":
            items = [i for i in items if not i["has_ads"]]

        items = _sort_items_by(items, sort)
        total = len(items)
        page = items[offset: offset + limit]

        return web.json_response({
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "period_days": period,
            "filters": {
                "q": query_text,
                "ads": ads_filter if ads_filter in {"with", "without"} else "",
            },
            "items": page,
        })
    except Exception as exc:
        logger.error("products_list: ошибка: %s", exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_product_detail(request: web.Request) -> web.Response:
    """GET /api/products/{sku}?period=30&trend_days=30 — карточка товара."""
    deps = request.app[_DEPS_KEY]
    processor: "AdsAnalyticsProcessor" = deps["processor"]
    aggregator: "DataAggregator" = deps["aggregator"]
    ads_db: "AdsDataDB" = deps["ads_db"]
    products_db: "ProductsDB" = deps["products_db"]

    sku = request.match_info["sku"]
    period = _parse_int_param(request, "period", 30, 1, 365)
    trend_days = _parse_int_param(request, "trend_days", 30, 7, 90)

    try:
        product = await products_db.get_product(sku)

        # Не возвращаем 404 если товар есть в ads_data — карточка всё равно полезна
        if product is None:
            latest_check = await ads_db.get_latest_by_sku(sku)
            if latest_check is None:
                return web.json_response({"error": "Product not found"}, status=404)

        roi_data = await processor.calculate_roi(sku, period_days=period)
        roas = await processor.calculate_roas(sku, period_days=period)
        cpc_eff = await processor.get_cpc_efficiency(sku)
        trends = await aggregator.get_trends(sku, days=trend_days)
        latest_marketing = await ads_db.get_latest_by_sku(sku, source="kaspi_marketing")
        latest_bonus = await ads_db.get_latest_by_sku(sku, source="kaspi_bonus")

        latest_data: dict | None = None
        if latest_marketing:
            latest_data = dict(latest_marketing)
        elif latest_bonus:
            latest_data = dict(latest_bonus)

        if latest_data is not None and latest_bonus:
            latest_data["bonus_active"] = latest_bonus.get("bonus_active", 0)
            latest_data["bonus_percent"] = latest_bonus.get("bonus_percent", 0.0)
            latest_data["bonus_scraped_at"] = latest_bonus.get("scraped_at")

        title = product.get("title") if product else await _enrich_title(sku, products_db, ads_db)
        url = product.get("url") if product else None

        return web.json_response({
            "sku": sku,
            "title": title,
            "url": url,
            "roi": roi_data,
            "roas": roas,
            "cpc_efficiency": cpc_eff,
            "trends": trends,
            "latest_data": latest_data,
            "period_days": period,
            "trend_days": trend_days,
        })
    except Exception as exc:
        logger.error("product_detail sku=%s: ошибка: %s", sku, exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_top_spenders(request: web.Request) -> web.Response:
    """GET /api/ads/top-spenders?limit=20 — топ по затратам."""
    deps = request.app[_DEPS_KEY]
    ads_db: "AdsDataDB" = deps["ads_db"]
    products_db: "ProductsDB" = deps["products_db"]

    limit = _parse_int_param(request, "limit", 20, 1, _MAX_PAGE_LIMIT)

    try:
        rows = await ads_db.get_top_spenders(limit=limit)
        result = []
        for row in rows:
            title = await _enrich_title(row["product_sku"], products_db, ads_db)
            result.append({
                "sku": row["product_sku"],
                "title": title,
                "total_spend": round(_safe_float(row["total_spend"]), 2),
                "total_clicks": _safe_int(row["total_clicks"]),
                "total_impressions": _safe_int(row["total_impressions"]),
                "avg_ctr": round(_safe_float(row["avg_ctr"]), 3),
                "avg_cpc": round(_safe_float(row["avg_cpc"]), 2),
            })
        return web.json_response({"items": result, "count": len(result)})
    except Exception as exc:
        logger.error("top_spenders: ошибка: %s", exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_top_performers(request: web.Request) -> web.Response:
    """GET /api/ads/top-performers?limit=20 — топ по ROAS."""
    deps = request.app[_DEPS_KEY]
    processor: "AdsAnalyticsProcessor" = deps["processor"]
    products_db: "ProductsDB" = deps["products_db"]
    ads_db: "AdsDataDB" = deps["ads_db"]

    limit = _parse_int_param(request, "limit", 20, 1, _MAX_PAGE_LIMIT)

    try:
        items = await processor.get_top_performers(limit=limit)
        for item in items:
            item["title"] = await _enrich_title(item["sku"], products_db, ads_db)
        return web.json_response({"items": items, "count": len(items)})
    except Exception as exc:
        logger.error("top_performers: ошибка: %s", exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_no_bonus(request: web.Request) -> web.Response:
    """GET /api/ads/no-bonus — товары без активных бонусов."""
    deps = request.app[_DEPS_KEY]
    processor: "AdsAnalyticsProcessor" = deps["processor"]

    try:
        items = await processor.get_no_bonus_products()
        return web.json_response({"items": items, "count": len(items)})
    except Exception as exc:
        logger.error("no_bonus: ошибка: %s", exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_most_clickable(request: web.Request) -> web.Response:
    """GET /api/ads/most-clickable?limit=20 — лучший CTR."""
    deps = request.app[_DEPS_KEY]
    processor: "AdsAnalyticsProcessor" = deps["processor"]

    limit = _parse_int_param(request, "limit", 20, 1, _MAX_PAGE_LIMIT)

    try:
        items = await processor.get_most_clickable(limit=limit)
        return web.json_response({"items": items, "count": len(items)})
    except Exception as exc:
        logger.error("most_clickable: ошибка: %s", exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_wasted_budget(request: web.Request) -> web.Response:
    """GET /api/ads/wasted-budget?threshold=0 — товары с ROI ниже порога."""
    deps = request.app[_DEPS_KEY]
    processor: "AdsAnalyticsProcessor" = deps["processor"]
    products_db: "ProductsDB" = deps["products_db"]

    try:
        threshold = float(request.rel_url.query.get("threshold", "0"))
    except ValueError:
        threshold = 0.0

    try:
        items = await processor.get_wasted_budget(threshold_roi=threshold)
        for item in items:
            item["title"] = await _enrich_title(item["sku"], products_db, ads_db)
        return web.json_response({"items": items, "count": len(items), "threshold": threshold})
    except Exception as exc:
        logger.error("wasted_budget: ошибка: %s", exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_trends(request: web.Request) -> web.Response:
    """GET /api/ads/trends/{sku}?days=30 — дневные тренды по SKU."""
    deps = request.app[_DEPS_KEY]
    aggregator: "DataAggregator" = deps["aggregator"]

    sku = request.match_info["sku"]
    days = _parse_int_param(request, "days", 30, 7, 90)

    try:
        trends = await aggregator.get_trends(sku, days=days)
        return web.json_response({"sku": sku, "days": days, "trends": trends})
    except Exception as exc:
        logger.error("trends sku=%s: ошибка: %s", sku, exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_summary_daily(request: web.Request) -> web.Response:
    """GET /api/summary/daily — сводка за сегодня."""
    deps = request.app[_DEPS_KEY]
    aggregator: "DataAggregator" = deps["aggregator"]

    try:
        data = await aggregator.aggregate_daily()
        return web.json_response(data)
    except Exception as exc:
        logger.error("summary_daily: ошибка: %s", exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_summary_weekly(request: web.Request) -> web.Response:
    """GET /api/summary/weekly — сводка за неделю."""
    deps = request.app[_DEPS_KEY]
    aggregator: "DataAggregator" = deps["aggregator"]

    try:
        data = await aggregator.aggregate_weekly()
        return web.json_response(data)
    except Exception as exc:
        logger.error("summary_weekly: ошибка: %s", exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_summary_monthly(request: web.Request) -> web.Response:
    """GET /api/summary/monthly — сводка за месяц."""
    deps = request.app[_DEPS_KEY]
    aggregator: "DataAggregator" = deps["aggregator"]

    try:
        data = await aggregator.aggregate_monthly()
        return web.json_response(data)
    except Exception as exc:
        logger.error("summary_monthly: ошибка: %s", exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_scrape_trigger(request: web.Request) -> web.Response:
    """POST /api/scrape/trigger — запуск ручного скрапинга."""
    deps = request.app[_DEPS_KEY]
    scrape_trigger = deps.get("scrape_trigger")

    if scrape_trigger is None:
        return web.json_response(
            {"error": "Scraper not configured (Kaspi auth not set)"},
            status=503,
        )

    try:
        # Запускаем скрапинг как фоновую задачу, не блокируя ответ
        asyncio.create_task(scrape_trigger())
        logger.info(
            "POST /api/scrape/trigger: запущен пользователем tma_user_id=%s",
            request.get("tma_user_id"),
        )
        return web.json_response({"status": "triggered"})
    except Exception as exc:
        logger.error("scrape_trigger: ошибка: %s", exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_scrape_status(request: web.Request) -> web.Response:
    """GET /api/scrape/status — статус последнего скрапинга."""
    deps = request.app[_DEPS_KEY]
    scrape_logs_db: "ScrapeLogsDB" = deps["scrape_logs_db"]

    try:
        latest = await scrape_logs_db.get_latest()
        if latest is None:
            return web.json_response({"status": "never_run", "log": None})
        return web.json_response({"status": latest.get("status"), "log": latest})
    except Exception as exc:
        logger.error("scrape_status: ошибка: %s", exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_health(request: web.Request) -> web.Response:
    """GET /health — health-check без авторизации."""
    return web.json_response({"status": "ok"})


# ---------------------------------------------------------------------------
# Регистрация маршрутов
# ---------------------------------------------------------------------------

def register_routes(app: web.Application) -> None:
    """Зарегистрировать все API маршруты в приложении."""
    app.router.add_get("/health", _handle_health)
    app.router.add_get("/api/dashboard", _handle_dashboard)
    app.router.add_get("/api/products", _handle_products_list)
    app.router.add_get("/api/products/{sku}", _handle_product_detail)
    app.router.add_get("/api/ads/top-spenders", _handle_top_spenders)
    app.router.add_get("/api/ads/top-performers", _handle_top_performers)
    app.router.add_get("/api/ads/no-bonus", _handle_no_bonus)
    app.router.add_get("/api/ads/most-clickable", _handle_most_clickable)
    app.router.add_get("/api/ads/wasted-budget", _handle_wasted_budget)
    app.router.add_get("/api/ads/trends/{sku}", _handle_trends)
    app.router.add_get("/api/summary/daily", _handle_summary_daily)
    app.router.add_get("/api/summary/weekly", _handle_summary_weekly)
    app.router.add_get("/api/summary/monthly", _handle_summary_monthly)
    app.router.add_post("/api/scrape/trigger", _handle_scrape_trigger)
    app.router.add_get("/api/scrape/status", _handle_scrape_status)


def _register_tma_static(app: web.Application, dist_path: Path) -> None:
    """
    Зарегистрировать раздачу статики TMA (React SPA) из dist/.

    /tma/assets/*  → dist/assets/* (статические файлы)
    /tma           → dist/index.html (SPA вход)
    /tma/          → dist/index.html
    /tma/{path}    → dist/index.html (SPA fallback для React Router)
    """
    assets_path = dist_path / "assets"
    if assets_path.is_dir():
        app.router.add_static("/tma/assets", assets_path, name="tma_assets")

    index_html = dist_path / "index.html"

    async def _serve_index(request: web.Request) -> web.Response:
        if not index_html.exists():
            return web.Response(
                text="TMA not deployed. Run: cd tma && npm run build",
                status=503,
                content_type="text/plain",
            )
        return web.FileResponse(index_html)

    app.router.add_get("/tma", _serve_index)
    app.router.add_get("/tma/", _serve_index)
    app.router.add_get("/tma/{path:.*}", _serve_index)
