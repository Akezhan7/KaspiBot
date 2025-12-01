"""
Тест POST запросов к Kaspi API
"""
import asyncio
import httpx
import json

async def test_kaspi_post():
    """Тестирование POST запросов"""
    
    proxy = "http://ByA1ap:TEDseZ2VYU4H@mproxy.site:10887"
    sku = "113282830"
    
    url = f"https://kaspi.kz/yml/offer-view/offers/{sku}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Content-Type": "application/json",
        "Referer": f"https://kaspi.kz/shop/p/product-{sku}/",
        "Origin": "https://kaspi.kz",
    }
    
    print(f"🔍 Тестирование POST запроса к Kaspi API\n")
    print(f"URL: {url}\n")
    
    # Тест 1: POST без body
    print("1️⃣ POST без body:")
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=20.0) as client:
            response = await client.post(url, headers=headers)
            print(f"   Статус: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"   ✅ Успех!")
                offers = data.get("offers", [])
                print(f"   Offers: {len(offers)}")
                if offers:
                    print(f"   Первый: {offers[0].get('merchantName')}")
            else:
                print(f"   Ответ: {response.text[:200]}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
    
    print()
    
    # Тест 2: POST с пустым JSON
    print("2️⃣ POST с пустым JSON body:")
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=20.0) as client:
            response = await client.post(url, headers=headers, json={})
            print(f"   Статус: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"   ✅ Успех!")
                offers = data.get("offers", [])
                print(f"   Offers: {len(offers)}")
                if offers:
                    for i, offer in enumerate(offers[:3], 1):
                        print(f"   {i}. {offer.get('merchantName')} - {offer.get('price')} ₸")
            else:
                print(f"   Ответ: {response.text[:200]}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
    
    print()
    
    # Тест 3: POST с параметрами
    print("3️⃣ POST с параметрами:")
    body = {
        "cityId": "750000000",  # Алматы
        "limit": 50,
        "page": 0
    }
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=20.0) as client:
            response = await client.post(url, headers=headers, json=body)
            print(f"   Статус: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"   ✅ Успех!")
                offers = data.get("offers", [])
                print(f"   Offers: {len(offers)}")
                if offers:
                    for i, offer in enumerate(offers[:3], 1):
                        print(f"   {i}. {offer.get('merchantName')} - {offer.get('price')} ₸")
            else:
                print(f"   Ответ: {response.text[:200]}")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(test_kaspi_post())
