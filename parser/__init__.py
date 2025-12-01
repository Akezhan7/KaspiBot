"""
Parser модуль - Kaspi.kz парсинг и прокси
"""
from .proxy_manager import ProxyManager
from .kaspi_parser import KaspiParser
from .scanner import ProductScanner, NewSellerInfo

__all__ = ['ProxyManager', 'KaspiParser', 'ProductScanner', 'NewSellerInfo']
