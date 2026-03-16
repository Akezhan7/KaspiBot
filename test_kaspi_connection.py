"""
Тест подключения к Kaspi API
Диагностика проблем со сканированием
"""
import asyncio
import httpx
from config import Config

async def test_without_proxy():
    """Тест БЕЗ прокси"""
    print("\n=== ТЕСТ БЕЗ ПРОКСИ ===")
    
    url = "https://kaspi.kz/yml/offer-view/offers/143381154"
    json_body = {
        "cityId": "750000000",
        "limit": 50,
        "page": 0
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.post(url, headers=Config.KASPI_HEADERS, json=json_body)
            print(f"✅ Статус: {response.status_code}")
            print(f"✅ Ответ получен: {len(response.text)} bytes")
            
            if response.status_code == 200:
                data = response.json()
                offers = data.get("offers", [])
                print(f"✅ Offers найдено: {len(offers)}")
                if offers:
                    print(f"✅ Первый продавец: {offers[0].get('merchantName')}")
            else:
                print(f"❌ Контент: {response.text[:500]}")
                
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        print(f"   Тип ошибки: {type(e).__name__}")


async def test_with_proxy():
    """Тест С прокси"""
    print("\n=== ТЕСТ С ПРОКСИ ===")
    
    if not Config.PROXY_URL:
        print("⚠️ PROXY_URL не настроен")
        return
    
    print(f"Прокси: {Config.PROXY_URL[:30]}...")
    
    url = "https://kaspi.kz/yml/offer-view/offers/143381154"
    json_body = {
        "cityId": "750000000",
        "limit": 50,
        "page": 0
    }
    
    try:
        async with httpx.AsyncClient(
            proxy=Config.PROXY_URL, 
            timeout=30.0, 
            follow_redirects=True
        ) as client:
            response = await client.post(url, headers=Config.KASPI_HEADERS, json=json_body)
            print(f"✅ Статус: {response.status_code}")
            print(f"✅ Ответ получен: {len(response.text)} bytes")
            
            if response.status_code == 200:
                data = response.json()
                offers = data.get("offers", [])
                print(f"✅ Offers найдено: {len(offers)}")
                if offers:
                    print(f"✅ Первый продавец: {offers[0].get('merchantName')}")
            else:
                print(f"❌ Контент: {response.text[:500]}")
                
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        print(f"   Тип ошибки: {type(e).__name__}")


async def test_proxy_connection():
    """Тест подключения к прокси"""
    print("\n=== ТЕСТ ПОДКЛЮЧЕНИЯ К ПРОКСИ ===")
    
    if not Config.PROXY_URL:
        print("⚠️ PROXY_URL не настроен")
        return
    
    print(f"Прокси: {Config.PROXY_URL[:30]}...")
    
    try:
        # Пробуем простой запрос через прокси
        async with httpx.AsyncClient(proxy=Config.PROXY_URL, timeout=10.0) as client:
            response = await client.get("https://httpbin.org/ip")
            print(f"✅ Прокси работает!")
            print(f"✅ IP адрес: {response.json()}")
            
    except Exception as e:
        print(f"❌ Прокси НЕ работает: {e}")
        print(f"   Тип ошибки: {type(e).__name__}")


async def main():
    print("=" * 60)
    print("ДИАГНОСТИКА ПОДКЛЮЧЕНИЯ К KASPI API")
    print("=" * 60)
    
    # 1. Проверка прокси
    await test_proxy_connection()
    
    # 2. Тест без прокси (чтобы понять, работает ли API вообще)
    await test_without_proxy()
    
    # 3. Тест с прокси
    await test_with_proxy()
    
    print("\n" + "=" * 60)
    print("ДИАГНОСТИКА ЗАВЕРШЕНА")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
