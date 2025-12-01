"""
Повторная попытка получить телефоны для продавцов без телефонов
"""
import asyncio
import aiosqlite
from parser.kaspi_parser import KaspiParser
from config import Config
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def retry_fetch_phones():
    """Попытаться получить телефоны для продавцов без телефонов"""
    
    db_path = "data/kaspi_monitor.db"
    proxy_url = Config.PROXY_URL
    
    parser = KaspiParser(proxy_url)
    
    print("🔄 Попытка получить телефоны для продавцов без телефонов\n")
    
    async with aiosqlite.connect(db_path) as db:
        # Получаем продавцов без телефонов, у которых есть связь с товаром
        async with db.execute("""
            SELECT DISTINCT 
                s.merchant_id, 
                s.merchant_name,
                ps.product_id
            FROM sellers s
            JOIN product_sellers ps ON s.merchant_id = ps.seller_id
            WHERE (s.phone IS NULL OR s.phone = '')
            AND ps.is_active = 1
            ORDER BY ps.first_seen DESC
            LIMIT 10
        """) as cursor:
            rows = await cursor.fetchall()
        
        if not rows:
            print("✅ Все продавцы уже имеют телефоны!")
            return
        
        print(f"📋 Найдено {len(rows)} продавцов без телефонов\n")
        print("="*60)
        
        success_count = 0
        failed_count = 0
        
        for idx, row in enumerate(rows, 1):
            merchant_id = row[0]
            merchant_name = row[1]
            product_sku = row[2]
            
            print(f"\n{idx}. {merchant_name} (ID: {merchant_id})")
            
            try:
                # Пытаемся получить телефон
                phone = await parser.get_merchant_phone(merchant_id, product_sku)
                
                if phone:
                    # Обновляем в БД
                    await db.execute(
                        "UPDATE sellers SET phone = ? WHERE merchant_id = ?",
                        (phone, merchant_id)
                    )
                    await db.commit()
                    
                    print(f"   ✅ Телефон получен: {phone}")
                    success_count += 1
                else:
                    print(f"   ❌ Телефон не найден")
                    failed_count += 1
                
                # Задержка между запросами
                await asyncio.sleep(2)
                
            except Exception as e:
                print(f"   ❌ Ошибка: {e}")
                failed_count += 1
        
        print("\n" + "="*60)
        print(f"\n📊 РЕЗУЛЬТАТ:")
        print(f"  ✅ Успешно: {success_count}")
        print(f"  ❌ Не найдено: {failed_count}")
        
        if success_count > 0:
            print(f"\n💡 Обновлено {success_count} телефонов в базе данных!")

if __name__ == "__main__":
    asyncio.run(retry_fetch_phones())
