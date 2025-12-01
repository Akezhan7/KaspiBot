"""
Тест разных форматов прокси
"""
import asyncio
import httpx

async def test_proxy_formats():
    """Тестирование разных форматов прокси"""
    
    # Данные из .env
    login = "ByA1ap"
    password = "TEDseZ2YVU4H"
    host = "mproxy.site"
    port = "10887"
    
    formats = [
        f"http://{login}:{password}@{host}:{port}",
        f"http://{host}:{port}",  # Без авторизации
        f"socks5://{login}:{password}@{host}:{port}",  # SOCKS5
    ]
    
    print("🔍 Тестирование форматов прокси\n")
    
    for idx, proxy_url in enumerate(formats, 1):
        print(f"{idx}. Формат: {proxy_url[:50]}...")
        
        try:
            # Попытка с авторизацией через auth параметр
            if idx == 1:
                async with httpx.AsyncClient(
                    proxy=proxy_url,
                    timeout=10.0
                ) as client:
                    response = await client.get("https://api.ipify.org?format=json")
                    data = response.json()
                    print(f"   ✅ Успех! IP: {data['ip']}\n")
            else:
                async with httpx.AsyncClient(proxy=proxy_url, timeout=10.0) as client:
                    response = await client.get("https://api.ipify.org?format=json")
                    data = response.json()
                    print(f"   ✅ Успех! IP: {data['ip']}\n")
                    
        except Exception as e:
            print(f"   ❌ Ошибка: {str(e)[:100]}\n")
    
    # Попробуем с явным указанием auth
    print("4. С использованием httpx.Auth:")
    try:
        auth = httpx.BasicAuth(username=login, password=password)
        proxy_without_auth = f"http://{host}:{port}"
        
        async with httpx.AsyncClient(
            proxy=proxy_without_auth,
            timeout=10.0,
            auth=auth
        ) as client:
            response = await client.get("https://api.ipify.org?format=json")
            data = response.json()
            print(f"   ✅ Успех! IP: {data['ip']}\n")
    except Exception as e:
        print(f"   ❌ Ошибка: {str(e)[:100]}\n")

if __name__ == "__main__":
    asyncio.run(test_proxy_formats())
