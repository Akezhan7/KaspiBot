"""
Поиск правильного URL страницы магазина
"""
import asyncio
import httpx
import re

async def test_merchant_pages():
    """Тестирование разных вариантов URL страницы магазина"""
    
    proxy = "http://ByA1ap:TEDseZ2VYU4H@mproxy.site:10887"
    merchant_id = "1094131"  # ID из примера в ТЗ
    
    # Разные возможные URL
    urls = [
        f"https://kaspi.kz/shop/info/merchant/{merchant_id}",
        f"https://kaspi.kz/shop/info/merchant/{merchant_id}/",
        f"https://kaspi.kz/merchantpage/{merchant_id}",
        f"https://kaspi.kz/shop/{merchant_id}",
        f"https://kaspi.kz/shop/merchant/{merchant_id}",
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "Referer": "https://kaspi.kz/",
    }
    
    print(f"🔍 Поиск правильного URL для merchant_id={merchant_id}\n")
    
    async with httpx.AsyncClient(proxy=proxy, timeout=20.0, follow_redirects=True) as client:
        for idx, url in enumerate(urls, 1):
            print(f"{idx}. {url}")
            
            try:
                response = await client.get(url, headers=headers)
                print(f"   Статус: {response.status_code}")
                
                if response.status_code == 200:
                    html = response.text
                    print(f"   ✅ Страница загружена ({len(html)} байт)")
                    
                    # Ищем телефон
                    phone_pattern = r'"phone":\s*"([^"]+)"'
                    match = re.search(phone_pattern, html)
                    
                    if match:
                        phone = match.group(1)
                        print(f"   📞 НАЙДЕН ТЕЛЕФОН: {phone}")
                    else:
                        print(f"   ⚠️  Телефон не найден в HTML")
                        
                        # Ищем, есть ли вообще merchant в HTML
                        if "BACKEND.components.merchant" in html:
                            print(f"   ✅ Объект BACKEND.components.merchant найден")
                            # Выведем кусок с merchant
                            merchant_start = html.find("BACKEND.components.merchant")
                            snippet = html[merchant_start:merchant_start+500]
                            print(f"\n   Фрагмент:\n   {snippet[:300]}\n")
                        elif merchant_id in html:
                            print(f"   ✅ merchant_id упоминается в HTML")
                        else:
                            print(f"   ❌ Нет упоминаний о merchant")
                else:
                    print(f"   ❌ Ошибка {response.status_code}")
                    
            except Exception as e:
                print(f"   ❌ Исключение: {str(e)[:100]}")
            
            print()
            await asyncio.sleep(1)
    
    # Также попробуем через поиск - открыть реальную страницу товара
    print("=" * 60)
    print("\n🔍 Попытка найти ссылку на магазин через страницу товара:\n")
    
    product_sku = "109619826"
    product_url = f"https://kaspi.kz/shop/p/product-{product_sku}/"
    
    try:
        async with httpx.AsyncClient(proxy=proxy, timeout=20.0, follow_redirects=True) as client:
            response = await client.get(product_url, headers=headers)
            
            if response.status_code == 200:
                html = response.text
                print(f"✅ Страница товара загружена")
                
                # Ищем ссылки на магазины
                merchant_links = re.findall(r'href="([^"]*merchant[^"]*)"', html, re.IGNORECASE)
                
                if merchant_links:
                    print(f"\n📋 Найденные ссылки на магазины:\n")
                    for link in set(merchant_links[:5]):
                        print(f"  {link}")
                else:
                    print("❌ Ссылки на магазины не найдены")
                    
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    asyncio.run(test_merchant_pages())
