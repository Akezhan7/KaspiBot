"""
Проверка данных в offers - может там уже есть контакты?
"""
import asyncio
import httpx
import json

async def check_offers_data():
    """Проверяем, какие данные приходят в offers"""
    
    proxy = "http://ByA1ap:TEDseZ2VYU4H@mproxy.site:10887"
    sku = "109619826"
    
    url = f"https://kaspi.kz/yml/offer-view/offers/{sku}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": f"https://kaspi.kz/shop/p/product-{sku}/",
        "Origin": "https://kaspi.kz",
    }
    
    body = {
        "cityId": "750000000",
        "limit": 3,  # Только первые 3 для примера
        "page": 0
    }
    
    print(f"🔍 Проверка данных в offers для SKU: {sku}\n")
    
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=20.0) as client:
            response = await client.post(url, headers=headers, json=body)
            
            if response.status_code == 200:
                data = response.json()
                offers = data.get("offers", [])
                
                print(f"✅ Получено {len(offers)} offers\n")
                print("=" * 60)
                
                # Выводим ПОЛНЫЕ данные первого offer
                if offers:
                    first_offer = offers[0]
                    print("\n📦 ПОЛНЫЕ ДАННЫЕ ПЕРВОГО OFFER:\n")
                    print(json.dumps(first_offer, indent=2, ensure_ascii=False))
                    
                    print("\n" + "=" * 60)
                    print("\n🔑 ВСЕ КЛЮЧИ В OFFER:\n")
                    for key in first_offer.keys():
                        print(f"  - {key}: {type(first_offer[key]).__name__}")
                    
            else:
                print(f"❌ Ошибка: {response.status_code}")
                print(response.text[:500])
                
    except Exception as e:
        print(f"❌ Исключение: {e}")

if __name__ == "__main__":
    asyncio.run(check_offers_data())
