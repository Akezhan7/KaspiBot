"""
Тест смены IP через iproxy.online
Проверяет текущий IP, меняет его и проверяет снова
"""
import httpx
import asyncio
from dotenv import load_dotenv
import os

load_dotenv()

PROXY_URL = os.getenv("PROXY_URL")
CHANGE_API = os.getenv("PROXY_CHANGE_API")


async def get_current_ip():
    """Получить текущий IP адрес через прокси"""
    try:
        async with httpx.AsyncClient(proxy=PROXY_URL, timeout=30) as client:
            response = await client.get("https://api.ipify.org?format=json")
            data = response.json()
            return data.get("ip")
    except Exception as e:
        print(f"❌ Ошибка получения IP: {e}")
        return None


async def change_ip():
    """Сменить IP через API"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(CHANGE_API)
            print(f"📡 Ответ API смены IP: {response.status_code}")
            print(f"   Текст: {response.text[:200]}")
            return response.status_code == 200
    except Exception as e:
        print(f"❌ Ошибка смены IP: {e}")
        return False


async def main():
    print("=" * 60)
    print("🔍 ТЕСТ СМЕНЫ IP ЧЕРЕЗ IPROXY.ONLINE")
    print("=" * 60)
    print()
    
    print(f"📌 Прокси: {PROXY_URL}")
    print(f"📌 API смены: {CHANGE_API}")
    print()
    
    # 1. Получить текущий IP
    print("1️⃣ Получение текущего IP...")
    ip_before = await get_current_ip()
    if ip_before:
        print(f"✅ Текущий IP: {ip_before}")
    else:
        print("❌ Не удалось получить IP")
        return
    
    print()
    
    # 2. Сменить IP
    print("2️⃣ Смена IP адреса...")
    success = await change_ip()
    if not success:
        print("❌ Не удалось сменить IP")
        return
    
    print("✅ Запрос на смену отправлен")
    print()
    
    # 3. Подождать
    print("3️⃣ Ожидание 45 секунд...")
    await asyncio.sleep(75)
    print()
    
    # 4. Проверить новый IP
    print("4️⃣ Проверка нового IP...")
    ip_after = await get_current_ip()
    if ip_after:
        print(f"✅ Новый IP: {ip_after}")
    else:
        print("❌ Не удалось получить новый IP")
        return
    
    print()
    print("=" * 60)
    
    # Результат
    if ip_before != ip_after:
        print("✅ УСПЕХ! IP успешно изменен")
        print(f"   Было: {ip_before}")
        print(f"   Стало: {ip_after}")
    else:
        print("⚠️ IP не изменился (это может быть нормально)")
        print(f"   IP: {ip_before}")
    
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
