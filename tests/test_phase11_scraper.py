"""
Тесты Фазы 11: Marketing Scraper — вспомогательные функции и модели данных.

Покрывает:
- _parse_number / _parse_int: форматы строк Kaspi Pay (тысячи, запятая, ₸, %)
- AdCampaignData.to_dao_dict: структура, типы, значения по умолчанию
- BonusData.to_dao_dict: корректное кодирование bonus_active (bool → int)
- ScrapeResult: total_items, has_errors, add_error
"""
from datetime import date

import pytest

from database.schema import DatabaseSchema
from database.products import ProductsDB
from scraper.marketing import MarketingScraper, _parse_number, _parse_int
from scraper.models import AdCampaignData, BonusData, ScrapeResult


# ===========================================================================
# Блок 1: _parse_number
# ===========================================================================

def test_parse_number_plain_integer():
    assert _parse_number("123") == 123.0


def test_parse_number_with_space_thousands():
    """Разделитель тысяч — обычный пробел."""
    assert _parse_number("1 234") == 1234.0


def test_parse_number_with_nbsp_thousands():
    """Разделитель тысяч — неразрывный пробел \\u00a0 (Kaspi Pay формат)."""
    assert _parse_number("1\u00a0234") == 1234.0


def test_parse_number_decimal_comma():
    """Казахстанский формат: запятая как десятичный разделитель."""
    assert _parse_number("1 234,56") == pytest.approx(1234.56)


def test_parse_number_large_with_spaces_and_comma():
    assert _parse_number("10 000,00") == pytest.approx(10000.00)


def test_parse_number_currency_symbol():
    """Число с символом тенге ₸."""
    assert _parse_number("5 000 ₸") == pytest.approx(5000.0)


def test_parse_number_percent_sign():
    """Число со знаком %."""
    assert _parse_number("2,5 %") == pytest.approx(2.5)


def test_parse_number_zero():
    assert _parse_number("0") == 0.0


def test_parse_number_empty_string():
    assert _parse_number("") == 0.0


def test_parse_number_float_dot():
    """Числа с точкой как десятичным разделителем."""
    assert _parse_number("12.34") == pytest.approx(12.34)


def test_parse_number_pure_text_returns_zero():
    """Строка без цифр → 0.0."""
    assert _parse_number("нет данных") == 0.0


def test_parse_number_large_million():
    assert _parse_number("1 000 000") == 1_000_000.0


def test_parse_number_only_whitespace():
    assert _parse_number("   ") == 0.0


# ===========================================================================
# Блок 2: _parse_int
# ===========================================================================

def test_parse_int_plain():
    assert _parse_int("42") == 42


def test_parse_int_with_thousands():
    assert _parse_int("1 234") == 1234


def test_parse_int_zero():
    assert _parse_int("0") == 0


def test_parse_int_empty():
    assert _parse_int("") == 0


def test_parse_int_returns_int_type():
    result = _parse_int("100")
    assert isinstance(result, int)


def test_parse_int_truncates_float():
    """_parse_int возвращает целую часть."""
    result = _parse_int("3,7")
    assert isinstance(result, int)
    assert result == 3


# ===========================================================================
# Блок 3: AdCampaignData.to_dao_dict
# ===========================================================================

def _campaign(**overrides) -> AdCampaignData:
    defaults = dict(
        product_sku="SKU-001",
        product_name="Тестовый товар",
        impressions=1000,
        clicks=50,
        ctr=5.0,
        spend=2500.0,
        cpc=50.0,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 1, 31),
    )
    defaults.update(overrides)
    return AdCampaignData(**defaults)


def test_campaign_dao_dict_sku():
    assert _campaign().to_dao_dict("2024-01-31T12:00:00")["product_sku"] == "SKU-001"


def test_campaign_dao_dict_scraped_at():
    ts = "2024-01-31T12:00:00"
    assert _campaign().to_dao_dict(ts)["scraped_at"] == ts


def test_campaign_dao_dict_numeric_fields():
    d = _campaign().to_dao_dict("2024-01-31T12:00:00")
    assert d["impressions"] == 1000
    assert d["clicks"] == 50
    assert d["ctr"] == 5.0
    assert d["spend"] == 2500.0
    assert d["cpc"] == 50.0


def test_campaign_dao_dict_period_dates():
    d = _campaign().to_dao_dict("2024-01-31T12:00:00")
    assert d["period_start"] == "2024-01-01"
    assert d["period_end"] == "2024-01-31"


def test_campaign_dao_dict_no_dates_are_none():
    """Кампания без период-дат — period_start / period_end == None."""
    c = AdCampaignData(
        product_sku="SKU-X",
        product_name="Test",
        impressions=0,
        clicks=0,
        ctr=0.0,
        spend=0.0,
        cpc=0.0,
    )
    d = c.to_dao_dict("2024-01-31T12:00:00")
    assert d["period_start"] is None
    assert d["period_end"] is None


def test_campaign_dao_dict_bonus_defaults_zero():
    """Рекламная кампания: бонусы по умолчанию = 0."""
    d = _campaign().to_dao_dict("2024-01-31T12:00:00")
    assert d["bonus_active"] == 0
    assert d["bonus_percent"] == 0.0
    assert d["orders"] == 0
    assert d["revenue"] == 0.0


def test_campaign_dao_dict_source():
    assert _campaign().to_dao_dict("ts")["source"] == "kaspi_marketing"


def test_campaign_dao_dict_raw_data_has_product_name():
    d = _campaign().to_dao_dict("ts")
    assert d["raw_data"]["product_name"] == "Тестовый товар"


# ===========================================================================
# Блок 4: BonusData.to_dao_dict
# ===========================================================================

def test_bonus_active_true_encodes_as_1():
    bonus = BonusData(product_sku="SKU-1", product_name="T", bonus_active=True, bonus_percent=10.0)
    assert bonus.to_dao_dict("2024-01-31")["bonus_active"] == 1


def test_bonus_inactive_encodes_as_0():
    bonus = BonusData(product_sku="SKU-1", product_name="T", bonus_active=False, bonus_percent=0.0)
    assert bonus.to_dao_dict("2024-01-31")["bonus_active"] == 0


def test_bonus_percent_preserved():
    bonus = BonusData(product_sku="SKU-1", product_name="T", bonus_active=True, bonus_percent=7.5)
    assert bonus.to_dao_dict("2024-01-31")["bonus_percent"] == 7.5


def test_bonus_dao_dict_source():
    bonus = BonusData("SKU-X", "Test", True, 5.0)
    assert bonus.to_dao_dict("2024-01-31")["source"] == "kaspi_bonus"


def test_bonus_dao_dict_ad_metrics_are_zero():
    """Бонусная запись не несёт рекламных метрик — все нули."""
    bonus = BonusData("SKU-X", "Test", True, 5.0)
    d = bonus.to_dao_dict("2024-01-31")
    assert d["spend"] == 0.0
    assert d["clicks"] == 0
    assert d["impressions"] == 0
    assert d["cpc"] == 0.0


# ===========================================================================
# Блок 5: ScrapeResult
# ===========================================================================

def test_scrape_result_total_items_empty():
    assert ScrapeResult().total_items == 0


def test_scrape_result_total_items_campaigns():
    result = ScrapeResult(campaigns=[_campaign(), _campaign(product_sku="SKU-002")])
    assert result.total_items == 2


def test_scrape_result_total_items_bonuses():
    result = ScrapeResult(bonuses=[BonusData("SKU-1", "T", True, 5.0)])
    assert result.total_items == 1


def test_scrape_result_total_items_combined():
    result = ScrapeResult(
        campaigns=[_campaign()],
        bonuses=[BonusData("SKU-1", "T", True, 5.0), BonusData("SKU-2", "T2", False, 0.0)],
    )
    assert result.total_items == 3


def test_scrape_result_has_errors_false_default():
    assert ScrapeResult().has_errors is False


def test_scrape_result_add_error_sets_has_errors():
    result = ScrapeResult()
    result.add_error("something failed")
    assert result.has_errors is True


def test_scrape_result_add_error_appends_message():
    result = ScrapeResult()
    result.add_error("err1")
    result.add_error("err2")
    assert len(result.errors) == 2
    assert "err1" in result.errors
    assert "err2" in result.errors


# ===========================================================================
# Блок 6: нормализация SKU отчётов
# ===========================================================================

@pytest.mark.asyncio
async def test_scraper_normalizes_report_row_sku_from_product_name_sku(tmp_path):
    """Если отчёт дал surrogate SKU, но product_name = master_sku, берём реальный SKU."""
    db_path = tmp_path / "sku_normalization.db"
    await DatabaseSchema.init_db(db_path)
    products_db = ProductsDB(str(db_path))
    await products_db.add_product(
        "162393025",
        "https://kaspi.kz/shop/p/zvonok-162393025",
        "Звонок с кнопкой белый",
    )

    scraper = MarketingScraper(browser_context=None, db_path=str(db_path))  # type: ignore[arg-type]
    row = AdCampaignData(
        product_sku="RPT-ABCDEF12",
        product_name="162393025",
        impressions=100,
        clicks=10,
        ctr=10.0,
        spend=500.0,
        cpc=50.0,
    )

    await scraper._normalize_report_product_skus([row])

    assert row.product_sku == "162393025"


@pytest.mark.asyncio
async def test_scraper_normalization_diagnostics_counts_surrogate_rows(tmp_path):
    db_path = tmp_path / "sku_diagnostics.db"
    await DatabaseSchema.init_db(db_path)
    scraper = MarketingScraper(browser_context=None, db_path=str(db_path))  # type: ignore[arg-type]
    rows = [
        AdCampaignData("162393025", "162393025", 100, 10, 10.0, 500.0, 50.0),
        BonusData("RPT-ABCDEF12", "Неизвестный товар", True, 5.0),
    ]

    diagnostics = await scraper.get_report_identity_diagnostics(rows)

    assert diagnostics["total"] == 2
    assert diagnostics["real_sku_count"] == 1
    assert diagnostics["surrogate_sku_count"] == 1


# ===========================================================================
# Блок 7: реальные форматы CSV Kaspi Marketing
# ===========================================================================

def test_parse_marketing_csv_uses_kaspi_product_report_columns():
    """Отчёт Kaspi содержит отдельные SKU, просмотры, CPC и расходы."""
    payload = """\ufeffРекламируемый товар;Товар;Текущий статус;Просмотры;Клики;CTR;Ср. стоим. клика;Расходы на рекламу;Сумма заказов
169799541;Миксер Micser черно-серебристый;Активный;278;2;0,72;41,67;83,33;0,00
"""
    scraper = MarketingScraper(browser_context=None, db_path=":memory:")  # type: ignore[arg-type]

    rows = scraper._parse_marketing_csv(payload)

    assert len(rows) == 1
    row = rows[0]
    assert row.product_sku == "169799541"
    assert row.product_name == "Миксер Micser черно-серебристый"
    assert row.impressions == 278
    assert row.clicks == 2
    assert row.ctr == pytest.approx(0.72)
    assert row.cpc == pytest.approx(41.67)
    assert row.spend == pytest.approx(83.33)


def test_parse_bonus_csv_uses_report_sku_and_does_not_treat_paid_bonus_as_percent():
    """В бонусной выгрузке SKU есть отдельно, а выплаты клиентам не являются процентом."""
    payload = """\ufeffSKU;Наименование;Статус;Просмотры;Клики;Выплачено бонусов клиентам;Осталось товаров по акции
169647440;Чеснокодавка 30324053_569_Чеснокодавка 1 шт, нержавеющая сталь;Активна;277;1;500,00;
"""
    scraper = MarketingScraper(browser_context=None, db_path=":memory:")  # type: ignore[arg-type]

    rows = scraper._parse_bonus_csv(payload)

    assert len(rows) == 1
    row = rows[0]
    assert row.product_sku == "169647440"
    assert row.product_name == "Чеснокодавка 30324053_569_Чеснокодавка 1 шт, нержавеющая сталь"
    assert row.bonus_active is True
    assert row.bonus_percent == 0.0


# ===========================================================================
# Блок 8: регрессии аналитики внешней рекламы и двух типов бонусов
# ===========================================================================

def test_deduplicate_bonuses_keeps_seller_and_review_for_same_sku():
    """Один товар может одновременно иметь бонус продавца и бонус за отзыв."""
    items = [
        BonusData(
            product_sku="169799541",
            product_name="Товар",
            bonus_active=True,
            bonus_percent=0.0,
            source="kaspi_bonus_review",
            period_days=7,
        ),
        BonusData(
            product_sku="169799541",
            product_name="Товар",
            bonus_active=True,
            bonus_percent=0.0,
            source="kaspi_bonus_seller",
            period_days=7,
        ),
    ]

    result = MarketingScraper._deduplicate_bonuses(items)

    assert {(row.product_sku, row.source, row.period_days) for row in result} == {
        ("169799541", "kaspi_bonus_review", 7),
        ("169799541", "kaspi_bonus_seller", 7),
    }


def test_parse_marketing_csv_uses_cost_not_ad_spend_ratio():
    """Внешний отчёт содержит и стоимость, и ДРР; расходом является стоимость."""
    payload = """Наименование;Статус;Клики;Ср. стоим. клика;Стоимость;Сумма заказов;Заказы;В корзину;В избранное;Доля рекламных расходов
Картон;Активная;148;13.79;2041.01;98420;7;4;14;2,1
"""
    scraper = MarketingScraper(browser_context=None, db_path=":memory:")  # type: ignore[arg-type]

    rows = scraper._parse_marketing_csv(payload)

    assert len(rows) == 1
    assert rows[0].spend == pytest.approx(2041.01)


def test_external_campaign_payload_builds_product_report_urls():
    """Внешняя реклама отдаёт кампании через SPA API, а товарный отчёт идёт по campaignId."""
    payload = {
        "data": {
            "items": [
                {"id": 2031727, "name": "Картон"},
                {"id": 2029808, "name": "Топоры"},
            ]
        }
    }
    response_url = (
        "https://marketing.kaspi.kz/external/advertising/products/api/"
        "v1/merchant/947041/campaigns?startDate=2026-06-16&endDate=2026-06-22"
    )

    entries = MarketingScraper._extract_external_campaign_entries(payload, response_url)
    report_url = MarketingScraper._external_products_report_url("947041", "2031727")

    assert entries == [
        {"id": "2031727", "name": "Картон"},
        {"id": "2029808", "name": "Топоры"},
    ]
    assert "merchant/947041/reports/products/xlsx" in report_url
    assert "campaignId=2031727" in report_url


def test_external_campaigns_response_ignores_exists_probe():
    """Ответ `/campaigns/exists` не содержит кампании и не должен перехватываться."""
    class ExistsResponse:
        url = (
            "https://marketing.kaspi.kz/external/advertising/products/api/"
            "v1/merchant/947041/campaigns/exists"
        )

    assert MarketingScraper._is_external_campaigns_api_response(ExistsResponse()) is False


@pytest.mark.asyncio
async def test_scrape_external_marketing_downloads_product_reports_for_each_period(monkeypatch):
    """External SPA API -> список кампаний -> товарные отчёты за 7 и 30 дней."""
    class FakeCampaignsResponse:
        url = (
            "https://marketing.kaspi.kz/external/advertising/products/api/"
            "v1/merchant/947041/campaigns?startDate=2026-06-16&endDate=2026-06-22"
        )

        async def json(self):
            return {"data": {"items": [{"id": 2031727, "name": "Картон"}]}}

    class FakeResponseExpectation:
        def __init__(self, response):
            self.response = response

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        @property
        def value(self):
            async def result():
                return self.response
            return result()

    class FakeReportResponse:
        ok = True
        status = 200

        async def body(self):
            return (
                b"\xd0\xa0\xd0\xb5\xd0\xba\xd0\xbb\xd0\xb0\xd0\xbc\xd0\xb8\xd1\x80\xd1\x83\xd0\xb5\xd0\xbc\xd1\x8b\xd0\xb9 \xd1\x82\xd0\xbe\xd0\xb2\xd0\xb0\xd1\x80;\xd0\xa2\xd0\xbe\xd0\xb2\xd0\xb0\xd1\x80;\xd0\x9a\xd0\xbb\xd0\xb8\xd0\xba\xd0\xb8;\xd0\xa0\xd0\xb0\xd1\x81\xd1\x85\xd0\xbe\xd0\xb4\xd1\x8b \xd0\xbd\xd0\xb0 \xd1\x80\xd0\xb5\xd0\xba\xd0\xbb\xd0\xb0\xd0\xbc\xd1\x83\n"
                b"169799541;\xd0\xa2\xd0\xbe\xd0\xb2\xd0\xb0\xd1\x80;2;83.33\n"
            )

    class FakeRequestContext:
        def __init__(self):
            self.urls = []

        async def get(self, url, timeout):
            self.urls.append(url)
            return FakeReportResponse()

    class FakeBrowserContext:
        def __init__(self):
            self.request = FakeRequestContext()

    class FakePage:
        def __init__(self):
            self.response = FakeCampaignsResponse()
            self.visited_urls = []

        def expect_response(self, predicate, timeout):
            assert predicate(self.response)
            return FakeResponseExpectation(self.response)

        async def goto(self, url, wait_until, timeout):
            self.visited_urls.append(url)

    context = FakeBrowserContext()
    page = FakePage()
    scraper = MarketingScraper(browser_context=context, db_path=":memory:")  # type: ignore[arg-type]

    async def no_delay():
        return None

    monkeypatch.setattr(scraper, "_random_delay", no_delay)
    rows = await scraper._scrape_external_marketing(
        page,  # type: ignore[arg-type]
        "https://marketing.kaspi.kz/external/advertising/products/campaigns?tab=overview",
    )

    assert len(rows) == 2
    assert {row.period_days for row in rows} == {7, 30}
    assert {row.source for row in rows} == {"kaspi_external_ads"}
    assert all(row.product_sku == "169799541" for row in rows)
    assert all("campaignId=2031727" in url for url in context.request.urls)
