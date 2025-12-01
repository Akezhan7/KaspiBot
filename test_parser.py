"""
Тест парсинга Kaspi API
Проверка получения offers для товара
"""
import asyncio
from parser.kaspi_parser import KaspiParser
from config import Config

async def test_parser():
    """Тест парсера с реальным товаром"""
    
    # SKU для теста (замените на реальный)
    test_sku = "113282830"
    
    print(f"🔍 Тестирование парсера для SKU: {test_sku}\n")
    
    # Создаем парсер
    proxy_url = Config.PROXY_URL if Config.PROXY_URL else None
    if proxy_url:
        print(f"✅ Используется прокси: {proxy_url[:30]}...")
    else:
        print("⚠️  Работа без прокси")
    
    parser = KaspiParser(proxy_url)
    
    # Получаем offers
    print(f"\n📦 Запрос offers для SKU {test_sku}...")
    success, offers = await parser.get_product_offers(test_sku)
    
    if not success:
        print("❌ Не удалось получить offers")
        return
    
    print(f"✅ Получено {len(offers)} offers\n")
    
    # Выводим информацию о продавцах
    for idx, offer in enumerate(offers[:5], 1):  # Первые 5
        parsed = parser.parse_offer(offer)
        print(f"{idx}. {parsed['merchant_name']}")
        print(f"   ID: {parsed['merchant_id']}")
        print(f"   Цена: {parsed['price']:,.0f} ₸\n")
    
    if len(offers) > 5:
        print(f"   ... и еще {len(offers) - 5} продавцов\n")
    
    # Тест получения телефона (для первого продавца)
    if offers:
        first_offer = parser.parse_offer(offers[0])
        merchant_id = first_offer['merchant_id']
        
        print(f"📞 Попытка получить телефон для merchant_id={merchant_id}...")
        phone = await parser.get_merchant_phone(merchant_id)
        
        if phone:
            print(f"✅ Телефон: {phone}")
        else:
            print("⚠️  Телефон не найден")

if __name__ == "__main__":
    asyncio.run(test_parser())
