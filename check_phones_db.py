"""
Проверка телефонов в базе данных
"""
import asyncio
import aiosqlite

async def check_phones_in_db():
    """Проверить наличие телефонов у продавцов в БД"""
    
    db_path = "data/kaspi_monitor.db"
    
    print("🔍 Проверка телефонов в базе данных\n")
    print("="*60)
    
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        
        # Общая статистика
        async with db.execute("SELECT COUNT(*) FROM sellers") as cursor:
            total_sellers = (await cursor.fetchone())[0]
        
        async with db.execute("SELECT COUNT(*) FROM sellers WHERE phone IS NOT NULL AND phone != ''") as cursor:
            with_phone = (await cursor.fetchone())[0]
        
        without_phone = total_sellers - with_phone
        
        print(f"\n📊 СТАТИСТИКА:")
        print(f"  Всего продавцов: {total_sellers}")
        print(f"  ✅ С телефоном: {with_phone}")
        print(f"  ❌ Без телефона: {without_phone}")
        print(f"  📈 Процент: {(with_phone/total_sellers*100):.1f}%\n")
        
        print("="*60)
        
        # Показываем продавцов БЕЗ телефонов
        print(f"\n❌ ПРОДАВЦЫ БЕЗ ТЕЛЕФОНОВ ({without_phone}):\n")
        
        async with db.execute("""
            SELECT merchant_id, merchant_name 
            FROM sellers 
            WHERE phone IS NULL OR phone = ''
            ORDER BY created_at DESC
        """) as cursor:
            rows = await cursor.fetchall()
            
            for idx, row in enumerate(rows, 1):
                print(f"{idx}. {row['merchant_name']} (ID: {row['merchant_id']})")
        
        print("\n" + "="*60)
        
        # Показываем продавцов С телефонами (первые 10)
        print(f"\n✅ ПРОДАВЦЫ С ТЕЛЕФОНАМИ (первые 10):\n")
        
        async with db.execute("""
            SELECT merchant_id, merchant_name, phone 
            FROM sellers 
            WHERE phone IS NOT NULL AND phone != ''
            ORDER BY created_at DESC
            LIMIT 10
        """) as cursor:
            rows = await cursor.fetchall()
            
            for idx, row in enumerate(rows, 1):
                print(f"{idx}. {row['merchant_name']}")
                print(f"   📞 {row['phone']}")
                print()

if __name__ == "__main__":
    asyncio.run(check_phones_in_db())
