"""
Тест HTTP и SOCKS5 прокси с правильными данными
"""
import asyncio
import httpx

async def test_both_proxies():
    """Тестирование HTTP и SOCKS5 прокси"""
    
    # HTTP прокси
    http_proxy = "http://ByA1ap:TEDseZ2VYU4H@mproxy.site:10887"
    
    # SOCKS5 прокси
    socks5_proxy = "socks5://ByA1ap:TEDseZ2VYU4H@bproxy.site:10887"
    
    print("🔍 Тестирование обоих прокси\n")
    
    # Тест 1: HTTP прокси
    print("1️⃣ HTTP прокси (mproxy.site):")
    print(f"   URL: {http_proxy}\n")
    try:
        async with httpx.AsyncClient(proxy=http_proxy, timeout=15.0) as client:
            # Проверка IP
            response = await client.get("https://api.ipify.org?format=json")
            data = response.json()
            print(f"   ✅ IP: {data['ip']}")
            
            # Проверка Kaspi API
            kaspi_url = "https://kaspi.kz/yml/offer-view/offers/113282830"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": "https://kaspi.kz/shop/",
            }
            response = await client.get(kaspi_url, headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                offers = data.get("offers", [])
                print(f"   ✅ Kaspi API работает! Получено {len(offers)} offers")
                if offers:
                    print(f"   Первый продавец: {offers[0].get('merchantName', 'N/A')}")
            else:
                print(f"   ⚠️  Kaspi API статус: {response.status_code}")
                
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
    
    print("\n" + "="*60 + "\n")
    
    # Тест 2: SOCKS5 прокси
    print("2️⃣ SOCKS5 прокси (bproxy.site):")
    print(f"   URL: {socks5_proxy}\n")
    try:
        async with httpx.AsyncClient(proxy=socks5_proxy, timeout=15.0) as client:
            # Проверка IP
            response = await client.get("https://api.ipify.org?format=json")
            data = response.json()
            print(f"   ✅ IP: {data['ip']}")
            
            # Проверка Kaspi API
            kaspi_url = "https://kaspi.kz/yml/offer-view/offers/113282830"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
                "Referer": "https://kaspi.kz/shop/",
            }
            response = await client.get(kaspi_url, headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                offers = data.get("offers", [])
                print(f"   ✅ Kaspi API работает! Получено {len(offers)} offers")
                if offers:
                    print(f"   Первый продавец: {offers[0].get('merchantName', 'N/A')}")
            else:
                print(f"   ⚠️  Kaspi API статус: {response.status_code}")
                
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
    
    print("\n" + "="*60)
    print("\n💡 Рекомендация: используйте тот прокси, который работает лучше")

if __name__ == "__main__":
    asyncio.run(test_both_proxies())
