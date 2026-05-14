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
  GET  /api/products/export.xlsx   — экспорт списка товаров в Excel
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
import re
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

# Идентификаторы источников ads_data — используются в /api/products?missing=...
_SRC_MARKETING = "kaspi_marketing"        # внутренняя реклама
_SRC_EXTERNAL = "kaspi_external_ads"      # внешняя реклама
_SRC_BONUS_SELLER = "kaspi_bonus_seller"  # бонус от продавца
_SRC_BONUS_REVIEW = "kaspi_bonus_review"  # бонус за отзыв
_SRC_BONUS_LEGACY = "kaspi_bonus"         # старые записи до миграции (учитываются для бонусных фильтров)

# Маппинг missing-значения → (source, требует bonus_active=1, требует spend>0)
_MISSING_FILTER_MAP: dict[str, tuple[tuple[str, ...], bool, bool]] = {
    "ads": ((_SRC_MARKETING,), False, True),
    "external": ((_SRC_EXTERNAL,), False, True),
    "bonus_seller": ((_SRC_BONUS_SELLER, _SRC_BONUS_LEGACY), True, False),
    "bonus_review": ((_SRC_BONUS_REVIEW, _SRC_BONUS_LEGACY), True, False),
}

# Поддерживаемые report_period (длина окна XLSX-отчёта в днях). Скрапер парсит
# оба значения за одну ночь и кладёт строки с нужным period_days в БД.
_ALLOWED_REPORT_PERIODS = {7, 30}
_DEFAULT_REPORT_PERIOD = 7


def _parse_report_period(request: web.Request) -> int:
    """Получить значение report_period из query (?report_period=7|30).

    Если параметр не указан или невалиден, возвращаем дефолтные 7 дней.
    """
    raw = request.rel_url.query.get("report_period", "").strip()
    if not raw:
        return _DEFAULT_REPORT_PERIOD
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_REPORT_PERIOD
    return value if value in _ALLOWED_REPORT_PERIODS else _DEFAULT_REPORT_PERIOD


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


# Реальный товарный SKU в Kaspi: только цифры, 5-12 разрядов.
# Surrogate-коды скрапера (`RPT-XXXXXXXX`, `CMP-…`, хеш-строки) этому формату
# не удовлетворяют и в каталог не попадают.
_REAL_SKU_RE = re.compile(r"^\d{5,12}$")


def _is_real_product_sku(sku: str | None) -> bool:
    """Проверка: похоже ли значение на настоящий товарный SKU Kaspi."""
    if not sku:
        return False
    return bool(_REAL_SKU_RE.match(sku.strip()))


def _has_ad_activity(summary: dict) -> bool:
    """Есть ли в свежем snapshot реальные рекламные метрики.

    Достаточно одной ненулевой метрики (impressions/clicks/spend) — это
    означает, что товар реально присутствует в рекламном кабинете
    («неактивные» строки и нулевые агрегаты отсекаются).
    """
    return (
        _safe_float(summary.get("total_spend")) > 0
        or _safe_int(summary.get("total_clicks")) > 0
        or _safe_int(summary.get("total_impressions")) > 0
    )


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
    """GET /api/dashboard — главная сводка: общие метрики + сигналы.

    Query params:
        report_period: 7 | 30 — окно XLSX-отчёта, по которому считать траты
            и сигналы. По умолчанию 7 (как в /api/products).
    """
    deps = request.app[_DEPS_KEY]
    processor: "AdsAnalyticsProcessor" = deps["processor"]
    aggregator: "DataAggregator" = deps["aggregator"]
    report_period = _parse_report_period(request)

    try:
        total_stats = await aggregator.get_total_stats(report_period=report_period)
        daily = await aggregator.aggregate_daily()
        wasted = await processor.get_wasted_budget(
            threshold_roi=0.0, report_period=report_period,
        )
        no_bonus = await processor.get_no_bonus_products(
            report_period=report_period,
        )

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


async def _build_products_list(
    ads_db: "AdsDataDB",
    products_db: "ProductsDB",
    *,
    period: int,
    report_period: int,
    query_text: str,
    ads_filter: str,
    missing: str,
    sort: str,
) -> list[dict]:
    """Собрать и отфильтровать список товаров с рекламными метриками.

    Каталог = `products` (витрина) + те SKU из `ads_data`, у которых:
      - корректный товарный формат (5-12 цифр), И
      - есть рекламная активность (последний snapshot.spend > 0 ИЛИ есть
        импрешены/клики).

    Surrogate-SKU вида `RPT-…` (которые скрапер генерирует когда не видит
    реальный артикул в строке XLSX) — отбрасываются: пользователь всё
    равно не сможет идентифицировать такой «товар».

    Используется и `/api/products` (с пагинацией), и
    `/api/products/export.xlsx` (полная выгрузка) — единая точка фильтрации
    исключает расхождения между UI и экспортом.
    """
    if query_text:
        catalog = await products_db.search_products(query_text)
    else:
        catalog = await products_db.get_all_products()

    ads_summaries: dict[str, dict] = {
        row["product_sku"]: row
        for row in await ads_db.get_spend_revenue_summary(
            period_days=period, report_period=report_period,
        )
    }

    # Расширяем каталог: добавляем только реальные товарные SKU, на которые
    # реально тратят деньги. Бот сам по себе уже парсит **активные**
    # рекламные кампании, поэтому SKU из ads_data с spend>0 = это реально
    # рекламируемый товар (не наложенная аналитика, не агрегат-строка).
    catalog_skus = {p["master_sku"] for p in catalog}
    ads_names = await ads_db.get_all_recent_skus_with_names(period_days=period)
    q_lower = query_text.lower().strip()

    extra_catalog: list[dict] = []
    for sku, summary in ads_summaries.items():
        if sku in catalog_skus:
            continue
        if not _is_real_product_sku(sku):
            continue
        # Должна быть рекламная активность в свежем snapshot.
        if not _has_ad_activity(summary):
            continue
        name = ads_names.get(sku) or sku
        if q_lower and q_lower not in sku.lower() and q_lower not in name.lower():
            continue
        extra_catalog.append({
            "master_sku": sku,
            "title": name,
            "url": None,
        })

    combined_catalog = list(catalog) + extra_catalog

    external_skus = await ads_db.get_active_skus_by_source(
        _SRC_EXTERNAL, period_days=period, require_spend=True,
        report_period=report_period,
    )
    # Бонусы: report_period не передаём — бонус это моментальный статус,
    # не зависящий от 7/30-дневного окна отчёта.
    bonus_seller_skus = (
        await ads_db.get_active_skus_by_source(
            _SRC_BONUS_SELLER, period_days=period, require_active_bonus=True,
        )
        | await ads_db.get_active_skus_by_source(
            _SRC_BONUS_LEGACY, period_days=period, require_active_bonus=True,
        )
    )
    bonus_review_skus = (
        await ads_db.get_active_skus_by_source(
            _SRC_BONUS_REVIEW, period_days=period, require_active_bonus=True,
        )
        | await ads_db.get_active_skus_by_source(
            _SRC_BONUS_LEGACY, period_days=period, require_active_bonus=True,
        )
    )

    items: list[dict] = []
    for product in combined_catalog:
        sku = product["master_sku"]
        ads = ads_summaries.get(sku, {})
        has_ads = sku in ads_summaries
        spend = _safe_float(ads.get("total_spend"))
        items.append({
            "sku": sku,
            "title": product.get("title") or sku,
            "url": product.get("url"),
            "has_ads": has_ads,
            "has_external_ads": sku in external_skus,
            "has_bonus_seller": sku in bonus_seller_skus,
            "has_bonus_review": sku in bonus_review_skus,
            "spend": round(spend, 2),
            "revenue": 0.0,
            "clicks": _safe_int(ads.get("total_clicks")),
            "impressions": _safe_int(ads.get("total_impressions")),
            "avg_ctr": round(_safe_float(ads.get("avg_ctr")), 3),
            "avg_cpc": round(_safe_float(ads.get("avg_cpc")), 2),
            "roi_percent": None,
        })

    if missing == "ads":
        items = [i for i in items if not i["has_ads"]]
    elif missing == "external":
        items = [i for i in items if not i["has_external_ads"]]
    elif missing == "bonus_seller":
        items = [i for i in items if not i["has_bonus_seller"]]
    elif missing == "bonus_review":
        items = [i for i in items if not i["has_bonus_review"]]

    if ads_filter == "with":
        items = [i for i in items if i["has_ads"]]
    elif ads_filter == "without":
        items = [i for i in items if not i["has_ads"]]

    return _sort_items_by(items, sort)


async def _handle_products_list(request: web.Request) -> web.Response:
    """GET /api/products — каталог товаров с наслоением рекламных метрик.

    Параметры:
        sort           — spend_desc | spend_asc | ctr_desc | clicks_desc | roi_desc | roi_asc
        limit          — 1..100 (default 20)
        offset         — 0..N
        period         — 1..365 дней истории (default 30)
        report_period  — 7 | 30 — длина окна XLSX-отчёта (default 7).
                         Влияет на фильтрацию по period_days в ads_data.
        q              — поиск по title/SKU
        ads            — with | without (deprecated, оставлен для совместимости)
        missing        — ads | external | bonus_seller | bonus_review
                         (товары, у которых нет указанного признака)
    """
    deps = request.app[_DEPS_KEY]
    ads_db: "AdsDataDB" = deps["ads_db"]
    products_db: "ProductsDB" = deps["products_db"]

    sort = _parse_sort_param(request)
    limit = _parse_int_param(request, "limit", 20, 1, _MAX_PAGE_LIMIT)
    offset = _parse_int_param(request, "offset", 0, 0)
    period = _parse_int_param(request, "period", 30, 1, 365)
    report_period = _parse_report_period(request)
    query_text = request.rel_url.query.get("q", "").strip()
    ads_filter = request.rel_url.query.get("ads", "").strip().lower()
    missing = request.rel_url.query.get("missing", "").strip().lower()

    try:
        items = await _build_products_list(
            ads_db,
            products_db,
            period=period,
            report_period=report_period,
            query_text=query_text,
            ads_filter=ads_filter,
            missing=missing,
            sort=sort,
        )
        total = len(items)
        page = items[offset: offset + limit]

        return web.json_response({
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort": sort,
            "period_days": period,
            "report_period": report_period,
            "filters": {
                "q": query_text,
                "ads": ads_filter if ads_filter in {"with", "without"} else "",
                "missing": missing if missing in _MISSING_FILTER_MAP else "",
            },
            "items": page,
        })
    except Exception as exc:
        logger.error("products_list: ошибка: %s", exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_products_export(request: web.Request) -> web.StreamResponse:
    """GET /api/products/export.xlsx — выгрузка списка товаров в Excel.

    Принимает те же фильтры, что и `/api/products`, но игнорирует limit/offset:
    в файл попадают ВСЕ товары после применения фильтров.
    """
    deps = request.app[_DEPS_KEY]
    ads_db: "AdsDataDB" = deps["ads_db"]
    products_db: "ProductsDB" = deps["products_db"]

    sort = _parse_sort_param(request)
    period = _parse_int_param(request, "period", 30, 1, 365)
    report_period = _parse_report_period(request)
    query_text = request.rel_url.query.get("q", "").strip()
    ads_filter = request.rel_url.query.get("ads", "").strip().lower()
    missing = request.rel_url.query.get("missing", "").strip().lower()

    try:
        items = await _build_products_list(
            ads_db,
            products_db,
            period=period,
            report_period=report_period,
            query_text=query_text,
            ads_filter=ads_filter,
            missing=missing,
            sort=sort,
        )

        from .xlsx_writer import write_xlsx

        headers = [
            "SKU",
            "Название",
            "Период отчёта (дн.)",
            "Затраты, ₸",
            "Клики",
            "Показы",
            "CTR, %",
            "CPC, ₸",
            "Реклама",
            "Внешняя реклама",
            "Бонус продавца",
            "Бонус за отзыв",
            "Ссылка",
        ]
        rows = [
            [
                item["sku"],
                item["title"],
                report_period,
                item["spend"],
                item["clicks"],
                item["impressions"],
                item["avg_ctr"],
                item["avg_cpc"],
                "Да" if item["has_ads"] else "Нет",
                "Да" if item["has_external_ads"] else "Нет",
                "Да" if item["has_bonus_seller"] else "Нет",
                "Да" if item["has_bonus_review"] else "Нет",
                item.get("url") or "",
            ]
            for item in items
        ]

        payload = write_xlsx(
            headers=headers,
            rows=rows,
            sheet_name=f"Аналитика {report_period}д",
        )

        from datetime import datetime as _dt
        filename = f"kaspibot-products-{report_period}d-{_dt.now():%Y%m%d-%H%M}.xlsx"
        return web.Response(
            body=payload,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )
    except Exception as exc:
        logger.error("products_export: ошибка: %s", exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


async def _handle_product_detail(request: web.Request) -> web.Response:
    """GET /api/products/{sku}?period=30&trend_days=30 — карточка товара.

    Возвращает 4 раздела активности (marketing / external_ads / bonus_seller /
    bonus_review). Каждый — dict со статусом, метриками и названием
    последней кампании/акции.
    """
    deps = request.app[_DEPS_KEY]
    processor: "AdsAnalyticsProcessor" = deps["processor"]
    aggregator: "DataAggregator" = deps["aggregator"]
    ads_db: "AdsDataDB" = deps["ads_db"]
    products_db: "ProductsDB" = deps["products_db"]

    sku = request.match_info["sku"]
    period = _parse_int_param(request, "period", 30, 1, 365)
    trend_days = _parse_int_param(request, "trend_days", 30, 7, 90)
    report_period = _parse_report_period(request)

    async def _latest_with_fallback(source: str, with_period: bool) -> dict | None:
        """Берём свежайшую запись по source.

        Для рекламных секций сначала пробуем с фильтром по report_period; если
        точного снапшота за выбранный период нет — возвращаем самую свежую
        вообще. Это критично сразу после миграции (когда в БД ещё нет 30д
        снапшотов) и для бонусов (для них period_days не имеет физического
        смысла — бонус активен в моменте, а не "за 7 дней").
        """
        if with_period:
            row = await ads_db.get_latest_by_sku(
                sku, source=source, report_period=report_period,
            )
            if row is not None:
                return row
        return await ads_db.get_latest_by_sku(sku, source=source)

    try:
        product = await products_db.get_product(sku)

        if product is None:
            latest_check = await ads_db.get_latest_by_sku(sku)
            if latest_check is None:
                return web.json_response({"error": "Product not found"}, status=404)

        roi_data = await processor.calculate_roi(sku, period_days=period)
        roas = await processor.calculate_roas(sku, period_days=period)
        cpc_eff = await processor.get_cpc_efficiency(sku)
        trends = await aggregator.get_trends(sku, days=trend_days)

        latest_marketing = await _latest_with_fallback(_SRC_MARKETING, with_period=True)
        latest_external = await _latest_with_fallback(_SRC_EXTERNAL, with_period=True)
        # Для бонусов фильтр по report_period не применяем — бонус это
        # моментальный статус, не зависит от 7/30-дневного окна.
        latest_bonus_seller = await _latest_with_fallback(_SRC_BONUS_SELLER, with_period=False)
        latest_bonus_review = await _latest_with_fallback(_SRC_BONUS_REVIEW, with_period=False)
        latest_bonus_legacy = await _latest_with_fallback(_SRC_BONUS_LEGACY, with_period=False)

        # Назад-совместимость: latest_data — последняя marketing-запись,
        # с подмешиванием бонусной информации (старая контрактная форма).
        latest_data: dict | None = None
        if latest_marketing:
            latest_data = dict(latest_marketing)
        elif latest_bonus_seller or latest_bonus_review or latest_bonus_legacy:
            latest_data = dict(
                latest_bonus_seller or latest_bonus_review or latest_bonus_legacy
            )

        any_bonus = latest_bonus_seller or latest_bonus_review or latest_bonus_legacy
        if latest_data is not None and any_bonus:
            latest_data["bonus_active"] = any_bonus.get("bonus_active", 0)
            latest_data["bonus_percent"] = any_bonus.get("bonus_percent", 0.0)
            latest_data["bonus_scraped_at"] = any_bonus.get("scraped_at")

        title = product.get("title") if product else await _enrich_title(sku, products_db, ads_db)
        url = product.get("url") if product else None

        # 4 секции активности
        sections = {
            "marketing": _build_ads_section(latest_marketing),
            "external_ads": _build_ads_section(latest_external),
            "bonus_seller": _build_bonus_section(latest_bonus_seller, latest_bonus_legacy),
            "bonus_review": _build_bonus_section(latest_bonus_review, latest_bonus_legacy),
        }

        return web.json_response({
            "sku": sku,
            "title": title,
            "url": url,
            "roi": roi_data,
            "roas": roas,
            "cpc_efficiency": cpc_eff,
            "trends": trends,
            "latest_data": latest_data,
            "sections": sections,
            "period_days": period,
            "trend_days": trend_days,
            "report_period": report_period,
        })
    except Exception as exc:
        logger.error("product_detail sku=%s: ошибка: %s", sku, exc, exc_info=True)
        return web.json_response({"error": "Internal server error"}, status=500)


# ---------------------------------------------------------------------------
# Секции активности товара
# ---------------------------------------------------------------------------

# Если последнее списание было больше этого порога — реклама запущена,
# но фактически не показывается (типичный случай: чужая ставка ниже на 3%).
_ADS_STALE_DAYS = 2


def _campaign_name_from_raw(row: dict | None) -> str | None:
    """Достать campaign_name из raw_data JSON (либо None)."""
    if not row:
        return None
    raw = row.get("raw_data")
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        name = (data or {}).get("campaign_name") if isinstance(data, dict) else None
        return name or None
    except Exception:
        return None


def _build_ads_section(row: dict | None) -> dict:
    """Сформировать секцию рекламной активности (marketing / external_ads).

    activity:
      inactive — записи нет
      active   — есть запись с spend > 0 и свежее _ADS_STALE_DAYS
      stale    — запись есть, но spend = 0 или скрапинг старше порога
    """
    if not row:
        return {
            "active": False,
            "activity": "inactive",
            "spend": 0.0,
            "clicks": 0,
            "impressions": 0,
            "ctr": 0.0,
            "cpc": 0.0,
            "campaign_name": None,
            "scraped_at": None,
        }

    spend = _safe_float(row.get("spend"))
    scraped_at = row.get("scraped_at")
    activity = "active" if spend > 0 else "stale"

    if scraped_at and activity == "active":
        try:
            from datetime import datetime, timedelta
            ts = datetime.fromisoformat(str(scraped_at).replace(" ", "T"))
            if datetime.now() - ts > timedelta(days=_ADS_STALE_DAYS):
                activity = "stale"
        except Exception:
            pass

    return {
        "active": True,
        "activity": activity,
        "spend": round(spend, 2),
        "clicks": _safe_int(row.get("clicks")),
        "impressions": _safe_int(row.get("impressions")),
        "ctr": round(_safe_float(row.get("ctr")), 3),
        "cpc": round(_safe_float(row.get("cpc")), 2),
        "campaign_name": _campaign_name_from_raw(row),
        "scraped_at": scraped_at,
    }


def _build_bonus_section(row: dict | None, legacy_row: dict | None) -> dict:
    """Сформировать секцию бонуса (seller / review).

    Если по новому source данных нет, но есть запись в kaspi_bonus
    (старые скрапинги до миграции) — отображаем её как fallback.
    """
    src = row or legacy_row
    if not src:
        return {
            "active": False,
            "activity": "inactive",
            "percent": 0.0,
            "campaign_name": None,
            "scraped_at": None,
        }

    bonus_active = bool(src.get("bonus_active"))
    return {
        "active": bonus_active,
        "activity": "active" if bonus_active else "stale",
        "percent": round(_safe_float(src.get("bonus_percent")), 2),
        "campaign_name": _campaign_name_from_raw(src),
        "scraped_at": src.get("scraped_at"),
    }


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
    app.router.add_get("/api/products/export.xlsx", _handle_products_export)
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
        # Читаем файл напрямую чтобы принудительно отключить браузерный кеш.
        # FileResponse выставляет ETag и возвращает 304 при повторных запросах —
        # это ломает TMA после каждого обновления сборки (новые хешированные имена JS/CSS).
        # Хешированные ассеты (index-abc123.js) можно кешировать — они меняются вместе с именем.
        content = index_html.read_bytes()
        return web.Response(
            body=content,
            content_type="text/html",
            charset="utf-8",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    app.router.add_get("/tma", _serve_index)
    app.router.add_get("/tma/", _serve_index)
    app.router.add_get("/tma/{path:.*}", _serve_index)
