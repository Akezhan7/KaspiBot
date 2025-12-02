"""
Тест парсинга товара 143381154
"""
import asyncio
import sys
import logging
from parser.kaspi_parser import KaspiParser
from config import Config

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def test_product():
    """Тест получения данных товара"""
    url = "https://kaspi.kz/shop/p/20w-143381154/?maSource=dynamicLink&ref=shared_link"
    
    print(f"🔍 Тестируем товар: {url}\n")
    
    # 1. Извлечь SKU
    sku = await KaspiParser.extract_master_sku_async(url)
    print(f"✅ SKU: {sku}\n")
    
    if not sku:
        print("❌ Не удалось извлечь SKU")
        return
    
    # 2. Получить offers
    parser = KaspiParser(Config.PROXY_URL)
    
    # 2a. Пробуем получить через product API
    print("=" * 60)
    print("ПОПЫТКА 1: Product API")
    print("=" * 60)
    product_info = await parser.get_product_info(sku)
    
    if product_info:
        print("✅ Получены данные из product API\n")
        product_name_from_api = (
            product_info.get('title') or 
            product_info.get('name') or 
            product_info.get('productName') or
            'НЕ НАЙДЕНО'
        )
        print(f"Название из product API: {product_name_from_api}\n")
    else:
        print("❌ Product API не вернул данные\n")
        product_name_from_api = None
    
    # 2b. Получаем offers
    print("=" * 60)
    print("ПОПЫТКА 2: Offers API")
    print("=" * 60)
    success, offers = await parser.get_product_offers(sku)
    
    print(f"📦 Успех: {success}")
    print(f"📦 Количество offers: {len(offers)}\n")
    
    if not success or not offers:
        print("❌ Не удалось получить offers")
        return
    
    # 3. Показать первый offer
    print("=" * 60)
    print("ПЕРВЫЙ OFFER (полные данные):")
    print("=" * 60)
    first_offer = offers[0]
    for key, value in first_offer.items():
        print(f"{key}: {value}")
    
    print("\n" + "=" * 60)
    print("ИЗВЛЕЧЕННОЕ НАЗВАНИЕ:")
    print("=" * 60)
    
    # Проверяем все возможные поля для названия
    product_name = (
        first_offer.get('productName') or 
        first_offer.get('name') or 
        first_offer.get('title') or
        'НЕ НАЙДЕНО (из offers)'
    )
    
    print(f"Название из offers: {product_name}")
    
    # Итоговое название
    print("\n" + "=" * 60)
    print("ИТОГОВОЕ НАЗВАНИЕ:")
    print("=" * 60)
    final_name = product_name_from_api or product_name
    print(f"✅ {final_name}")
    
    # 4. Показать всех продавцов
    print("\n" + "=" * 60)
    print(f"ВСЕ ПРОДАВЦЫ ({len(offers)}):")
    print("=" * 60)
    
    for idx, offer in enumerate(offers, 1):
        merchant_id = offer.get('merchantId', '?')
        merchant_name = offer.get('merchantName', '?')
        price = offer.get('price', 0)
        
        print(f"{idx}. {merchant_name}")
        print(f"   ID: {merchant_id}")
        print(f"   Цена: {price:,.0f} ₸\n")

if __name__ == "__main__":
    asyncio.run(test_product())
