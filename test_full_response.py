"""
Полный вывод ответа API для анализа структуры
"""
import asyncio
import json
from parser.kaspi_parser import KaspiParser
from config import Config

async def test_full_response():
    """Получить полный ответ API"""
    sku = "143381154"
    
    parser = KaspiParser(Config.PROXY_URL)
    
    # Делаем запрос напрямую, чтобы получить ВСЁ
    import httpx
    url = f"https://kaspi.kz/yml/offer-view/offers/{sku}"
    json_body = {
        "cityId": "750000000",
        "limit": 50,
        "page": 0
    }
    
    async with httpx.AsyncClient(proxy=Config.PROXY_URL, timeout=30) as client:
        response = await client.post(url, headers=parser.headers, json=json_body)
        data = response.json()
    
    print("=" * 80)
    print("ПОЛНЫЙ ОТВЕТ (КОРЕНЬ):")
    print("=" * 80)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    
    # Ищем title
    print("\n" + "=" * 80)
    print("ПОИСК 'title' НА ВСЕХ УРОВНЯХ:")
    print("=" * 80)
    
    def find_title(obj, path="root"):
        results = []
        if isinstance(obj, dict):
            for key, value in obj.items():
                if 'title' in key.lower():
                    results.append(f"{path}.{key} = {value}")
                if isinstance(value, (dict, list)):
                    results.extend(find_title(value, f"{path}.{key}"))
        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                results.extend(find_title(item, f"{path}[{idx}]"))
        return results
    
    for result in find_title(data):
        print(result)
        
        find_title(offers[0])

if __name__ == "__main__":
    asyncio.run(test_full_response())
