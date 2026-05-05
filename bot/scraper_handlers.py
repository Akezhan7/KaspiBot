"""
Telegram-команды для управления Kaspi Pay скрапером.

/login_kaspi — авторизация в Kaspi Pay (логин/пароль или phone+SMS)
/kaspi_status — проверка статуса сессии
/scrape       — ручной запуск сбора маркетинговых данных
/analytics    — открыть дашборд аналитики (TMA)
"""
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from config import Config

logger = logging.getLogger(__name__)

scraper_router = Router()

# Ссылки на менеджеры — устанавливаются из main.py
_auth_manager = None
_marketing_scraper = None
_scrape_logs_db = None
_ads_data_db = None


def set_auth_manager(auth_manager) -> None:
    """Установить ссылку на KaspiAuthManager для команд."""
    global _auth_manager
    _auth_manager = auth_manager


def set_marketing_scraper(scraper, scrape_logs_db, ads_data_db) -> None:
    """Установить ссылку на MarketingScraper и DAO для команды /scrape."""
    global _marketing_scraper, _scrape_logs_db, _ads_data_db
    _marketing_scraper = scraper
    _scrape_logs_db = scrape_logs_db
    _ads_data_db = ads_data_db


def _is_admin(user_id: int) -> bool:
    return user_id in Config.ADMIN_USER_IDS


@scraper_router.message(Command("login_kaspi"))
async def cmd_login_kaspi(message: Message) -> None:
    """Обработчик /login_kaspi [код].

    Без аргументов — инициирует логин.
    С аргументом — передаёт SMS-код в auth manager (если 2FA запрошена).
    """
    if not _is_admin(message.from_user.id):
        return

    if not _auth_manager:
        await message.answer("Scraper не инициализирован. Проверьте конфигурацию.")
        return

    args = message.text.strip().split(maxsplit=1)
    if len(args) > 1:
        # Передача SMS-кода
        code = args[1].strip()
        if not code.isdigit():
            await message.answer("SMS-код должен содержать только цифры.")
            return

        _auth_manager.submit_sms_code(code)
        await message.answer(f"SMS-код <code>{code}</code> передан. Ожидание результата...")
        logger.info("login_kaspi: SMS-код передан пользователем %d", message.from_user.id)
    else:
        # Запуск процесса логина
        await message.answer(
            "Запуск авторизации в Kaspi Pay...\n"
            "Если система запросит SMS-код, отправьте: <code>/login_kaspi 123456</code>"
        )
        logger.info("login_kaspi: запуск авторизации пользователем %d", message.from_user.id)

        success = await _auth_manager.login()
        if success:
            await message.answer("Kaspi Pay: авторизация успешна!")
        else:
            await message.answer("Kaspi Pay: ошибка авторизации. Проверьте логи.")


@scraper_router.message(Command("kaspi_status"))
async def cmd_kaspi_status(message: Message) -> None:
    """Проверить статус текущей сессии Kaspi Pay."""
    if not _is_admin(message.from_user.id):
        return

    if not _auth_manager:
        await message.answer("Scraper не инициализирован.")
        return

    await message.answer("Проверяю сессию Kaspi Pay...")

    valid = await _auth_manager.is_session_valid()
    state_path = Config.KASPI_STORAGE_STATE_PATH

    status_text = (
        f"<b>Kaspi Pay Session</b>\n\n"
        f"Статус: {'Активна' if valid else 'Невалидна / Истекла'}\n"
        f"State файл: {'существует' if state_path.exists() else 'отсутствует'}\n"
        f"Headless: {Config.PLAYWRIGHT_HEADLESS}\n"
        f"Scrape schedule: {Config.SCRAPE_SCHEDULE_HOUR:02d}:{Config.SCRAPE_SCHEDULE_MINUTE:02d}"
    )
    await message.answer(status_text)


@scraper_router.message(Command("scrape"))
async def cmd_scrape(message: Message) -> None:
    """Ручной запуск сбора маркетинговых данных из Kaspi Pay.

    Только для администраторов. Запускает полный цикл скрапинга и
    выводит статистику: сколько SKU обработано, ошибки.
    """
    if not _is_admin(message.from_user.id):
        return

    if not _marketing_scraper or not _scrape_logs_db or not _ads_data_db:
        await message.answer(
            "Scraper не инициализирован.\n"
            "Убедитесь что задан один из способов auth в .env:\n"
            "1) KASPI_PAY_LOGIN + KASPI_PAY_PASSWORD\n"
            "или\n"
            "2) KASPI_PAY_PHONE\n"
            "и бот перезапущен."
        )
        return

    if not _auth_manager:
        await message.answer("Auth manager не инициализирован.")
        return

    await message.answer("Запускаю сбор данных Kaspi Marketing...\nЭто может занять несколько минут.")

    log_id = await _scrape_logs_db.create_log()
    logger.info("cmd_scrape: запущен пользователем %d, log_id=%d", message.from_user.id, log_id)

    # Проверяем/восстанавливаем сессию
    if not await _auth_manager.is_session_valid():
        await message.answer("Сессия Kaspi Pay истекла. Пробую автоматическую авторизацию...")
        relogin_ok = await _auth_manager.login()
        if not relogin_ok:
            await message.answer(
                "Авто-авторизация не удалась.\n"
                "Выполните /login_kaspi для ручной авторизации."
            )
            await _scrape_logs_db.update_log(
                log_id,
                status="failed",
                errors="Сессия истекла, авто-авторизация не удалась",
            )
            return

    try:
        result = await _marketing_scraper.scrape_all()
        scraped_at = result.scraped_at.strftime("%Y-%m-%d %H:%M:%S")

        # Сохраняем кампании
        campaign_dicts = [c.to_dao_dict(scraped_at) for c in result.campaigns]
        saved_campaigns = await _ads_data_db.save_campaigns_batch(campaign_dicts)

        # Сохраняем бонусы
        bonus_dicts = [b.to_dao_dict(scraped_at) for b in result.bonuses]
        saved_bonuses = await _ads_data_db.save_campaigns_batch(bonus_dicts)

        total_saved = saved_campaigns + saved_bonuses
        errors_text = "\n".join(result.errors) if result.errors else None

        await _scrape_logs_db.update_log(
            log_id,
            status="completed" if not result.errors else "completed_with_errors",
            products_scraped=total_saved,
            errors=errors_text,
        )

        # Формируем ответ
        lines = [
            "<b>Сбор данных завершён</b>",
            "",
            f"Кампаний: {len(result.campaigns)}",
            f"Бонусов: {len(result.bonuses)}",
            f"Сохранено записей: {total_saved}",
        ]
        if result.errors:
            lines.append(f"\n⚠️ Ошибки ({len(result.errors)}):")
            for err in result.errors[:5]:
                lines.append(f"• {err[:120]}")
            if len(result.errors) > 5:
                lines.append(f"• ...и ещё {len(result.errors) - 5}")

        await message.answer("\n".join(lines))
        logger.info(
            "cmd_scrape: завершён. Кампаний=%d, бонусов=%d, ошибок=%d",
            len(result.campaigns),
            len(result.bonuses),
            len(result.errors),
        )

    except Exception as e:
        logger.error("cmd_scrape: критическая ошибка: %s", e, exc_info=True)
        await _scrape_logs_db.update_log(log_id, status="failed", errors=str(e))
        await message.answer(f"Ошибка скрапинга: {e}")


@scraper_router.message(Command("analytics"))
async def cmd_analytics(message: Message) -> None:
    """Открыть дашборд аналитики Kaspi Marketing (Telegram Mini App).

    Если TMA_URL настроен — отправляет inline-кнопку с WebApp.
    Если нет — сообщает об ошибке конфигурации.
    """
    if not _is_admin(message.from_user.id):
        return

    tma_url = Config.TMA_URL.strip()

    if not tma_url:
        await message.answer(
            "TMA_URL не настроен.\n"
            "Добавьте в .env:\n"
            "<code>TMA_URL=https://your-domain/tma</code>"
        )
        logger.warning("cmd_analytics: TMA_URL не задан")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть аналитику",
                    web_app=WebAppInfo(url=tma_url),
                )
            ]
        ]
    )
    await message.answer(
        "<b>Kaspi Ads Analytics</b>\n\nНажмите кнопку для открытия дашборда.",
        reply_markup=keyboard,
    )
    logger.info("cmd_analytics: отправлена inline-кнопка TMA пользователю %d", message.from_user.id)

