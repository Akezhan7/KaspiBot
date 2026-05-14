"""
Тесты Фазы 12: AdsDataDB DAO + AdsAnalyticsProcessor + DataAggregator.

Покрывает:
- AdsDataDB: save_campaign, save_campaigns_batch, get_latest_by_sku,
             get_campaigns_for_period, get_top_spenders, get_products_without_bonuses,
             get_most_clickable, get_bonuses_status, get_spend_revenue_summary
- AdsAnalyticsProcessor: calculate_roi, calculate_roas, get_cpc_efficiency,
                          get_wasted_budget, get_top_performers, get_no_bonus_products,
                          get_most_clickable
- DataAggregator: aggregate_daily, aggregate_weekly, aggregate_monthly,
                  get_total_stats, get_trends
"""
import aiosqlite
import pytest
from datetime import date, timedelta
from pathlib import Path

from config import now_kz_str, now_kz
from database.schema import DatabaseSchema
from database.migrations import DatabaseMigrations
from database.ads_data import AdsDataDB
from database.products import ProductsDB
from database.product_sellers import ProductSellersDB
from analytics import AdsAnalyticsProcessor, DataAggregator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_path(tmp_path) -> Path:
    p = tmp_path / "test_phase12.db"
    await DatabaseSchema.init_db(p)
    mig = DatabaseMigrations(p)
    await mig.run_migrations()
    return p


@pytest.fixture
async def ads_db(db_path) -> AdsDataDB:
    return AdsDataDB(str(db_path))


@pytest.fixture
async def products_db(db_path) -> ProductsDB:
    return ProductsDB(str(db_path))


@pytest.fixture
async def ps_db(db_path) -> ProductSellersDB:
    return ProductSellersDB(str(db_path))


@pytest.fixture
async def processor(ads_db, products_db, ps_db) -> AdsAnalyticsProcessor:
    return AdsAnalyticsProcessor(ads_db, products_db, ps_db)


@pytest.fixture
async def aggregator(ads_db, products_db) -> DataAggregator:
    return DataAggregator(ads_db, products_db)


# ---------------------------------------------------------------------------
# Helper: вставить одну рекламную запись
# ---------------------------------------------------------------------------

async def _insert(
    ads_db: AdsDataDB,
    sku: str,
    spend: float = 100.0,
    revenue: float = 0.0,
    clicks: int = 10,
    impressions: int = 1000,
    ctr: float = 1.0,
    cpc: float = 10.0,
    bonus_active: int = 0,
    bonus_percent: float = 0.0,
    source: str = "kaspi_marketing",
    scraped_at: str | None = None,
) -> int:
    return await ads_db.save_campaign(
        {
            "product_sku": sku,
            "scraped_at": scraped_at or now_kz_str(),
            "source": source,
            "impressions": impressions,
            "clicks": clicks,
            "ctr": ctr,
            "spend": spend,
            "cpc": cpc,
            "orders": 1 if revenue > 0 else 0,
            "revenue": revenue,
            "bonus_active": bonus_active,
            "bonus_percent": bonus_percent,
        }
    )


# ===========================================================================
# Блок 1: AdsDataDB — CRUD
# ===========================================================================

@pytest.mark.asyncio
async def test_save_campaign_returns_positive_id(ads_db):
    row_id = await _insert(ads_db, "SKU-001")
    assert isinstance(row_id, int)
    assert row_id > 0


@pytest.mark.asyncio
async def test_get_latest_by_sku_returns_most_recent(ads_db):
    """get_latest_by_sku возвращает запись с наибольшим scraped_at."""
    await _insert(ads_db, "SKU-A", spend=100.0, scraped_at="2024-01-01 10:00:00")
    await _insert(ads_db, "SKU-A", spend=200.0, scraped_at="2024-01-02 10:00:00")

    row = await ads_db.get_latest_by_sku("SKU-A")
    assert row is not None
    assert row["spend"] == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_get_latest_by_sku_nonexistent_returns_none(ads_db):
    result = await ads_db.get_latest_by_sku("NONEXISTENT-SKU")
    assert result is None


@pytest.mark.asyncio
async def test_save_campaigns_batch_returns_count(ads_db):
    items = [
        {
            "product_sku": f"BATCH-{i}",
            "scraped_at": now_kz_str(),
            "source": "kaspi_marketing",
            "impressions": 100 * i,
            "clicks": 10 * i,
            "ctr": 1.0,
            "spend": 50.0 * i,
            "cpc": 5.0,
            "orders": 0,
            "revenue": 0.0,
            "bonus_active": 0,
            "bonus_percent": 0.0,
        }
        for i in range(1, 4)
    ]
    count = await ads_db.save_campaigns_batch(items)
    assert count == 3


@pytest.mark.asyncio
async def test_save_campaigns_batch_empty_returns_zero(ads_db):
    assert await ads_db.save_campaigns_batch([]) == 0


@pytest.mark.asyncio
async def test_get_campaigns_for_period_filters_by_date(ads_db):
    await _insert(ads_db, "SKU-OLD", scraped_at="2023-06-01 12:00:00")
    await _insert(ads_db, "SKU-NEW", scraped_at="2024-06-01 12:00:00")

    rows = await ads_db.get_campaigns_for_period("2024-01-01", "2024-12-31 23:59:59")

    skus = [r["product_sku"] for r in rows]
    assert "SKU-NEW" in skus
    assert "SKU-OLD" not in skus


@pytest.mark.asyncio
async def test_get_top_spenders_sorted_descending(ads_db):
    await _insert(ads_db, "SKU-LOW", spend=100.0)
    await _insert(ads_db, "SKU-HIGH", spend=9000.0)

    rows = await ads_db.get_top_spenders(limit=10)

    assert rows[0]["product_sku"] == "SKU-HIGH"
    assert rows[1]["product_sku"] == "SKU-LOW"


@pytest.mark.asyncio
async def test_get_top_spenders_empty_db(ads_db):
    assert await ads_db.get_top_spenders() == []


@pytest.mark.asyncio
async def test_get_products_without_bonuses_includes_inactive(ads_db):
    """Товар с bonus_active=0 в последней записи входит в список."""
    await _insert(
        ads_db,
        "NO-BNS",
        source="kaspi_bonus",
        bonus_active=0,
        scraped_at="2024-01-01 10:00:00",
    )
    await _insert(
        ads_db,
        "NO-BNS",
        source="kaspi_bonus",
        bonus_active=0,
        scraped_at="2024-01-15 10:00:00",
    )

    rows = await ads_db.get_products_without_bonuses()
    skus = [r["product_sku"] for r in rows]
    assert "NO-BNS" in skus


@pytest.mark.asyncio
async def test_get_products_without_bonuses_uses_bonus_source_only(ads_db):
    """Маркетинговая запись с bonus_active=0 не должна считаться бонусным статусом."""
    ts = "2024-02-01 10:00:00"
    await _insert(ads_db, "SKU-MIXED", source="kaspi_marketing", bonus_active=0, scraped_at=ts)
    await _insert(ads_db, "SKU-MIXED", source="kaspi_bonus", bonus_active=1, scraped_at=ts)

    rows = await ads_db.get_products_without_bonuses()
    skus = [r["product_sku"] for r in rows]
    assert "SKU-MIXED" not in skus


@pytest.mark.asyncio
async def test_get_most_clickable_sorted_by_ctr(ads_db):
    await _insert(ads_db, "LOW-CTR", ctr=0.3, impressions=500)
    await _insert(ads_db, "HIGH-CTR", ctr=9.5, impressions=800)

    rows = await ads_db.get_most_clickable(limit=10)
    assert rows[0]["product_sku"] == "HIGH-CTR"


@pytest.mark.asyncio
async def test_get_bonuses_status_returns_latest_per_sku(ads_db):
    """get_bonuses_status должен брать самую новую запись по каждому SKU."""
    await _insert(
        ads_db, "BNS-SKU", bonus_active=0, source="kaspi_bonus",
        scraped_at="2024-01-01 10:00:00",
    )
    await _insert(
        ads_db, "BNS-SKU", bonus_active=1, bonus_percent=8.0, source="kaspi_bonus",
        scraped_at="2024-01-10 10:00:00",
    )

    rows = await ads_db.get_bonuses_status()

    assert len(rows) == 1
    assert rows[0]["bonus_active"] == 1
    assert rows[0]["bonus_percent"] == pytest.approx(8.0)


@pytest.mark.asyncio
async def test_get_spend_revenue_summary_returns_latest_snapshot(ads_db):
    """Возвращается **последний** XLSX-снапшот по SKU, не сумма по дням.

    Каждая запись `ads_data` уже содержит агрегат за `period_days` дней
    (так его строит скрапер). Суммирование таких снапшотов давало бы
    значение в N раз больше реального — поэтому DAO теперь берёт latest.
    """
    await _insert(ads_db, "AGG-SKU", spend=100.0, revenue=300.0, clicks=10)
    # Более свежий снапшот — он и должен попасть в результат.
    await _insert(ads_db, "AGG-SKU", spend=200.0, revenue=600.0, clicks=20)

    rows = await ads_db.get_spend_revenue_summary(period_days=30, sku="AGG-SKU")

    assert len(rows) == 1
    assert rows[0]["total_spend"] == pytest.approx(200.0)
    assert rows[0]["total_revenue"] == pytest.approx(600.0)
    assert rows[0]["total_clicks"] == 20


@pytest.mark.asyncio
async def test_get_spend_revenue_summary_empty_sku(ads_db):
    rows = await ads_db.get_spend_revenue_summary(period_days=30, sku="NEVER-INSERTED")
    assert rows == []


# ===========================================================================
# Блок 2: AdsAnalyticsProcessor
# ===========================================================================

@pytest.mark.asyncio
async def test_processor_calculate_roi_no_data(processor):
    result = await processor.calculate_roi("GHOST-SKU", period_days=30)
    assert result["roi_percent"] is None
    assert result["spend"] == 0.0
    assert result["has_revenue_data"] is False


@pytest.mark.asyncio
async def test_processor_calculate_roi_positive(processor, ads_db):
    await _insert(ads_db, "ROI-SKU", spend=1000.0, revenue=3000.0)

    result = await processor.calculate_roi("ROI-SKU", period_days=30)

    assert result["roi_percent"] == pytest.approx(200.0)
    assert result["has_revenue_data"] is True


@pytest.mark.asyncio
async def test_processor_calculate_roi_negative(processor, ads_db):
    """ROI < 0 когда revenue < spend."""
    await _insert(ads_db, "NEG-ROI", spend=1000.0, revenue=500.0)

    result = await processor.calculate_roi("NEG-ROI", period_days=30)

    assert result["roi_percent"] is not None
    assert result["roi_percent"] < 0


@pytest.mark.asyncio
async def test_processor_calculate_roas_no_data(processor):
    result = await processor.calculate_roas("GHOST-SKU", period_days=30)
    assert result is None


@pytest.mark.asyncio
async def test_processor_calculate_roas_with_data(processor, ads_db):
    await _insert(ads_db, "ROAS-SKU", spend=500.0, revenue=2000.0)

    result = await processor.calculate_roas("ROAS-SKU", period_days=30)

    assert result == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_processor_calculate_roas_no_revenue_returns_none(processor, ads_db):
    """ROAS = None если revenue == 0 (нет данных о продажах)."""
    await _insert(ads_db, "NO-REV", spend=100.0, revenue=0.0)

    result = await processor.calculate_roas("NO-REV", period_days=30)

    assert result is None


@pytest.mark.asyncio
async def test_processor_get_cpc_efficiency_no_data(processor):
    result = await processor.get_cpc_efficiency("GHOST-SKU")
    assert result["assessment"] == "no_data"
    assert result["efficiency_ratio"] is None


@pytest.mark.asyncio
async def test_processor_wasted_budget_empty_db(processor):
    result = await processor.get_wasted_budget()
    assert result == []


@pytest.mark.asyncio
async def test_processor_wasted_budget_includes_negative_roi(processor, ads_db):
    await _insert(ads_db, "WASTE-SKU", spend=1000.0, revenue=200.0)

    result = await processor.get_wasted_budget(threshold_roi=0.0)

    skus = [r["sku"] for r in result]
    assert "WASTE-SKU" in skus


@pytest.mark.asyncio
async def test_processor_wasted_budget_excludes_profitable(processor, ads_db):
    """Товар с положительным ROI не попадает в wasted_budget."""
    await _insert(ads_db, "PROFIT-SKU", spend=500.0, revenue=2000.0)

    result = await processor.get_wasted_budget(threshold_roi=0.0)

    skus = [r["sku"] for r in result]
    assert "PROFIT-SKU" not in skus


@pytest.mark.asyncio
async def test_processor_wasted_budget_sorted_by_spend_desc(processor, ads_db):
    """Список сортируется по spend DESC (наибольшие убытки — первые)."""
    await _insert(ads_db, "SMALL-WASTE", spend=100.0, revenue=50.0)
    await _insert(ads_db, "BIG-WASTE", spend=5000.0, revenue=100.0)

    result = await processor.get_wasted_budget(threshold_roi=0.0)

    assert result[0]["sku"] == "BIG-WASTE"


@pytest.mark.asyncio
async def test_processor_get_top_performers_empty(processor):
    result = await processor.get_top_performers()
    assert result == []


@pytest.mark.asyncio
async def test_processor_get_top_performers_sorted_by_roas(processor, ads_db):
    await _insert(ads_db, "MED-PERF", spend=1000.0, revenue=1200.0)   # ROAS=1.2
    await _insert(ads_db, "TOP-PERF", spend=500.0, revenue=5000.0)    # ROAS=10.0

    result = await processor.get_top_performers(limit=5)

    assert result[0]["sku"] == "TOP-PERF"
    assert result[0]["roas"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_processor_get_top_performers_excludes_no_revenue(processor, ads_db):
    """Товар без выручки не попадает в top performers."""
    await _insert(ads_db, "NO-REV-PERF", spend=1000.0, revenue=0.0)

    result = await processor.get_top_performers()

    skus = [r["sku"] for r in result]
    assert "NO-REV-PERF" not in skus


@pytest.mark.asyncio
async def test_processor_get_no_bonus_products(processor, ads_db):
    await _insert(ads_db, "NO-BNS-P", source="kaspi_bonus", bonus_active=0)

    result = await processor.get_no_bonus_products()

    skus = [r["sku"] for r in result]
    assert "NO-BNS-P" in skus


@pytest.mark.asyncio
async def test_processor_get_most_clickable_with_product_title(processor, ads_db, db_path):
    """get_most_clickable обогащает данные названием из таблицы products."""
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute(
            "INSERT INTO products (master_sku, url, title) VALUES (?, ?, ?)",
            ("CLK-SKU", "https://kaspi.kz/shop/p/clk", "Кликабельный товар"),
        )
        await db.commit()

    await _insert(ads_db, "CLK-SKU", ctr=9.5, impressions=1000)

    result = await processor.get_most_clickable(limit=5)

    clk = next((r for r in result if r["sku"] == "CLK-SKU"), None)
    assert clk is not None
    assert clk["title"] == "Кликабельный товар"


@pytest.mark.asyncio
async def test_processor_get_most_clickable_unknown_sku_uses_sku_as_title(processor, ads_db):
    """Если product не найден в products — sku используется как title."""
    await _insert(ads_db, "UNKNOWN-SKU", ctr=7.0, impressions=500)

    result = await processor.get_most_clickable(limit=5)

    item = next((r for r in result if r["sku"] == "UNKNOWN-SKU"), None)
    assert item is not None
    assert item["title"] == "UNKNOWN-SKU"


# ===========================================================================
# Блок 3: DataAggregator
# ===========================================================================

@pytest.mark.asyncio
async def test_aggregator_daily_empty_db(aggregator):
    result = await aggregator.aggregate_daily(date(2024, 1, 1))

    assert result["period"] == "daily"
    assert result["total_spend"] == 0.0
    assert result["total_clicks"] == 0


@pytest.mark.asyncio
async def test_aggregator_daily_with_matching_data(aggregator, ads_db):
    await _insert(ads_db, "DAY-SKU", spend=500.0, clicks=25, scraped_at="2024-06-15 10:00:00")

    result = await aggregator.aggregate_daily(date(2024, 6, 15))

    assert result["total_spend"] == pytest.approx(500.0)
    assert result["total_clicks"] == 25


@pytest.mark.asyncio
async def test_aggregator_daily_wrong_date_returns_empty(aggregator, ads_db):
    await _insert(ads_db, "DAY-SKU", scraped_at="2024-06-15 10:00:00")

    result = await aggregator.aggregate_daily(date(2024, 6, 20))

    assert result["total_spend"] == 0.0


@pytest.mark.asyncio
async def test_aggregator_weekly_empty(aggregator):
    result = await aggregator.aggregate_weekly()
    assert result["period"] == "weekly"
    assert result["total_spend"] == 0.0


@pytest.mark.asyncio
async def test_aggregator_monthly_empty(aggregator):
    result = await aggregator.aggregate_monthly()
    assert result["period"] == "monthly"
    assert result["total_spend"] == 0.0


@pytest.mark.asyncio
async def test_aggregator_get_total_stats_empty(aggregator):
    stats = await aggregator.get_total_stats()

    assert stats["total_spend"] == 0.0
    assert stats["products_with_ads"] == 0
    assert "products_without_bonuses" in stats


@pytest.mark.asyncio
async def test_aggregator_get_total_stats_counts_products(aggregator, ads_db):
    await _insert(ads_db, "STAT-1", spend=300.0, revenue=900.0)
    await _insert(ads_db, "STAT-2", spend=700.0, revenue=0.0)

    stats = await aggregator.get_total_stats()

    assert stats["products_with_ads"] == 2
    assert stats["total_spend"] == pytest.approx(1000.0)


@pytest.mark.asyncio
async def test_aggregator_get_trends_no_data_returns_empty(aggregator):
    result = await aggregator.get_trends("GHOST-SKU", days=7)
    assert result == []


@pytest.mark.asyncio
async def test_aggregator_get_trends_fills_missing_days(aggregator, ads_db):
    """Тренды заполняют пропущенные дни нулями для непрерывного графика."""
    today = now_kz().date()
    yesterday = (today - timedelta(days=1)).isoformat() + " 10:00:00"
    await _insert(ads_db, "TREND-SKU", spend=100.0, scraped_at=yesterday)

    result = await aggregator.get_trends("TREND-SKU", days=7)

    # days=7 → 8 точек (7 дней назад + сегодня включительно)
    assert len(result) == 8

    # Только один день с ненулевыми данными
    nonzero = [p for p in result if p["spend"] > 0]
    assert len(nonzero) == 1


@pytest.mark.asyncio
async def test_aggregator_get_trends_day_keys_present(aggregator, ads_db):
    """Каждая точка трендов содержит все необходимые ключи."""
    today = now_kz().date()
    today_str = today.isoformat() + " 12:00:00"
    await _insert(ads_db, "KEY-SKU", scraped_at=today_str)

    result = await aggregator.get_trends("KEY-SKU", days=3)

    required_keys = {"day", "spend", "revenue", "clicks", "impressions", "ctr", "cpc"}
    for point in result:
        assert required_keys.issubset(point.keys())
