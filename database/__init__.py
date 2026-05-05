"""
Database модуль - единая точка входа для работы с БД
"""
from .schema import DatabaseSchema
from .products import ProductsDB
from .sellers import SellersDB
from .product_sellers import ProductSellersDB
from .scan_logs import ScanLogsDB
from .recent_sellers import RecentSellersDB
from .seller_workflow import SellerWorkflowDB
from .message_log import MessageLogDB
from .legal_requests import LegalRequestsDB
from .ads_data import AdsDataDB, ScrapeLogsDB, BrowserSessionsDB

__all__ = [
    'DatabaseSchema',
    'ProductsDB',
    'SellersDB',
    'ProductSellersDB',
    'ScanLogsDB',
    'RecentSellersDB',
    'SellerWorkflowDB',
    'MessageLogDB',
    'LegalRequestsDB',
    'AdsDataDB',
    'ScrapeLogsDB',
    'BrowserSessionsDB',
]
