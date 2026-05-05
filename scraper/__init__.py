"""
Scraper модуль — Playwright-скрапер Kaspi Pay кабинета.
Auth & Session Manager + Marketing Scraper.
"""
from .browser_manager import BrowserManager
from .auth import KaspiAuthManager
from .marketing import MarketingScraper
from .models import AdCampaignData, BonusData, ScrapeResult

__all__ = [
    "BrowserManager",
    "KaspiAuthManager",
    "MarketingScraper",
    "AdCampaignData",
    "BonusData",
    "ScrapeResult",
]
