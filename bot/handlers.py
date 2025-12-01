"""
Telegram bot handlers
Обработка команд пользователя
"""
import logging
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.markdown import hcode

from config import Config
from database import ProductsDB, ProductSellersDB, ScanLogsDB, SellersDB
from parser import KaspiParser
from .utils import validate_kaspi_url, paginate_list

logger = logging.getLogger(__name__)

router = Router()


# Проверка админа
def is_admin(user_id: int) -> bool:
    """Проверить является ли пользователь администратором"""
    return user_id in Config.ADMIN_USER_IDS


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Команда /start"""
    # Логируем user_id для удобства настройки
    logger.info(
        f"Команда /start от пользователя: "
        f"ID={message.from_user.id}, "
        f"Username=@{message.from_user.username}, "
        f"Name={message.from_user.full_name}"
    )
    
    await message.answer(
        "🤖 <b>Kaspi Sellers Monitor</b>\n\n"
        "Я отслеживаю новых продавцов на товарах Kaspi.kz\n\n"
        "<b>Команды:</b>\n"
        "/add <code>&lt;url&gt;</code> — добавить товар\n"
        "/list — список товаров\n"
        "/remove <code>&lt;sku&gt;</code> — удалить товар\n"
        "/stats — статистика\n"
        "/scan — принудительная проверка (admin)\n\n"
        "Проверка каждые 6 часов автоматически 🕐",
        parse_mode="HTML"
    )


@router.message(Command("add"))
async def cmd_add(message: Message):
    """Команда /add <url>"""
    if not is_admin(message.from_user.id):
        await message.answer("Доступно только администраторам")
        return
    
    # Парсинг аргументов
    args = message.text.split(maxsplit=1)
    
    if len(args) < 2:
        await message.answer(
            "Укажите URL товара\n\n"
            "<b>Пример:</b>\n"
            "/add https://kaspi.kz/shop/p/название-107664472/",
            parse_mode="HTML"
        )
        return
    
    url = args[1].strip()
    
    # Валидация URL (базовая проверка на kaspi.kz)
    if 'kaspi.kz' not in url:
        await message.answer("Неверный формат URL Kaspi")
        return
    
    # Извлечь master_sku (async версия для поддержки коротких ссылок)
    master_sku = await KaspiParser.extract_master_sku_async(url)
    
    if not master_sku:
        await message.answer("Не удалось извлечь SKU из URL")
        return
    
    # Добавить в БД
    try:
        products_db = ProductsDB(Config.DB_PATH)
        
        # Проверить существование
        existing = await products_db.get_product(master_sku)
        if existing:
            await message.answer(
                f"Товар уже отслеживается\n\n"
                f"<b>SKU:</b> {hcode(master_sku)}\n"
                f"<b>Добавлен:</b> {existing['added_at']}",
                parse_mode="HTML"
            )
            return
        
        # Получить название товара сразу при добавлении
        product_title = None
        try:
            parser = KaspiParser(Config.PROXY_URL)
            success, offers = await parser.get_product_offers(master_sku)
            if success and offers and len(offers) > 0:
                # Пробуем разные поля для названия
                product_title = (
                    offers[0].get('productName') or 
                    offers[0].get('title') or 
                    offers[0].get('name')
                )
                logger.info(f"Получено название товара: {product_title}")
        except Exception as e:
            logger.warning(f"Не удалось получить название товара при добавлении: {e}")
        
        # Добавить новый товар с названием
        success = await products_db.add_product(master_sku, url, title=product_title)
        
        if success:
            title_text = product_title if product_title else "Без названия"
            await message.answer(
                f"Товар добавлен\n\n"
                f"<b>Название:</b> {title_text}\n"
                f"<b>SKU:</b> {hcode(master_sku)}\n"
                f"<b>URL:</b> {url[:50]}...\n\n"
                f"Будет проверяться каждые 6 часов",
                parse_mode="HTML"
            )
            logger.info(f"Товар {master_sku} добавлен пользователем {message.from_user.id}")
        else:
            await message.answer("Ошибка добавления товара")
            
    except Exception as e:
        logger.error(f"Ошибка в cmd_add: {e}", exc_info=True)
        await message.answer("Произошла ошибка при добавлении товара")


@router.message(Command("list"))
async def cmd_list(message: Message):
    """Команда /list - показать все товары с кнопками (с пагинацией)"""
    try:
        products_db = ProductsDB(Config.DB_PATH)
        products = await products_db.get_all_products()
        
        if not products:
            await message.answer("Нет отслеживаемых товаров")
            return
        
        # Показываем первую страницу
        await show_products_list(message, products, page=1)
        
    except Exception as e:
        logger.error(f"Ошибка в cmd_list: {e}", exc_info=True)
        await message.answer("Ошибка получения списка товаров")


async def show_products_list(message: Message, products: list, page: int = 1):
    """Показать список товаров с пагинацией"""
    per_page = 10
    total = len(products)
    total_pages = (total + per_page - 1) // per_page
    
    # Вычисляем индексы для пагинации
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    products_page = products[start_idx:end_idx]
    
    text = f"<b>Отслеживаемые товары ({total})</b>\n\n"
    text += f"<i>Нажмите на товар, чтобы увидеть продавцов</i>\n"
    text += f"Страница {page}/{total_pages}\n\n"
    
    # Создаем кнопки для товаров на текущей странице
    keyboard = []
    for product in products_page:
        title = product.get('title') or 'Без названия'
        sku = product['master_sku']
        
        # Сокращаем название если длинное
        button_text = title[:35] + '...' if len(title) > 35 else title
        
        keyboard.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"product_{sku}_1"
            )
        ])
    
    # Кнопки навигации
    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="◀️ Назад",
                callback_data=f"list_page_{page-1}"
            )
        )
    
    if page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Вперед ▶️",
                callback_data=f"list_page_{page+1}"
            )
        )
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await message.answer(
        text,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("product_"))
async def show_product_sellers(callback: CallbackQuery):
    """Показать продавцов для товара"""
    try:
        # Извлекаем SKU и страницу из callback_data
        parts = callback.data.split("_")
        sku = parts[1]
        page = int(parts[2]) if len(parts) > 2 else 1
        
        per_page = 20
        
        products_db = ProductsDB(Config.DB_PATH)
        product_sellers_db = ProductSellersDB(Config.DB_PATH)
        sellers_db = SellersDB(Config.DB_PATH)
        
        # Получаем информацию о товаре
        product = await products_db.get_product(sku)
        if not product:
            await callback.answer("Товар не найден", show_alert=True)
            return
        
        # Получаем продавцов
        sellers_list = await product_sellers_db.get_sellers_for_product(sku, active_only=True)
        
        if not sellers_list:
            await callback.answer("⚠️ Продавцов пока нет", show_alert=True)
            return
        
        total = len(sellers_list)
        total_pages = (total + per_page - 1) // per_page
        
        # Вычисляем индексы для пагинации
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        sellers_page = sellers_list[start_idx:end_idx]
        
        # Формируем сообщение
        title = product.get('title') or 'Без названия'
        text = f"<b>{title}</b>\n\n"
        text += f"<b>SKU:</b> {hcode(sku)}\n"
        text += f"<b>Всего продавцов:</b> {total}\n"
        text += f"<b>Страница:</b> {page}/{total_pages}\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        # Показываем продавцов на текущей странице
        for idx, seller_link in enumerate(sellers_page, start_idx + 1):
            seller_id = seller_link['seller_id']
            price = seller_link['price']
            
            # Получаем полную информацию о продавце
            seller = await sellers_db.get_seller(seller_id)
            if not seller:
                continue
            
            merchant_name = seller['merchant_name']
            phone = seller.get('phone') or 'недоступен'
            
            text += f"{idx}. <b>{merchant_name}</b>\n"
            text += f"   Цена: {price:,.0f} ₸\n"
            text += f"   Телефон: <code>{phone}</code>\n\n"
        
        # Кнопки навигации
        keyboard = []
        nav_buttons = []
        
        # Кнопка "Назад" (к предыдущей странице)
        if page > 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data=f"product_{sku}_{page-1}"
                )
            )
        
        # Кнопка "Вперед" (к следующей странице)
        if page < total_pages:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Вперед ▶️",
                    callback_data=f"product_{sku}_{page+1}"
                )
            )
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        # Кнопка "К списку товаров"
        keyboard.append([
            InlineKeyboardButton(text="К списку товаров", callback_data="back_to_list")
        ])
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка в show_product_sellers: {e}", exc_info=True)
        await callback.answer("Ошибка загрузки продавцов", show_alert=True)


@router.callback_query(F.data.startswith("list_page_"))
async def list_page_navigation(callback: CallbackQuery):
    """Навигация по страницам списка товаров"""
    try:
        page = int(callback.data.split("_")[-1])
        
        products_db = ProductsDB(Config.DB_PATH)
        products = await products_db.get_all_products()
        
        if not products:
            await callback.message.edit_text("Нет отслеживаемых товаров")
            return
        
        per_page = 10
        total = len(products)
        total_pages = (total + per_page - 1) // per_page
        
        # Вычисляем индексы для пагинации
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        products_page = products[start_idx:end_idx]
        
        text = f"<b>Отслеживаемые товары ({total})</b>\n\n"
        text += f"<i>Нажмите на товар, чтобы увидеть продавцов</i>\n"
        text += f"Страница {page}/{total_pages}\n\n"
        
        # Создаем кнопки для товаров на текущей странице
        keyboard = []
        for product in products_page:
            title = product.get('title') or 'Без названия'
            sku = product['master_sku']
            
            button_text = title[:35] + '...' if len(title) > 35 else title
            
            keyboard.append([
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"product_{sku}_1"
                )
            ])
        
        # Кнопки навигации
        nav_buttons = []
        if page > 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="◀️ Назад",
                    callback_data=f"list_page_{page-1}"
                )
            )
        
        if page < total_pages:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Вперед ▶️",
                    callback_data=f"list_page_{page+1}"
                )
            )
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка в list_page_navigation: {e}", exc_info=True)
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data == "back_to_list")
async def back_to_list(callback: CallbackQuery):
    """Вернуться к списку товаров (первая страница)"""
    try:
        products_db = ProductsDB(Config.DB_PATH)
        products = await products_db.get_all_products()
        
        if not products:
            await callback.message.edit_text("Нет отслеживаемых товаров")
            return
        
        per_page = 10
        total = len(products)
        total_pages = (total + per_page - 1) // per_page
        products_page = products[:per_page]
        
        text = f"<b>Отслеживаемые товары ({total})</b>\n\n"
        text += f"<i>Нажмите на товар, чтобы увидеть продавцов</i>\n"
        text += f"Страница 1/{total_pages}\n\n"
        
        # Создаем кнопки для товаров на первой странице
        keyboard = []
        for product in products_page:
            title = product.get('title') or 'Без названия'
            sku = product['master_sku']
            
            button_text = title[:35] + '...' if len(title) > 35 else title
            
            keyboard.append([
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"product_{sku}_1"
                )
            ])
        
        # Кнопка навигации (только "Вперед" если есть следующая страница)
        if total_pages > 1:
            keyboard.append([
                InlineKeyboardButton(
                    text="Вперед ▶️",
                    callback_data="list_page_2"
                )
            ])
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка в back_to_list: {e}", exc_info=True)
        await callback.answer("Ошибка", show_alert=True)


@router.message(Command("remove"))
async def cmd_remove(message: Message):
    """Команда /remove <sku>"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔️ Доступно только администраторам")
        return
    
    args = message.text.split(maxsplit=1)
    
    if len(args) < 2:
        await message.answer(
            "Укажите SKU товара\n\n"
            "<b>Пример:</b>\n"
            "/remove 107664472",
            parse_mode="HTML"
        )
        return
    
    master_sku = args[1].strip()
    
    try:
        products_db = ProductsDB(Config.DB_PATH)
        success = await products_db.delete_product(master_sku)
        
        if success:
            await message.answer(
                f"Товар удален\n\n"
                f"<b>SKU:</b> {hcode(master_sku)}",
                parse_mode="HTML"
            )
            logger.info(f"Товар {master_sku} удален пользователем {message.from_user.id}")
        else:
            await message.answer(f"Товар с SKU {hcode(master_sku)} не найден", parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"Ошибка в cmd_remove: {e}", exc_info=True)
        await message.answer("Ошибка удаления товара")


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Команда /stats - показать статистику"""
    try:
        products_db = ProductsDB(Config.DB_PATH)
        product_sellers_db = ProductSellersDB(Config.DB_PATH)
        scan_logs_db = ScanLogsDB(Config.DB_PATH)
        
        # Получить данные
        total_products = await products_db.get_products_count()
        total_sellers = await product_sellers_db.get_total_sellers_count()
        total_links = await product_sellers_db.get_active_links_count()
        last_scan = await scan_logs_db.get_last_scan()
        
        text = "📊 <b>Статистика</b>\n\n"
        text += f"📦 Товаров: {total_products}\n"
        text += f"🏪 Продавцов: {total_sellers}\n"
        text += f"🔗 Активных связей: {total_links}\n\n"
        
        if last_scan:
            text += f"🕐 <b>Последнее сканирование:</b>\n"
            text += f"   Начато: {last_scan.get('started_at', '—')}\n"
            text += f"   Завершено: {last_scan.get('finished_at', '—')}\n"
            text += f"   Проверено товаров: {last_scan.get('products_checked', 0)}\n"
            text += f"   Новых продавцов: {last_scan.get('new_sellers', 0)}\n"
            
            if last_scan.get('errors'):
                text += f"   ⚠️ Ошибки: есть\n"
        else:
            text += "🕐 Сканирования еще не было\n"
        
        await message.answer(text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Ошибка в cmd_stats: {e}", exc_info=True)
        await message.answer("Ошибка получения статистики")


@router.message(Command("scan"))
async def cmd_scan(message: Message):
    """Команда /scan - принудительное сканирование (только админ)"""
    if not is_admin(message.from_user.id):
        await message.answer("⛔️ Доступно только администраторам")
        return
    
    # Эта команда будет обрабатываться в main.py
    # Здесь просто placeholder
    await message.answer(
        "🔄 Запускаю сканирование...\n\n"
        "Это может занять несколько минут"
    )
