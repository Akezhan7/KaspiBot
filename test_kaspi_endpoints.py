"""
Проверка правильного Kaspi API endpoint
"""
import asyncio
import httpx

async def test_kaspi_endpoints():
    """Тестирование разных Kaspi endpoints"""
    
    proxy = "http://ByA1ap:TEDseZ2VYU4H@mproxy.site:10887"
    sku = "113282830"
    
    # Разные возможные endpoints
    endpoints = [
        f"https://kaspi.kz/yml/offer-view/offers/{sku}",
        f"https://kaspi.kz/shop/api/offers/{sku}",
        f"https://kaspi.kz/api/v1/offers/{sku}",
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": f"https://kaspi.kz/shop/p/product-{sku}/",
        "Origin": "https://kaspi.kz",
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }
    
    print(f"🔍 Тестирование Kaspi API endpoints для SKU: {sku}\n")
    
    async with httpx.AsyncClient(proxy=proxy, timeout=20.0, follow_redirects=True) as client:
        for idx, url in enumerate(endpoints, 1):
            print(f"{idx}. {url}")
            
            try:
                response = await client.get(url, headers=headers)
                print(f"   Статус: {response.status_code}")
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        print(f"   ✅ JSON получен!")
                        
                        # Попробуем найти offers
                        if "offers" in data:
                            offers = data["offers"]
                            print(f"   📦 Offers: {len(offers)}")
                            if offers:
                                print(f"   Первый: {offers[0].get('merchantName', 'N/A')}")
                        else:
                            print(f"   Ключи в ответе: {list(data.keys())[:5]}")
                    except:
                        print(f"   Не JSON, длина: {len(response.text)}")
                else:
                    print(f"   ❌ Ошибка: {response.text[:100]}")
                    
            except Exception as e:
                print(f"   ❌ Исключение: {e}")
            
            print()
    
    # Попробуем открыть страницу товара напрямую
    print("="*60)
    print("\n4. Прямой запрос страницы товара:")
    product_url = f"https://kaspi.kz/shop/p/product-{sku}/"
    print(f"   {product_url}")
    
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=20.0, follow_redirects=True) as client:
            response = await client.get(product_url, headers=headers)
            print(f"   Статус: {response.status_code}")
            print(f"   Длина HTML: {len(response.text)}")
            
            # Поищем упоминание API в HTML
            if "offer-view/offers" in response.text:
                print("   ✅ Найдена ссылка на API в HTML")
            if "yml/offer-view" in response.text:
                print("   ✅ Найдена ссылка yml/offer-view в HTML")
                
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(test_kaspi_endpoints())
