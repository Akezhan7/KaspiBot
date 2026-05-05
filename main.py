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
from bot import router, admin_router, scraper_router, set_auth_manager, set_marketing_scraper, NotificationService
from workflow import WorkflowEngine, EscalationScheduler
from whatsapp import GreenAPIClient, MessageClassifier, WhatsAppWebhook
from scraper import BrowserManager, KaspiAuthManager, MarketingScraper
from database.ads_data import AdsDataDB, ScrapeLogsDB
from analytics import AdsAnalyticsProcessor, DataAggregator
from api import TMAApiServer


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
browser_manager: BrowserManager = None
kaspi_auth: KaspiAuthManager = None
marketing_scraper: MarketingScraper = None


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


async def scheduled_scrape_marketing():
    """Ежедневный автоматический сбор маркетинговых данных из Kaspi Pay."""
    logger = logging.getLogger(__name__)
    logger.info("Начало запланированного скрапинга Kaspi Marketing")

    if not marketing_scraper or not kaspi_auth:
        logger.info("Kaspi Marketing scraper не инициализирован — пропуск задачи")
        return

    db_path = str(Config.DB_PATH)
    scrape_logs_db = ScrapeLogsDB(db_path)
    ads_data_db = AdsDataDB(db_path)
    log_id = await scrape_logs_db.create_log()

    try:
        # Проверяем сессию; при истечении пытаемся автоматически перелогиниться.
        if not await kaspi_auth.is_session_valid():
            logger.warning("Kaspi Pay: сессия невалидна, пробуем автоматическую авторизацию")
            relogin_ok = await kaspi_auth.login()
            if not relogin_ok:
                msg = (
                    "Kaspi Pay: сессия истекла и авто-авторизация не удалась — сбор данных пропущен.\n"
                    "Выполните /login_kaspi для ручной авторизации."
                )
                logger.warning(msg)
                await notification_service.send_to_admins(msg)
                await scrape_logs_db.update_log(
                    log_id,
                    status="failed",
                    errors="Сессия истекла, авто-авторизация не удалась",
                )
                return

        result = await marketing_scraper.scrape_all()
        scraped_at = result.scraped_at.strftime("%Y-%m-%d %H:%M:%S")

        campaign_dicts = [c.to_dao_dict(scraped_at) for c in result.campaigns]
        saved_campaigns = await ads_data_db.save_campaigns_batch(campaign_dicts)

        bonus_dicts = [b.to_dao_dict(scraped_at) for b in result.bonuses]
        saved_bonuses = await ads_data_db.save_campaigns_batch(bonus_dicts)

        total_saved = saved_campaigns + saved_bonuses
        errors_text = "\n".join(result.errors) if result.errors else None
        status = "completed" if not result.errors else "completed_with_errors"

        await scrape_logs_db.update_log(
            log_id,
            status=status,
            products_scraped=total_saved,
            errors=errors_text,
        )

        summary = (
            f"Kaspi Marketing: сбор завершён\n"
            f"Кампаний: {len(result.campaigns)}, бонусов: {len(result.bonuses)}\n"
            f"Сохранено: {total_saved} записей"
        )
        if result.errors:
            summary += f"\n⚠️ Ошибок: {len(result.errors)}"
        logger.info(summary)

        if result.errors:
            await notification_service.send_to_admins(summary)

    except Exception as e:
        logger.error("Критическая ошибка при плановом скрапинге: %s", e, exc_info=True)
        await scrape_logs_db.update_log(log_id, status="failed", errors=str(e))
        await notification_service.send_to_admins(
            f"Kaspi Marketing: ошибка планового скрапинга — {e}"
        )


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
    global browser_manager, kaspi_auth, marketing_scraper
    tma_api_server: TMAApiServer = None
    
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
        
        # Инициализация Kaspi Pay scraper (если настроен любой способ auth)
        if Config.is_kaspi_pay_enabled():
            browser_manager = BrowserManager(
                storage_state_path=Config.KASPI_STORAGE_STATE_PATH,
                proxy_url=Config.get_kaspi_pay_proxy_url() or None,
                headless=Config.PLAYWRIGHT_HEADLESS,
            )
            await browser_manager.launch()

            kaspi_auth = KaspiAuthManager(browser_manager, db_path=db_path)
            kaspi_auth.set_notify_callback(notification_service.send_to_admins)
            set_auth_manager(kaspi_auth)

            # Инициализация MarketingScraper
            marketing_scraper = MarketingScraper(
                browser_context=browser_manager.context,
                db_path=db_path,
            )
            scrape_logs_db = ScrapeLogsDB(db_path)
            ads_data_db_inst = AdsDataDB(db_path)
            set_marketing_scraper(marketing_scraper, scrape_logs_db, ads_data_db_inst)

            logger.info("Kaspi Pay scraper инициализирован")
        else:
            logger.info(
                "Kaspi Pay auth не настроен (ожидается KASPI_PAY_LOGIN/KASPI_PAY_PASSWORD или KASPI_PAY_PHONE) — scraper отключен"
            )

        # Инициализация аналитики
        ads_data_db_analytics = AdsDataDB(db_path)
        scrape_logs_db_analytics = ScrapeLogsDB(db_path)
        analytics_processor = AdsAnalyticsProcessor(
            ads_db=ads_data_db_analytics,
            products_db=products_db,
            product_sellers_db=product_sellers_db,
        )
        data_aggregator = DataAggregator(
            ads_db=ads_data_db_analytics,
            products_db=products_db,
        )

        # Инициализация TMA API-сервера
        tma_api_server = TMAApiServer(
            processor=analytics_processor,
            aggregator=data_aggregator,
            ads_db=ads_data_db_analytics,
            products_db=products_db,
            scrape_logs_db=scrape_logs_db_analytics,
            bot_token=Config.TELEGRAM_BOT_TOKEN,
            admin_user_ids=set(Config.ADMIN_USER_IDS),
            host=Config.TMA_API_HOST,
            port=Config.TMA_API_PORT,
            cors_origins=Config.TMA_CORS_ORIGINS,
            scrape_trigger=scheduled_scrape_marketing if Config.is_kaspi_pay_enabled() else None,
            tma_dist_path=Config.TMA_DIST_PATH,
        )

        # Dispatcher
        dp = Dispatcher()
        dp.include_router(scraper_router)
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

        # Ежедневный сбор данных Kaspi Marketing (только если scraper настроен)
        if Config.is_kaspi_pay_enabled():
            from apscheduler.triggers.cron import CronTrigger
            scheduler.add_job(
                scheduled_scrape_marketing,
                trigger=CronTrigger(
                    hour=Config.SCRAPE_SCHEDULE_HOUR,
                    minute=Config.SCRAPE_SCHEDULE_MINUTE,
                    timezone="Asia/Almaty",
                ),
                id="scrape_kaspi_marketing",
                name="Сбор данных Kaspi Marketing",
                max_instances=1,
                coalesce=True,
                replace_existing=True,
            )
            logger.info(
                "Запланирован скрапинг Kaspi Marketing: %02d:%02d (Almaty)",
                Config.SCRAPE_SCHEDULE_HOUR,
                Config.SCRAPE_SCHEDULE_MINUTE,
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

        # Запуск TMA API-сервера
        await tma_api_server.start()

        # Запуск polling (drop_pending_updates — не обрабатывать старые update-ы)
        try:
            await dp.start_polling(bot, drop_pending_updates=True)
        finally:
            await tma_api_server.stop()
            await whatsapp_webhook.stop()
            if browser_manager:
                await browser_manager.close()
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
