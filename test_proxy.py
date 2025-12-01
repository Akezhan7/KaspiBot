"""
Тест прокси подключения
"""
import asyncio
import httpx
from config import Config

async def test_proxy():
    """Проверка работы прокси"""
    
    proxy_url = Config.PROXY_URL
    
    print("🔍 Тестирование прокси\n")
    print(f"Прокси URL: {proxy_url}\n")
    
    # Тест 1: Без прокси
    print("1️⃣ Тест БЕЗ прокси:")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get("https://api.ipify.org?format=json")
            data = response.json()
            print(f"   ✅ IP: {data['ip']}\n")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}\n")
    
    # Тест 2: С прокси
    print("2️⃣ Тест С прокси:")
    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=10.0) as client:
            response = await client.get("https://api.ipify.org?format=json")
            data = response.json()
            print(f"   ✅ IP через прокси: {data['ip']}\n")
    except Exception as e:
        print(f"   ❌ Ошибка: {e}\n")
    
    # Тест 3: Kaspi API через прокси
    print("3️⃣ Тест Kaspi API через прокси:")
    test_sku = "113282830"
    url = f"https://kaspi.kz/yml/offer-view/offers/{test_sku}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://kaspi.kz/shop/",
    }
    
    try:
        async with httpx.AsyncClient(proxy=proxy_url, timeout=30.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            print(f"   Status: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                offers = data.get("offers", [])
                print(f"   ✅ Получено {len(offers)} offers")
                
                if offers:
                    print(f"   Первый продавец: {offers[0].get('merchantName', 'N/A')}")
            else:
                print(f"   ❌ Ошибка: {response.text[:200]}")
                
    except Exception as e:
        print(f"   ❌ Ошибка: {e}\n")

if __name__ == "__main__":
    asyncio.run(test_proxy())
