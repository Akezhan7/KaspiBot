"""
Тест получения телефона с правильным URL
"""
import asyncio
import httpx
import re

async def test_phone_extraction():
    """Тестирование получения телефона"""
    
    proxy = "http://ByA1ap:TEDseZ2VYU4H@mproxy.site:10887"
    
    # Данные из вашего примера
    merchant_id = "30398108"
    product_sku = "109619826"
    
    url = f"https://kaspi.kz/shop/info/merchant/{merchant_id}/review/?productCode={product_sku}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": f"https://kaspi.kz/shop/p/product-{product_sku}/",
    }
    
    print(f"🔍 Тестирование получения телефона\n")
    print(f"URL: {url}\n")
    
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=20.0, follow_redirects=True) as client:
            response = await client.get(url, headers=headers)
            
            print(f"Статус: {response.status_code}")
            
            if response.status_code == 200:
                html = response.text
                print(f"✅ Страница загружена ({len(html)} байт)\n")
                
                # Ищем BACKEND.components.merchant
                if "BACKEND.components.merchant" in html:
                    print("✅ Объект BACKEND.components.merchant найден\n")
                    
                    # Извлекаем фрагмент
                    start = html.find("BACKEND.components.merchant")
                    end = html.find("}", start) + 1
                    
                    # Ищем закрывающую скобку правильно
                    bracket_count = 0
                    for i, char in enumerate(html[start:start+2000]):
                        if char == '{':
                            bracket_count += 1
                        elif char == '}':
                            bracket_count -= 1
                            if bracket_count == 0:
                                end = start + i + 1
                                break
                    
                    merchant_obj = html[start:end]
                    print("📦 Объект merchant:")
                    print(merchant_obj[:800])
                    print("\n" + "="*60 + "\n")
                    
                    # Пробуем извлечь телефон
                    patterns = [
                        r'"phone":\s*"([^"]+)"',
                        r'BACKEND\.components\.merchant.*?"phone":\s*"([^"]+)"'
                    ]
                    
                    for pattern in patterns:
                        match = re.search(pattern, html, re.DOTALL)
                        if match:
                            phone = match.group(1)
                            print(f"✅ ТЕЛЕФОН НАЙДЕН: {phone}")
                            break
                    else:
                        print("❌ Телефон не найден в HTML")
                        
                        # Поищем любые телефоны в тексте
                        phone_patterns = [
                            r'\+7\s*\(\d{3}\)\s*\d{3}[- ]?\d{2}[- ]?\d{2}',
                            r'\+7\d{10}',
                            r'8\s*\(\d{3}\)\s*\d{3}[- ]?\d{2}[- ]?\d{2}'
                        ]
                        
                        print("\n🔍 Поиск телефонов в тексте:")
                        for pattern in phone_patterns:
                            matches = re.findall(pattern, html)
                            if matches:
                                print(f"  Найдено: {set(matches)}")
                else:
                    print("❌ Объект BACKEND.components.merchant НЕ найден")
                    
            else:
                print(f"❌ Ошибка: {response.status_code}")
                print(response.text[:500])
                
    except Exception as e:
        print(f"❌ Исключение: {e}")

if __name__ == "__main__":
    asyncio.run(test_phone_extraction())
