"""
Kaspi Sellers Monitor Bot
Главный файл приложения
"""
import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config import Config
from database import DatabaseSchema
from parser import ProductScanner
from bot import router, NotificationService


# === НАСТРОЙКА ЛОГИРОВАНИЯ ===
def setup_logging():
    """Настройка логирования"""
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    # Создать директорию для логов
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # Настройка handlers
    handlers = [
        logging.StreamHandler(sys.stdout),  # Консоль
        logging.FileHandler(log_dir / "bot.log", encoding="utf-8")  # Файл
    ]
    
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=handlers
    )
    
    # Отключить verbose логи библиотек
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("apscheduler").setLevel(logging.INFO)


# === ГЛОБАЛЬНЫЕ ОБЪЕКТЫ ===
bot: Bot = None
notification_service: NotificationService = None
scanner: ProductScanner = None


# === ЗАДАЧИ ПЛАНИРОВЩИКА ===
async def scheduled_scan():
    """Автоматическое сканирование по расписанию"""
    logger = logging.getLogger(__name__)
    logger.info("🔄 Начало запланированного сканирования")
    
    try:
        # Выполнить сканирование
        result = await scanner.scan_all_products()
        
        # Отправить уведомления о новых продавцах
        new_sellers = result['new_sellers']
        if new_sellers:
            await notification_service.notify_new_sellers(new_sellers)
        
        # Уведомление о завершении
        await notification_service.notify_scan_complete(
            total=result['total_products'],
            successful=result['successful'],
            failed=result['failed'],
            new_sellers_count=result['new_sellers_count']
        )
        
    except Exception as e:
        logger.error(f"Ошибка запланированного сканирования: {e}", exc_info=True)
        await notification_service.notify_scan_error(str(e))


async def manual_scan_handler(message):
    """Обработчик ручного сканирования через команду /scan"""
    logger = logging.getLogger(__name__)
    
    try:
        # Выполнить сканирование
        result = await scanner.scan_all_products()
        
        # Отправить уведомления
        new_sellers = result['new_sellers']
        if new_sellers:
            await notification_service.notify_new_sellers(new_sellers)
        
        # Ответ пользователю
        await message.answer(
            f"✅ <b>Сканирование завершено</b>\n\n"
            f"📦 Проверено: {result['successful']}/{result['total_products']}\n"
            f"❌ Ошибок: {result['failed']}\n"
            f"🆕 Новых продавцов: {result['new_sellers_count']}",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Ошибка ручного сканирования: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка сканирования: {e}")


# === ОСНОВНЫЕ ФУНКЦИИ ===
async def on_startup():
    """Действия при запуске бота"""
    logger = logging.getLogger(__name__)
    
    logger.info("=" * 50)
    logger.info("ЗАПУСК KASPI SELLERS MONITOR BOT")
    logger.info("=" * 50)
    
    # Инициализация базы данных
    logger.info("Инициализация базы данных...")
    await DatabaseSchema.init_db(Config.DB_PATH)
    
    # Уведомление админов о запуске
    await notification_service.send_to_admins(
        "✅ <b>Бот запущен</b>\n\n"
        f"Автоматическое сканирование каждые {Config.SCAN_INTERVAL_HOURS} часов"
    )
    
    logger.info("Бот успешно запущен!")


async def on_shutdown():
    """Действия при остановке бота"""
    logger = logging.getLogger(__name__)
    logger.info("Остановка бота...")
    
    await notification_service.send_to_admins("⚠️ <b>Бот остановлен</b>")


async def main():
    """Главная функция"""
    global bot, notification_service, scanner
    
    # Настройка логирования
    setup_logging()
    logger = logging.getLogger(__name__)
    
    try:
        # Инициализация бота
        bot = Bot(
            token=Config.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        )
        
        # Инициализация сервисов
        notification_service = NotificationService(bot)
        scanner = ProductScanner()
        
        # Dispatcher
        dp = Dispatcher()
        dp.include_router(router)
        
        # Добавить обработчик команды /scan
        from aiogram.filters import Command
        
        @dp.message(Command("scan"))
        async def scan_command(message):
            from bot.handlers import is_admin
            if not is_admin(message.from_user.id):
                await message.answer("⛔️ Доступно только администраторам")
                return
            
            await message.answer("🔄 Запускаю сканирование...")
            await manual_scan_handler(message)
        
        # Настройка планировщика
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            scheduled_scan,
            trigger=IntervalTrigger(hours=Config.SCAN_INTERVAL_HOURS),
            id="scan_products",
            name="Сканирование товаров",
            replace_existing=True
        )
        
        # Запуск планировщика
        scheduler.start()
        logger.info(f"Планировщик запущен: сканирование каждые {Config.SCAN_INTERVAL_HOURS} часов")
        
        # Startup
        await on_startup()
        
        # Запуск polling
        try:
            await dp.start_polling(bot)
        finally:
            await on_shutdown()
            scheduler.shutdown()
            await bot.session.close()
            
    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен пользователем (Ctrl+C)")
    except Exception as e:
        logging.critical(f"Неожиданная ошибка: {e}", exc_info=True)
        sys.exit(1)
