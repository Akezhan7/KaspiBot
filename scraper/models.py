"""
Dataclass-модели для скрапера Kaspi Pay.

Используются MarketingScraper-ом для передачи данных в DAO-слой.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class AdCampaignData:
    """Данные рекламной кампании из раздела «Kaspi Marketing»."""

    product_sku: str
    product_name: str
    impressions: int
    clicks: int
    ctr: float              # CTR в процентах (например 2.5 = 2.5%)
    spend: float            # Затраты в тенге
    cpc: float              # Стоимость одного клика
    period_start: date | None = None
    period_end: date | None = None
    source: str = "kaspi_marketing"

    def to_dao_dict(self, scraped_at: str) -> dict:
        """Конвертация в словарь для AdsDataDB.save_campaign."""
        raw: dict = {"product_name": self.product_name}
        # campaign_name может быть выставлен скрапером после создания объекта
        campaign_name = getattr(self, "_campaign_name", "")
        if campaign_name:
            raw["campaign_name"] = campaign_name
        return {
            "product_sku": self.product_sku,
            "product_name": self.product_name,
            "scraped_at": scraped_at,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "source": self.source,
            "impressions": self.impressions,
            "clicks": self.clicks,
            "ctr": self.ctr,
            "spend": self.spend,
            "cpc": self.cpc,
            "orders": 0,
            "revenue": 0.0,
            "bonus_active": 0,
            "bonus_percent": 0.0,
            "raw_data": raw,
        }


@dataclass
class BonusData:
    """Данные о бонусах продукта из раздела «Бонусы».

    source может быть одним из:
        kaspi_bonus_seller — бонус от продавца (раздел /bonuses/products/)
        kaspi_bonus_review — бонус за отзыв (раздел /bonuses/reviews/)
        kaspi_bonus       — legacy / неизвестный источник (для совместимости)
    """

    product_sku: str
    product_name: str
    bonus_active: bool
    bonus_percent: float
    source: str = "kaspi_bonus"

    def to_dao_dict(self, scraped_at: str) -> dict:
        """Конвертация в словарь для AdsDataDB.save_campaign."""
        raw: dict = {"product_name": self.product_name}
        # campaign_name выставляется скрапером после создания объекта
        campaign_name = getattr(self, "_campaign_name", "")
        if campaign_name:
            raw["campaign_name"] = campaign_name
        return {
            "product_sku": self.product_sku,
            "product_name": self.product_name,
            "scraped_at": scraped_at,
            "period_start": None,
            "period_end": None,
            "source": self.source,
            "impressions": 0,
            "clicks": 0,
            "ctr": 0.0,
            "spend": 0.0,
            "cpc": 0.0,
            "orders": 0,
            "revenue": 0.0,
            "bonus_active": 1 if self.bonus_active else 0,
            "bonus_percent": self.bonus_percent,
            "raw_data": raw,
        }


@dataclass
class ScrapeResult:
    """Итоговый результат полного цикла скрапинга."""

    campaigns: list[AdCampaignData] = field(default_factory=list)
    bonuses: list[BonusData] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    scraped_at: datetime = field(default_factory=datetime.now)

    @property
    def total_items(self) -> int:
        return len(self.campaigns) + len(self.bonuses)

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
