"""
Тест извлечения master_sku из разных форматов URL Kaspi
"""
from parser.kaspi_parser import KaspiParser

# Тестовые URL
test_urls = [
    "https://kaspi.kz/shop/p/название-107664472/",
    "https://kaspi.kz/shop/p/название-107664472",
    "https://kaspi.kz/shop/p/107664472/",
    "https://kaspi.kz/shop/p/107664472",
    "https://kaspi.kz/shop/p/test-product-123456789/",
    "https://kaspi.kz/shop/p/test-product-123456789",
    "https://kaspi.kz/shop/p/некое-название-товара-987654321/?ref=shared",
    "https://kaspi.kz/shop/p/навиен-deluxe-16k-16-kvt-dvukhkonturnyj-104886899/",
]

print("🔍 Тестирование извлечения master_sku:\n")

for url in test_urls:
    sku = KaspiParser.extract_master_sku(url)
    status = "✅" if sku else "❌"
    print(f"{status} SKU: {sku or 'НЕ НАЙДЕН'}")
    print(f"   URL: {url}\n")
