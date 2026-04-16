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
from database import (
    DatabaseSchema,
    SellerWorkflowDB,
    MessageLogDB,
    LegalRequestsDB,
    SellersDB,
    ProductsDB,
    ProductSellersDB,
)
from database.migrations import DatabaseMigrations
from parser import ProductScanner
from bot import router, admin_router, NotificationService
from workflow import WorkflowEngine, EscalationScheduler
from whatsapp import GreenAPIClient, MessageClassifier, WhatsAppWebhook


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
workflow_engine: WorkflowEngine = None
escalation_scheduler: EscalationScheduler = None


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
            f"<b>Сканирование завершено</b>\n\n"
            f"Проверено: {result['successful']}/{result['total_products']}\n"
            f"Ошибок: {result['failed']}\n"
            f"Новых продавцов: {result['new_sellers_count']}",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Ошибка ручного сканирования: {e}", exc_info=True)
        await message.answer(f"Ошибка сканирования: {e}")


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
    
    # Применение миграций
    migrations = DatabaseMigrations(Config.DB_PATH)
    applied = await migrations.run_migrations()
    if applied > 0:
        logger.info(f"Применено {applied} миграций БД")
    
    # Уведомление админов о запуске
    await notification_service.send_to_admins(
        "<b>Бот запущен</b>\n\n"
        f"Автоматическое сканирование каждые {Config.SCAN_INTERVAL_HOURS} часов"
    )
    
    logger.info("Бот успешно запущен!")


async def on_shutdown():
    """Действия при остановке бота"""
    logger = logging.getLogger(__name__)
    logger.info("Остановка бота...")
    
    await notification_service.send_to_admins("<b>Бот остановлен</b>")


async def main():
    """Главная функция"""
    global bot, notification_service, scanner, workflow_engine, escalation_scheduler
    
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
        
        # Инициализация DAO
        db_path = str(Config.DB_PATH)
        workflow_db = SellerWorkflowDB(db_path)
        message_log_db = MessageLogDB(db_path)
        legal_db = LegalRequestsDB(db_path)
        sellers_db = SellersDB(db_path)
        products_db = ProductsDB(db_path)
        product_sellers_db = ProductSellersDB(db_path)
        
        # Инициализация WhatsApp клиента
        whatsapp_client = GreenAPIClient(
            api_url=Config.GREEN_API_URL,
            instance_id=Config.GREEN_API_INSTANCE_ID,
            token=Config.GREEN_API_TOKEN,
            media_url=Config.GREEN_API_MEDIA_URL,
        )
        
        # Инициализация LLM-классификатора
        classifier = MessageClassifier(api_key=Config.OPENAI_API_KEY)
        
        # Инициализация движка воронки
        workflow_engine = WorkflowEngine(
            workflow_db=workflow_db,
            message_log_db=message_log_db,
            legal_db=legal_db,
            sellers_db=sellers_db,
            products_db=products_db,
            product_sellers_db=product_sellers_db,
            whatsapp_client=whatsapp_client,
            classifier=classifier,
            notification_service=notification_service,
            scanner=scanner,
        )
        
        # Передать workflow_engine в scanner для интеграции
        scanner.workflow_engine = workflow_engine
        
        # Инициализация WhatsApp webhook-сервера
        ip_whitelist_raw = Config.WHATSAPP_WEBHOOK_IP_WHITELIST
        ip_whitelist = {ip.strip() for ip in ip_whitelist_raw.split(",") if ip.strip()} if ip_whitelist_raw else set()
        
        whatsapp_webhook = WhatsAppWebhook(
            host=Config.WHATSAPP_WEBHOOK_HOST,
            port=Config.WHATSAPP_WEBHOOK_PORT,
            on_incoming_message=lambda phone, text, name, raw: workflow_engine.handle_incoming_message(phone, text, name),
            ip_whitelist=ip_whitelist,
        )
        
        # Инициализация планировщика эскалации
        escalation_scheduler = EscalationScheduler(workflow_engine)
        
        # Dispatcher
        dp = Dispatcher()
        dp.include_router(admin_router)
        dp.include_router(router)
        
        # Добавить обработчики команды /scan и кнопки "Сканировать"
        from aiogram.filters import Command
        from aiogram import F
        
        async def handle_scan_request(message):
            """Общий обработчик для команды /scan и кнопки Сканировать"""
            from bot.handlers import is_admin
            if not is_admin(message.from_user.id):
                await message.answer("Доступно только администраторам")
                return
            
            status_msg = await message.answer(
                "Запускаю сканирование...\n\n"
                "Это может занять несколько минут"
            )
            
            try:
                await manual_scan_handler(message)
            except Exception as e:
                logger.error(f"Ошибка при выполнении scan_command: {e}", exc_info=True)
                await status_msg.edit_text(f"Ошибка: {str(e)}")
        
        @dp.message(Command("scan"))
        async def scan_command(message):
            """Обработчик команды /scan"""
            await handle_scan_request(message)
        
        @dp.message(F.text == "Сканировать")
        async def scan_button(message):
            """Обработчик кнопки Сканировать"""
            await handle_scan_request(message)
        
        # Настройка планировщика
        scheduler = AsyncIOScheduler()
        scheduler.add_job(
            scheduled_scan,
            trigger=IntervalTrigger(hours=Config.SCAN_INTERVAL_HOURS),
            id="scan_products",
            name="Сканирование товаров",
            replace_existing=True
        )
        
        # Задачи эскалации воронки
        escalation_interval = Config.ESCALATION_INTERVAL_MINUTES
        
        scheduler.add_job(
            escalation_scheduler.process_new_sellers,
            trigger=IntervalTrigger(minutes=escalation_interval),
            id="escalation_new_sellers",
            name="Эскалация: новые продавцы → WARN1",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        scheduler.add_job(
            escalation_scheduler.process_warn1_expiry,
            trigger=IntervalTrigger(minutes=escalation_interval),
            id="escalation_warn1_expiry",
            name="Эскалация: WARN1 → WARN2",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        scheduler.add_job(
            escalation_scheduler.process_warn2_expiry,
            trigger=IntervalTrigger(minutes=escalation_interval),
            id="escalation_warn2_expiry",
            name="Эскалация: WARN2 → LEGAL",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        scheduler.add_job(
            escalation_scheduler.process_dialog_timeout,
            trigger=IntervalTrigger(hours=Config.DIALOG_TIMEOUT_CHECK_HOURS),
            id="escalation_dialog_timeout",
            name="Эскалация: таймаут диалогов",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )
        
        # Запуск планировщика
        scheduler.start()
        logger.info(
            f"Планировщик запущен: сканирование каждые {Config.SCAN_INTERVAL_HOURS}ч, "
            f"эскалация каждые {Config.ESCALATION_INTERVAL_MINUTES}мин"
        )
        
        # Startup
        await on_startup()
        
        # Запуск WhatsApp webhook-сервера
        await whatsapp_webhook.start()
        
        # Запуск polling (drop_pending_updates — не обрабатывать старые update-ы)
        try:
            await dp.start_polling(bot, drop_pending_updates=True)
        finally:
            await whatsapp_webhook.stop()
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
