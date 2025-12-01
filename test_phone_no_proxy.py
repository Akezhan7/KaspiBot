"""
Тест получения телефона БЕЗ прокси (для проверки URL)
"""
import asyncio
import httpx
import re

async def test_phone_no_proxy():
    """Тестирование получения телефона без прокси"""
    
    merchant_id = "30398108"
    product_sku = "109619826"
    
    url = f"https://kaspi.kz/shop/info/merchant/{merchant_id}/review/?productCode={product_sku}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": f"https://kaspi.kz/shop/p/product-{product_sku}/",
    }
    
    print(f"🔍 Тестирование БЕЗ прокси (прямое подключение)\n")
    print(f"URL: {url}\n")
    
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            
            print(f"Статус: {response.status_code}")
            
            if response.status_code == 200:
                html = response.text
                print(f"✅ Страница загружена ({len(html)} байт)\n")
                
                # Ищем BACKEND.components.merchant
                if "BACKEND.components.merchant" in html:
                    print("✅ Объект BACKEND.components.merchant найден\n")
                    
                    # Пробуем извлечь телефон
                    patterns = [
                        r'"phone":\s*"([^"]+)"',
                    ]
                    
                    for pattern in patterns:
                        match = re.search(pattern, html)
                        if match:
                            phone = match.group(1)
                            print(f"✅ ТЕЛЕФОН НАЙДЕН: {phone}\n")
                            
                            # Покажем контекст
                            pos = match.start()
                            context = html[max(0, pos-100):pos+100]
                            print(f"Контекст:\n{context}\n")
                            return phone
                    
                    print("❌ Телефон не найден regex\n")
                    
                    # Выведем кусок объекта merchant
                    start = html.find("BACKEND.components.merchant")
                    snippet = html[start:start+1000]
                    print(f"Фрагмент объекта:\n{snippet}\n")
                    
                else:
                    print("❌ Объект BACKEND.components.merchant НЕ найден")
                    
                    # Поищем другие варианты
                    if "merchant" in html.lower():
                        print("⚠️  Слово 'merchant' встречается в HTML")
                    
            else:
                print(f"❌ Ошибка: {response.status_code}")
                
    except Exception as e:
        print(f"❌ Исключение: {e}")

if __name__ == "__main__":
    asyncio.run(test_phone_no_proxy())
