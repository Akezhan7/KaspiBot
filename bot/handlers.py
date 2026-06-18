"""
Telegram bot handlers
Обработка команд пользователя
"""
import html
import logging
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, BufferedInputFile
from aiogram.utils.markdown import hcode

from config import Config, now_kz
from database import (
    ProductsDB,
    ProductSellersDB,
    ScanLogsDB,
    SellersDB,
    RecentSellersDB,
    SellerWorkflowDB,
)
from parser import KaspiParser
from parser.title_utils import clean_product_title
from .utils import validate_kaspi_url, paginate_list

logger = logging.getLogger(__name__)

router = Router()


class SellerSearchFSM(StatesGroup):
    waiting_query = State()


MENU_BUTTONS = [
    "Мои товары",
    "Все продавцы",
    "Новые продавцы",
    "Поиск",
    "Поиск продавца",
    "Статистика",
    "Добавить товар",
    "Сканировать",
]

MANUAL_WHATSAPP_MARKER = "🔴"


# Постоянная клавиатура для пользователей
def get_main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Получить главную клавиатуру с кнопками"""
    buttons = [
        [KeyboardButton(text="Мои товары"), KeyboardButton(text="Все продавцы")],
        [KeyboardButton(text="Новые продавцы"), KeyboardButton(text="Поиск")],
        [KeyboardButton(text="Поиск продавца")],
        [KeyboardButton(text="Статистика")]
    ]
    
    if is_admin:
        buttons.append([KeyboardButton(text="Добавить товар"), KeyboardButton(text="Сканировать")])
    
    return ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие..."
    )


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
    
    keyboard = get_main_keyboard(is_admin(message.from_user.id))
    
    await message.answer(
        "<b>Kaspi Sellers Monitor</b>\n\n"
        "Я отслеживаю новых продавцов на товарах Kaspi.kz\n\n"
        "<b>Используйте кнопки ниже или команды:</b>\n"
        "/add <code>&lt;url&gt;</code> — добавить товар\n"
        "/list — список товаров\n"
        "/search <code>&lt;запрос&gt;</code> — поиск товаров\n"
        "/seller_search <code>&lt;запрос&gt;</code> — поиск продавцов\n"
        "/sellers — все продавцы\n"
        "/recent — последние новые продавцы\n"
        "/remove <code>&lt;sku&gt;</code> — удалить товар\n"
        "/stats — статистика\n"
        "/scan — принудительная проверка (admin)\n\n"
        "Проверка каждые 12 часов автоматически",
        parse_mode="HTML",
        reply_markup=keyboard
    )


@router.message(Command("export_urls"))
async def cmd_export_urls(message: Message):
    """Команда /export_urls — выгрузить все URL товаров в txt файл"""
    if not is_admin(message.from_user.id):
        await message.answer("Доступно только администраторам")
        return

    try:
        products_db = ProductsDB(Config.DB_PATH)
        products = await products_db.get_all_products()

        if not products:
            await message.answer("Нет отслеживаемых товаров")
            return

        urls = []
        for p in products:
            url = p.get('url') or f"https://kaspi.kz/shop/p/{p['master_sku']}/"
            urls.append(url)

        content = "\n".join(urls)
        file = BufferedInputFile(
            content.encode("utf-8"),
            filename=f"kaspi_urls_{len(urls)}.txt"
        )

        await message.answer_document(
            file,
            caption=f"Все URL товаров: {len(urls)} шт."
        )
    except Exception as e:
        logger.error(f"Ошибка в cmd_export_urls: {e}", exc_info=True)
        await message.answer("Ошибка при выгрузке URL")


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
    
    # Валидация URL (базовая проверка на kaspi.kz или l.kaspi.kz)
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
            
            # Сначала пытаемся получить через product API
            product_info = await parser.get_product_info(master_sku)
            if product_info:
                product_title = clean_product_title(
                    product_info.get('title') or 
                    product_info.get('name') or 
                    product_info.get('productName')
                )
                logger.info(f"Получено название из product API: {product_title}")
            
            # Если не получилось через product API - пробуем через offers
            if not product_title:
                success, offers = await parser.get_product_offers(master_sku)
                if success and offers and len(offers) > 0:
                    # Пробуем разные поля для названия
                    product_title = clean_product_title(
                        offers[0].get('productName') or 
                        offers[0].get('title') or 
                        offers[0].get('name')
                    )
                    logger.info(f"Получено название из offers: {product_title}")
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
                f"Будет проверяться каждые 12 часов",
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
        products = await products_db.get_all_products_with_sellers_count()
        
        if not products:
            await message.answer("Нет отслеживаемых товаров")
            return
        
        # Показываем первую страницу
        await show_products_list(message, products, page=1)
        
    except Exception as e:
        logger.error(f"Ошибка в cmd_list: {e}", exc_info=True)
        await message.answer("Ошибка получения списка товаров")


@router.message(Command("recent"))
async def cmd_recent(message: Message):
    """Команда /recent - показать последних новых продавцов"""
    try:
        recent_db = RecentSellersDB(Config.DB_PATH)
        
        # Показываем первую страницу (20 записей)
        await show_recent_sellers(message, page=1)
        
    except Exception as e:
        logger.error(f"Ошибка в cmd_recent: {e}", exc_info=True)
        await message.answer("Ошибка получения истории")


async def show_recent_sellers(message: Message, page: int = 1):
    """Показать последних новых продавцов с пагинацией"""
    per_page = 20
    offset = (page - 1) * per_page
    
    recent_db = RecentSellersDB(Config.DB_PATH)
    recent_sellers = await recent_db.get_recent_sellers(limit=per_page, offset=offset)
    total = await recent_db.get_recent_count()
    
    if not recent_sellers:
        await message.answer("История пуста\n\nНовые продавцы появятся после сканирования")
        return
    
    total_pages = (total + per_page - 1) // per_page
    
    text = f"<b>Последние новые продавцы</b>\n\n"
    text += f"Всего: {total} | Страница {page}/{total_pages}\n\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    for idx, seller in enumerate(recent_sellers, offset + 1):
        product_title = seller.get('product_title') or 'Без названия'
        merchant_name = seller['merchant_name']
        price = seller['price']
        phone = seller.get('phone') or 'недоступен'
        detected_at = seller['detected_at'][:16]  # YYYY-MM-DD HH:MM
        
        # Сокращаем название
        if len(product_title) > 35:
            product_title = product_title[:35] + '...'
        
        text += f"{idx}. <b>{merchant_name}</b>\n"
        text += f"   {product_title}\n"
        text += f"   {price:,.0f} ₸ | <code>{phone}</code>\n"
        text += f"   <i>{detected_at}</i>\n\n"
    
    # Кнопки навигации
    keyboard = []
    nav_buttons = []
    
    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Назад",
                callback_data=f"recent_page_{page-1}"
            )
        )
    
    if page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Вперед",
                callback_data=f"recent_page_{page+1}"
            )
        )
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None
    
    await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("recent_page_"))
async def recent_page_navigation(callback: CallbackQuery):
    """Навигация по страницам истории"""
    try:
        page = int(callback.data.split("_")[-1])
        
        per_page = 20
        offset = (page - 1) * per_page
        
        recent_db = RecentSellersDB(Config.DB_PATH)
        recent_sellers = await recent_db.get_recent_sellers(limit=per_page, offset=offset)
        total = await recent_db.get_recent_count()
        
        if not recent_sellers:
            await callback.message.edit_text("История пуста")
            return
        
        total_pages = (total + per_page - 1) // per_page
        
        text = f"<b>Последние новые продавцы</b>\n\n"
        text += f"Всего: {total} | Страница {page}/{total_pages}\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for idx, seller in enumerate(recent_sellers, offset + 1):
            product_title = seller.get('product_title') or 'Без названия'
            merchant_name = seller['merchant_name']
            price = seller['price']
            phone = seller.get('phone') or 'недоступен'
            detected_at = seller['detected_at'][:16]
            
            if len(product_title) > 35:
                product_title = product_title[:35] + '...'
            
            text += f"{idx}. <b>{merchant_name}</b>\n"
            text += f"   {product_title}\n"
            text += f"   {price:,.0f} ₸ | <code>{phone}</code>\n"
            text += f"   <i>{detected_at}</i>\n\n"
        
        # Кнопки навигации
        keyboard = []
        nav_buttons = []
        
        if page > 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=f"recent_page_{page-1}"
                )
            )
        
        if page < total_pages:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Вперед",
                    callback_data=f"recent_page_{page+1}"
                )
            )
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None
        
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка в recent_page_navigation: {e}", exc_info=True)
        await callback.answer("Ошибка", show_alert=True)


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
        sellers_count = product.get('sellers_count', 0)
        
        # Сокращаем название если длинное
        display_title = title[:30] + '...' if len(title) > 30 else title
        button_text = f"{display_title} ({sellers_count})"
        
        keyboard.append([
            InlineKeyboardButton(
                text=button_text,
                callback_data=f"product_{sku}_1_{page}"
            )
        ])
    
    # Кнопки навигации
    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Назад",
                callback_data=f"list_page_{page-1}"
            )
        )
    
    if page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Вперед",
                callback_data=f"list_page_{page+1}"
            )
        )
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    # Кнопка поиска
    keyboard.append([
        InlineKeyboardButton(
            text="Поиск товара",
            callback_data="search_products"
        )
    ])
    
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
        # Извлекаем SKU, страницу продавцов и страницу списка из callback_data
        parts = callback.data.split("_")
        sku = parts[1]
        sellers_page = int(parts[2]) if len(parts) > 2 else 1
        list_page = int(parts[3]) if len(parts) > 3 else 1
        
        per_page = 20
        
        products_db = ProductsDB(Config.DB_PATH)
        product_sellers_db = ProductSellersDB(Config.DB_PATH)
        
        # Получаем информацию о товаре
        product = await products_db.get_product(sku)
        if not product:
            await callback.answer("Товар не найден", show_alert=True)
            return
        
        # Получаем продавцов с подсчетом других товаров (один запрос вместо N+1)
        sellers_list = await product_sellers_db.get_sellers_for_product_with_other_count(sku, active_only=True)
        
        total = len(sellers_list)
        sellers_with_products = []  # Инициализируем здесь для использования позже
        
        # Формируем сообщение
        title = product.get('title') or 'Без названия'
        text = f"<b>{title}</b>\n\n"
        text += f"<b>SKU:</b> {hcode(sku)}\n"
        
        if not sellers_list:
            # Если продавцов нет - показываем это
            text += f"<b>Всего продавцов:</b> 0\n\n"
            text += "━━━━━━━━━━━━━━━━━━━━\n\n"
            text += "<i>Продавцов пока нет.\n"
            text += "Они появятся после следующего сканирования.</i>\n"
        else:
            # Есть продавцы - показываем их
            total_pages = (total + per_page - 1) // per_page
            
            # Вычисляем индексы для пагинации
            start_idx = (sellers_page - 1) * per_page
            end_idx = start_idx + per_page
            sellers_on_page = sellers_list[start_idx:end_idx]
            
            text += f"<b>Всего продавцов:</b> {total}\n"
            text += f"<b>Страница:</b> {sellers_page}/{total_pages}\n\n"
            text += "━━━━━━━━━━━━━━━━━━━━\n\n"
            
            # Показываем продавцов на текущей странице
            for idx, seller_link in enumerate(sellers_on_page, start_idx + 1):
                seller_id = seller_link['seller_id']
                price = seller_link['price']
                merchant_name = seller_link['merchant_name']
                phone = seller_link.get('phone') or 'недоступен'
                other_products_count = seller_link.get('other_products_count', 0)
                
                text += f"{idx}. <b>{merchant_name}</b>\n"
                text += f"   Цена: {price:,.0f} ₸\n"
                text += f"   Телефон: <code>{phone}</code>\n"
                
                # Показываем информацию о других товарах
                if other_products_count > 0:
                    text += f"   Также на {other_products_count} других товарах [нажмите №{idx}]\n"
                    sellers_with_products.append((idx, seller_id, merchant_name))
                
                text += "\n"
        
        # Кнопки навигации
        keyboard = []
        
        # Кнопки с номерами продавцов (если есть другие товары)
        if sellers_with_products:
            seller_buttons_row = []
            for idx, seller_id, _ in sellers_with_products:
                seller_buttons_row.append(
                    InlineKeyboardButton(
                        text=f"№{idx}",
                        callback_data=f"seller_products_{seller_id}_{sku}"
                    )
                )
                # По 5 кнопок в ряд
                if len(seller_buttons_row) == 5:
                    keyboard.append(seller_buttons_row)
                    seller_buttons_row = []
            # Добавить оставшиеся кнопки
            if seller_buttons_row:
                keyboard.append(seller_buttons_row)
        
        # Кнопки навигации только если есть продавцы
        if total > 0:
            total_pages = (total + per_page - 1) // per_page
            nav_buttons = []
            
            # Кнопка "Назад" (к предыдущей странице)
            if sellers_page > 1:
                nav_buttons.append(
                    InlineKeyboardButton(
                        text="Назад",
                        callback_data=f"product_{sku}_{sellers_page-1}_{list_page}"
                    )
                )
            
            # Кнопка "Вперед" (к следующей странице)
            if sellers_page < total_pages:
                nav_buttons.append(
                    InlineKeyboardButton(
                        text="Вперед",
                        callback_data=f"product_{sku}_{sellers_page+1}_{list_page}"
                    )
                )
            
            if nav_buttons:
                keyboard.append(nav_buttons)
        
        # Кнопка "К списку товаров" с сохранением страницы
        keyboard.append([
            InlineKeyboardButton(text="К списку товаров", callback_data=f"back_to_list_{list_page}")
        ])
        
        # Кнопка "Удалить товар" (только для админов)
        if is_admin(callback.from_user.id):
            keyboard.append([
                InlineKeyboardButton(text="Удалить товар", callback_data=f"confirm_delete_{sku}_{list_page}")
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


@router.callback_query(F.data.startswith("seller_products_"))
async def show_seller_products(callback: CallbackQuery):
    """Показать список товаров, на которых есть этот продавец"""
    try:
        # Извлекаем seller_id и current_sku из callback_data
        parts = callback.data.split("_")
        seller_id = parts[2]
        current_sku = parts[3]
        
        sellers_db = SellersDB(Config.DB_PATH)
        product_sellers_db = ProductSellersDB(Config.DB_PATH)
        products_db = ProductsDB(Config.DB_PATH)
        
        # Получаем информацию о продавце
        seller = await sellers_db.get_seller(seller_id)
        if not seller:
            await callback.answer("Продавец не найден", show_alert=True)
            return
        
        merchant_name = seller['merchant_name']
        phone = seller.get('phone') or 'недоступен'
        
        # Получаем список других товаров (исключая текущий)
        other_products = await product_sellers_db.get_other_products_for_seller(
            seller_id, current_sku
        )
        
        # Формируем сообщение
        text = f"<b>{merchant_name}</b>\n"
        text += f"Телефон: <code>{phone}</code>\n\n"
        text += f"<b>Также продает на товарах:</b>\n\n"
        
        if not other_products:
            text += "<i>Больше нет других товаров</i>\n"
        else:
            for idx, prod_link in enumerate(other_products, 1):
                product_id = prod_link['product_id']
                price = prod_link['price']
                
                # Получаем название товара
                product = await products_db.get_product(product_id)
                if product:
                    title = product.get('title') or 'Без названия'
                    # Сокращаем название
                    if len(title) > 40:
                        title = title[:40] + '...'
                    
                    text += f"{idx}. {title}\n"
                    text += f"   Цена: {price:,.0f} ₸\n\n"
        
        # Кнопка "Назад"
        keyboard = [[
            InlineKeyboardButton(
                text="← Назад к товару",
                callback_data=f"product_{current_sku}_1"
            )
        ]]
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка в show_seller_products: {e}", exc_info=True)
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("list_page_"))
async def list_page_navigation(callback: CallbackQuery):
    """Навигация по страницам списка товаров"""
    try:
        page = int(callback.data.split("_")[-1])
        
        products_db = ProductsDB(Config.DB_PATH)
        products = await products_db.get_all_products_with_sellers_count()
        
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
            sellers_count = product.get('sellers_count', 0)
            
            # Сокращаем название если длинное
            display_title = title[:30] + '...' if len(title) > 30 else title
            button_text = f"{display_title} ({sellers_count})"
            
            keyboard.append([
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"product_{sku}_1_{page}"
                )
            ])
        
        # Кнопки навигации
        nav_buttons = []
        if page > 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=f"list_page_{page-1}"
                )
            )
        
        if page < total_pages:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Вперед",
                    callback_data=f"list_page_{page+1}"
                )
            )
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        # Кнопка поиска
        keyboard.append([
            InlineKeyboardButton(
                text="Поиск товара",
                callback_data="search_products"
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
        logger.error(f"Ошибка в list_page_navigation: {e}", exc_info=True)
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("back_to_list"))
async def back_to_list(callback: CallbackQuery):
    """Вернуться к списку товаров (на сохраненную страницу)"""
    try:
        # Извлекаем номер страницы
        parts = callback.data.split("_")
        page = int(parts[-1]) if len(parts) > 3 else 1
        
        products_db = ProductsDB(Config.DB_PATH)
        products = await products_db.get_all_products_with_sellers_count()
        
        if not products:
            await callback.message.edit_text("Нет отслеживаемых товаров")
            return
        
        per_page = 10
        total = len(products)
        total_pages = (total + per_page - 1) // per_page
        
        # Проверяем, что страница в допустимом диапазоне
        if page > total_pages:
            page = total_pages
        if page < 1:
            page = 1
        
        # Вычисляем индексы для пагинации
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        products_page = products[start_idx:end_idx]
        
        text = f"<b>Отслеживаемые товары ({total})</b>\n\n"
        text += f"<i>Нажмите на товар, чтобы увидеть продавцов</i>\n"
        text += f"Страница {page}/{total_pages}\n\n"
        
        # Создаем кнопки для товаров на первой странице
        keyboard = []
        for product in products_page:
            title = product.get('title') or 'Без названия'
            sku = product['master_sku']
            sellers_count = product.get('sellers_count', 0)
            
            # Сокращаем название если длинное
            display_title = title[:30] + '...' if len(title) > 30 else title
            button_text = f"{display_title} ({sellers_count})"
            
            keyboard.append([
                InlineKeyboardButton(
                    text=button_text,
                    callback_data=f"product_{sku}_1_{page}"
                )
            ])
        
        # Кнопки навигации
        nav_buttons = []
        if page > 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=f"list_page_{page-1}"
                )
            )
        
        if page < total_pages:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Вперед",
                    callback_data=f"list_page_{page+1}"
                )
            )
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        # Кнопка поиска
        keyboard.append([
            InlineKeyboardButton(
                text="Поиск товара",
                callback_data="search_products"
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
        await message.answer("Доступно только администраторам")
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
        
        text = "<b>Статистика</b>\n\n"
        text += f"Товаров: {total_products}\n"
        text += f"Продавцов: {total_sellers}\n"
        text += f"Активных связей: {total_links}\n\n"
        
        if last_scan:
            text += f"<b>Последнее сканирование:</b>\n"
            text += f"   Начато: {last_scan.get('started_at', '—')}\n"
            text += f"   Завершено: {last_scan.get('finished_at', '—')}\n"
            text += f"   Проверено товаров: {last_scan.get('products_checked', 0)}\n"
            text += f"   Новых продавцов: {last_scan.get('new_sellers', 0)}\n"
            
            if last_scan.get('errors'):
                text += f"   Ошибки: есть\n"
        else:
            text += "Сканирования еще не было\n"
        
        await message.answer(text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Ошибка в cmd_stats: {e}", exc_info=True)
        await message.answer("Ошибка получения статистики")


# Команда /scan обрабатывается в main.py для доступа к scanner объекту


@router.callback_query(F.data.startswith("confirm_delete_"))
async def confirm_delete_product(callback: CallbackQuery):
    """Показать окно подтверждения удаления товара"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступно только администраторам", show_alert=True)
        return
    
    try:
        # Извлекаем SKU и страницу из callback_data
        parts = callback.data.split("_")
        sku = parts[2]  # confirm_delete_{sku}_{page}
        list_page = int(parts[3]) if len(parts) > 3 else 1
        
        products_db = ProductsDB(Config.DB_PATH)
        product_sellers_db = ProductSellersDB(Config.DB_PATH)
        
        # Получаем информацию о товаре
        product = await products_db.get_product(sku)
        if not product:
            await callback.answer("Товар не найден", show_alert=True)
            return
        
        # Получаем количество продавцов
        sellers_list = await product_sellers_db.get_sellers_for_product(sku, active_only=True)
        sellers_count = len(sellers_list)
        
        # Формируем сообщение подтверждения
        title = product.get('title') or 'Без названия'
        added_at = product.get('added_at', '—')
        
        text = "<b>Удаление товара</b>\n\n"
        text += f"<b>Название:</b> {title}\n"
        text += f"<b>SKU:</b> {hcode(sku)}\n"
        text += f"<b>Продавцов:</b> {sellers_count}\n"
        text += f"<b>Добавлен:</b> {added_at}\n\n"
        text += "Все связи с продавцами будут удалены.\n\n"
        text += "<b>Вы уверены?</b>"
        
        # Кнопки подтверждения
        keyboard = [
            [
                InlineKeyboardButton(text="Отмена", callback_data=f"product_{sku}_1_{list_page}"),
                InlineKeyboardButton(text="Удалить", callback_data=f"delete_confirmed_{sku}_{list_page}")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка в confirm_delete_product: {e}", exc_info=True)
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("delete_confirmed_"))
async def delete_product_confirmed(callback: CallbackQuery):
    """Выполнить удаление товара после подтверждения"""
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступно только администраторам", show_alert=True)
        return
    
    try:
        # Извлекаем SKU и страницу из callback_data
        parts = callback.data.split("_")
        sku = parts[2]  # delete_confirmed_{sku}_{page}
        list_page = int(parts[3]) if len(parts) > 3 else 1
        
        products_db = ProductsDB(Config.DB_PATH)
        
        # Получаем название для сообщения
        product = await products_db.get_product(sku)
        title = product.get('title', 'Неизвестный товар') if product else 'Неизвестный товар'
        
        # Удаляем товар
        success = await products_db.delete_product(sku)
        
        if success:
            logger.info(f"Товар {sku} удален пользователем {callback.from_user.id}")
            
            # Показываем сообщение об успехе
            await callback.message.edit_text(
                f"<b>Товар удален</b>\n\n"
                f"<b>Название:</b> {title}\n"
                f"<b>SKU:</b> {hcode(sku)}\n\n"
                f"Все связи с продавцами также удалены.",
                parse_mode="HTML"
            )
            
            # Через 2 секунды показываем список товаров
            await callback.answer("Товар успешно удален", show_alert=False)
            
            # Возвращаем к списку товаров на сохраненную страницу
            import asyncio
            await asyncio.sleep(2)
            
            products = await products_db.get_all_products_with_sellers_count()
            
            if not products:
                await callback.message.edit_text("Нет отслеживаемых товаров")
                return
            
            per_page = 10
            total = len(products)
            total_pages = (total + per_page - 1) // per_page
            
            # Проверяем, что страница в допустимом диапазоне
            if list_page > total_pages:
                list_page = total_pages
            if list_page < 1:
                list_page = 1
            
            # Вычисляем индексы для пагинации
            start_idx = (list_page - 1) * per_page
            end_idx = start_idx + per_page
            products_page = products[start_idx:end_idx]
            
            text = f"<b>Отслеживаемые товары ({total})</b>\n\n"
            text += f"<i>Нажмите на товар, чтобы увидеть продавцов</i>\n"
            text += f"Страница {list_page}/{total_pages}\n\n"
            
            keyboard = []
            for product in products_page:
                title = product.get('title') or 'Без названия'
                sku = product['master_sku']
                sellers_count = product.get('sellers_count', 0)
                
                display_title = title[:30] + '...' if len(title) > 30 else title
                button_text = f"{display_title} ({sellers_count})"
                
                keyboard.append([
                    InlineKeyboardButton(
                        text=button_text,
                        callback_data=f"product_{sku}_1_{list_page}"
                    )
                ])
            
            # Кнопки навигации
            nav_buttons = []
            if list_page > 1:
                nav_buttons.append(
                    InlineKeyboardButton(
                        text="Назад",
                        callback_data=f"list_page_{list_page-1}"
                    )
                )
            
            if list_page < total_pages:
                nav_buttons.append(
                    InlineKeyboardButton(
                        text="Вперед",
                        callback_data=f"list_page_{list_page+1}"
                    )
                )
            
            if nav_buttons:
                keyboard.append(nav_buttons)
            
            # Кнопка поиска
            keyboard.append([
                InlineKeyboardButton(
                    text="Поиск товара",
                    callback_data="search_products"
                )
            ])
            
            reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
            
            await callback.message.edit_text(
                text,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            
        else:
            await callback.message.edit_text(
                f"Товар с SKU {hcode(sku)} не найден",
                parse_mode="HTML"
            )
            await callback.answer("Товар не найден", show_alert=True)
            
    except Exception as e:
        logger.error(f"Ошибка в delete_product_confirmed: {e}", exc_info=True)
        await callback.message.edit_text("Ошибка при удалении товара")
        await callback.answer("Ошибка", show_alert=True)


# ============================================
# ОБРАБОТЧИКИ КНОПОК ПОСТОЯННОЙ КЛАВИАТУРЫ
# ============================================

@router.message(F.text == "Мои товары")
async def button_list_products(message: Message):
    """Кнопка: Мои товары"""
    await cmd_list(message)


@router.message(F.text == "Новые продавцы")
async def button_recent_sellers(message: Message):
    """Кнопка: Новые продавцы"""
    await cmd_recent(message)


@router.message(F.text == "Статистика")
async def button_stats(message: Message):
    """Кнопка: Статистика"""
    await cmd_stats(message)


@router.message(F.text == "Добавить товар")
async def button_add_product(message: Message):
    """Кнопка: Добавить товар"""
    if not is_admin(message.from_user.id):
        await message.answer("Доступно только администраторам")
        return
    
    await message.answer(
        "<b>Добавление товара</b>\n\n"
        "Отправьте URL товара с Kaspi.kz\n\n"
        "<b>Пример:</b>\n"
        "https://kaspi.kz/shop/p/название-107664472/\n"
        "или\n"
        "https://l.kaspi.kz/shp/_56n26Vl4X8\n\n"
        "Или используйте команду:\n"
        "/add <code>&lt;url&gt;</code>",
        parse_mode="HTML"
    )


# Кнопка "Сканировать" обрабатывается в main.py для доступа к scanner объекту


@router.message(F.text.regexp(r"https://(l\.)?kaspi\.kz"))
async def handle_kaspi_url(message: Message):
    """Обработка URL Kaspi (для добавления товара через кнопку)"""
    if not is_admin(message.from_user.id):
        await message.answer("Доступно только администраторам")
        return
    
    url = message.text.strip()
    
    # Валидация URL
    if 'kaspi.kz' not in url:
        await message.answer("Неверный формат URL Kaspi")
        return
    
    # Извлечь master_sku
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
        
        # Получить название товара
        product_title = None
        try:
            parser = KaspiParser(Config.PROXY_URL)
            
            # Сначала пытаемся получить через product API
            product_info = await parser.get_product_info(master_sku)
            if product_info:
                product_title = (
                    product_info.get('title') or 
                    product_info.get('name') or 
                    product_info.get('productName')
                )
            
            # Если не получилось - через offers
            if not product_title:
                success, offers = await parser.get_product_offers(master_sku)
                if success and offers and len(offers) > 0:
                    product_title = (
                        offers[0].get('productName') or 
                        offers[0].get('title') or 
                        offers[0].get('name')
                    )
        except Exception as e:
            logger.warning(f"Не удалось получить название товара: {e}")
        
        # Добавить товар
        success = await products_db.add_product(master_sku, url, title=product_title)
        
        if success:
            title_text = product_title if product_title else "Без названия"
            await message.answer(
                f"<b>Товар добавлен</b>\n\n"
                f"<b>Название:</b> {title_text}\n"
                f"<b>SKU:</b> {hcode(master_sku)}\n"
                f"<b>URL:</b> {url[:50]}...\n\n"
                f"Будет проверяться каждые 12 часов",
                parse_mode="HTML"
            )
            logger.info(f"Товар {master_sku} добавлен пользователем {message.from_user.id}")
        else:
            await message.answer("Ошибка добавления товара")
            
    except Exception as e:
        logger.error(f"Ошибка добавления товара: {e}", exc_info=True)
        await message.answer("Произошла ошибка при добавлении товара")


# ============================================================================
# РАБОТА С ПРОДАВЦАМИ
# ============================================================================

@router.message(Command("sellers"))
async def cmd_sellers(message: Message):
    """Команда /sellers - показать всех продавцов"""
    try:
        await show_sellers_list(message, page=1)
    except Exception as e:
        logger.error(f"Ошибка в cmd_sellers: {e}", exc_info=True)
        await message.answer("Ошибка получения списка продавцов")


@router.message(F.text == "Все продавцы")
async def button_all_sellers(message: Message):
    """Кнопка 'Все продавцы'"""
    try:
        await show_sellers_list(message, page=1)
    except Exception as e:
        logger.error(f"Ошибка в button_all_sellers: {e}", exc_info=True)
        await message.answer("Ошибка получения списка продавцов")


def _seller_was_sent_manual_whatsapp(seller: dict) -> bool:
    return bool(seller.get("manual_products_sent_at"))


def _format_seller_button_text(seller: dict) -> str:
    merchant_name = seller["merchant_name"]
    product_count = seller.get("product_count", 0)
    display_name = merchant_name[:35] + "..." if len(merchant_name) > 35 else merchant_name
    marker = f"{MANUAL_WHATSAPP_MARKER} " if _seller_was_sent_manual_whatsapp(seller) else ""
    return f"{marker}{display_name} ({product_count})"


def _format_seller_list_line(idx: int, seller: dict) -> str:
    merchant_name = html.escape(seller["merchant_name"])
    product_count = seller.get("product_count", 0)
    marker = f"{MANUAL_WHATSAPP_MARKER} " if _seller_was_sent_manual_whatsapp(seller) else ""
    return f"{idx}. {marker}<b>{merchant_name}</b> ({product_count})\n"


def _build_sellers_list_view(
    sellers: list[dict],
    page: int,
    per_page: int = 20,
) -> tuple[str, InlineKeyboardMarkup, int]:
    total = len(sellers)
    total_pages = (total + per_page - 1) // per_page
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    sellers_page = sellers[start_idx:end_idx]

    text = "<b>Все продавцы</b>\n\n"
    text += f"Всего: {total} | Страница {page}/{total_pages}\n"
    text += f"{MANUAL_WHATSAPP_MARKER} — товары вручную отправлялись в WhatsApp\n\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"

    keyboard = []

    for idx, seller in enumerate(sellers_page, start_idx + 1):
        merchant_id = seller["merchant_id"]
        text += _format_seller_list_line(idx, seller)

        keyboard.append([
            InlineKeyboardButton(
                text=_format_seller_button_text(seller),
                callback_data=f"seller_{merchant_id}"
            )
        ])

    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Назад",
                callback_data=f"sellers_page_{page - 1}"
            )
        )

    if page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Вперед",
                callback_data=f"sellers_page_{page + 1}"
            )
        )

    if nav_buttons:
        keyboard.append(nav_buttons)

    return text, InlineKeyboardMarkup(inline_keyboard=keyboard), page


async def show_sellers_list(message: Message, page: int = 1):
    """Показать список всех продавцов с количеством товаров и пагинацией"""
    per_page = 20
    
    sellers_db = SellersDB(Config.DB_PATH)
    sellers = await sellers_db.get_all_sellers_with_product_count()
    
    if not sellers:
        await message.answer(
            "<b>Список продавцов пуст</b>\n\n"
            "Продавцы появятся после добавления товаров и первого сканирования",
            parse_mode="HTML"
        )
        return

    text, reply_markup, _ = _build_sellers_list_view(sellers, page, per_page)
    
    await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


@router.callback_query(F.data.startswith("sellers_page_"))
async def sellers_page_navigation(callback: CallbackQuery):
    """Навигация по страницам списка продавцов"""
    try:
        page = int(callback.data.split("_")[-1])
        per_page = 20
        
        sellers_db = SellersDB(Config.DB_PATH)
        sellers = await sellers_db.get_all_sellers_with_product_count()
        
        if not sellers:
            await callback.message.edit_text("Список продавцов пуст")
            return

        text, reply_markup, _ = _build_sellers_list_view(sellers, page, per_page)
        
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка в sellers_page_navigation: {e}", exc_info=True)
        await callback.answer("Ошибка", show_alert=True)


def _build_seller_details(
    seller_data: dict,
    page: int = 1,
    per_page: int = 10,
    tracking: dict | None = None,
    show_whatsapp_action: bool = False,
):
    """Формирует текст и клавиатуру карточки продавца с пагинацией товаров."""
    merchant_id = seller_data['merchant_id']
    merchant_name = seller_data['merchant_name']
    phone = seller_data.get('phone') or 'недоступен'
    products = seller_data.get('products', [])
    total = len(products)
    total_pages = max(1, (total + per_page - 1) // per_page)

    # Защита от выхода за диапазон
    page = max(1, min(page, total_pages))

    text = f"<b>{merchant_name}</b>\n\n"
    text += f"<b>Телефон:</b> <code>{phone}</code>\n"
    text += f"<b>Товаров:</b> {total}\n"
    if total_pages > 1:
        text += f"<b>Страница:</b> {page}/{total_pages}\n"

    if tracking and tracking.get("manual_products_sent_at"):
        sent_at = tracking["manual_products_sent_at"]
        try:
            from datetime import datetime as _dt
            sent_at = _dt.fromisoformat(sent_at).strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            pass

        initial = tracking.get("manual_products_initial_count")
        if initial is not None:
            initial = int(initial)
            detached = max(initial - total, 0)
            if total == 0:
                progress_status = "полностью открепился"
            elif detached > 0:
                progress_status = "частично открепился"
            elif total > initial:
                progress_status = "товаров стало больше"
            else:
                progress_status = "без изменений"

            text += "\n<b>Отслеживание после WhatsApp:</b>\n"
            text += f"Отправлено: {sent_at}\n"
            text += f"Было товаров: {initial}\n"
            text += f"Осталось: {total}\n"
            text += f"Откреплено: {detached}\n"
            text += f"Статус: {progress_status}\n"

    text += "\n━━━━━━━━━━━━━━━━━━━━\n\n"

    if not products:
        text += "<i>У продавца нет активных товаров</i>"
    else:
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_products = products[start_idx:end_idx]

        text += "<b>Товары:</b>\n\n"

        for idx, product in enumerate(page_products, start_idx + 1):
            title = product.get('title') or 'Без названия'
            price = product.get('price', 0)
            product_id = product['product_id']
            url = product.get('url', '')

            if len(title) > 40:
                title = title[:40] + '...'

            text += f"{idx}. <b>{title}</b>\n"
            text += f"   {price:,.0f} ₸\n"

            first_seen = product.get('first_seen')
            if first_seen:
                from datetime import datetime as _dt
                try:
                    seen_dt = _dt.fromisoformat(first_seen)
                    days = (now_kz().replace(tzinfo=None) - seen_dt).days
                    text += f"   Присоединён: {days} дн.\n"
                except (ValueError, TypeError):
                    pass

            if url:
                text += f"   <a href='{url}'>Открыть на Kaspi</a>\n"
            else:
                kaspi_url = f"https://kaspi.kz/shop/p/{product_id}/"
                text += f"   <a href='{kaspi_url}'>Открыть на Kaspi</a>\n"

            text += f"   <code>SKU: {product_id}</code>\n\n"

    # Кнопки навигации по товарам продавца
    keyboard = []
    if show_whatsapp_action and products and seller_data.get("phone"):
        keyboard.append([
            InlineKeyboardButton(
                text="Отправить товары в WhatsApp",
                callback_data=f"wa_products_send_{merchant_id}",
            )
        ])

    if total_pages > 1:
        nav_buttons = []
        if page > 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=f"sellerpg_{merchant_id}_{page - 1}"
                )
            )
        nav_buttons.append(
            InlineKeyboardButton(
                text=f"{page}/{total_pages}",
                callback_data="noop"
            )
        )
        if page < total_pages:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Вперед",
                    callback_data=f"sellerpg_{merchant_id}_{page + 1}"
                )
            )
        keyboard.append(nav_buttons)

    keyboard.append([
        InlineKeyboardButton(
            text="Назад к списку",
            callback_data="sellers_page_1"
        )
    ])

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    return text, reply_markup


async def _get_seller_tracking(merchant_id: str) -> dict | None:
    """Получить актуальный снимок ручной WhatsApp-отправки."""
    workflow_db = SellerWorkflowDB(Config.DB_PATH)
    workflow = await workflow_db.get_latest_workflow_for_seller(merchant_id)
    if not workflow or workflow.get("status") in ("CLOSED", "DETACHED"):
        return None
    return workflow


def _get_workflow_engine():
    """Получить глобальный WorkflowEngine, созданный в main.py."""
    import sys

    main_module = sys.modules.get("__main__")
    if main_module is None:
        return None
    return getattr(main_module, "workflow_engine", None)


@router.callback_query(F.data.startswith("sellerpg_"))
async def seller_details_page_navigation(callback: CallbackQuery):
    """Навигация по страницам товаров продавца"""
    try:
        parts = callback.data.split("_")
        merchant_id = parts[1]
        page = int(parts[2])

        sellers_db = SellersDB(Config.DB_PATH)
        seller_data = await sellers_db.get_seller_with_products(merchant_id)

        if not seller_data:
            await callback.answer("Продавец не найден", show_alert=True)
            return

        tracking = await _get_seller_tracking(merchant_id)
        text, reply_markup = _build_seller_details(
            seller_data,
            page,
            tracking=tracking,
            show_whatsapp_action=is_admin(callback.from_user.id),
        )
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        await callback.answer()

    except Exception as e:
        logger.error(f"Ошибка в seller_details_page_navigation: {e}", exc_info=True)
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    """Пустой callback для неактивных кнопок"""
    await callback.answer()


@router.callback_query(F.data.startswith("seller_"))
async def show_seller_details(callback: CallbackQuery):
    """Показать детали продавца с его товарами"""
    try:
        merchant_id = callback.data.replace("seller_", "")

        sellers_db = SellersDB(Config.DB_PATH)
        seller_data = await sellers_db.get_seller_with_products(merchant_id)

        if not seller_data:
            await callback.answer("Продавец не найден", show_alert=True)
            return

        tracking = await _get_seller_tracking(merchant_id)
        text, reply_markup = _build_seller_details(
            seller_data,
            page=1,
            tracking=tracking,
            show_whatsapp_action=is_admin(callback.from_user.id),
        )
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        await callback.answer()

    except Exception as e:
        logger.error(f"Ошибка в show_seller_details: {e}", exc_info=True)
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("wa_products_send_"))
async def send_seller_products_whatsapp(callback: CallbackQuery):
    """Отправить продавцу актуальный список товаров через WhatsApp."""
    if not is_admin(callback.from_user.id):
        await callback.answer("Доступно только администраторам", show_alert=True)
        return

    merchant_id = callback.data.removeprefix("wa_products_send_")
    engine = _get_workflow_engine()
    if engine is None:
        await callback.answer("WhatsApp-сервис не инициализирован", show_alert=True)
        return

    await callback.answer("Отправляю в WhatsApp...")
    result = await engine.send_products_to_seller(merchant_id)
    if not result.success:
        error_messages = {
            "seller_not_found": "Продавец не найден",
            "phone_missing": "У продавца нет телефона",
            "no_active_products": "У продавца нет активных товаров",
            "rate_limited": "Достигнут лимит сообщений этому продавцу",
            "whatsapp_error": "WhatsApp временно недоступен",
            "send_failed": "Не удалось отправить первое предупреждение",
        }
        await callback.message.answer(
            error_messages.get(result.reason, "Не удалось отправить сообщение")
        )
        return

    seller_data = await SellersDB(Config.DB_PATH).get_seller_with_products(
        merchant_id
    )
    tracking = await _get_seller_tracking(merchant_id)
    text, reply_markup = _build_seller_details(
        seller_data,
        page=1,
        tracking=tracking,
        show_whatsapp_action=True,
    )
    await callback.message.edit_text(
        "<b>Сообщение отправлено в WhatsApp</b>\n\n" + text,
        reply_markup=reply_markup,
        parse_mode="HTML",
    )


# ============================================================================
# ПОИСК ПРОДАВЦОВ
# ============================================================================

@router.message(F.text == "Поиск продавца")
async def button_seller_search(message: Message, state: FSMContext):
    """Кнопка 'Поиск продавца' - ждать запрос продавца."""
    await state.set_state(SellerSearchFSM.waiting_query)
    await message.answer(
        "<b>Поиск продавца</b>\n\n"
        "Введите название магазина, телефон или ID продавца.\n\n"
        "<b>Примеры:</b>\n"
        "• Alpha Market\n"
        "• 87011234567\n"
        "• M777\n\n"
        "Или используйте команду:\n"
        "/seller_search <code>&lt;запрос&gt;</code>",
        parse_mode="HTML",
    )


@router.message(Command("seller_search"))
async def cmd_seller_search(message: Message, state: FSMContext):
    """Команда /seller_search <запрос> - поиск продавцов."""
    args = message.text.split(maxsplit=1)

    if len(args) < 2:
        await button_seller_search(message, state)
        return

    query = args[1].strip()
    if len(query) < 2:
        await message.answer("Запрос слишком короткий. Минимум 2 символа.")
        return

    try:
        await perform_seller_search(message, query)
    except Exception as e:
        logger.error(f"Ошибка в cmd_seller_search: {e}", exc_info=True)
        await message.answer("Ошибка при поиске продавцов")


@router.message(SellerSearchFSM.waiting_query, F.text)
async def process_seller_search_query(message: Message, state: FSMContext):
    """Обработать следующий текст после кнопки поиска продавца."""
    query = message.text.strip()

    if query in MENU_BUTTONS:
        await state.clear()
        return

    if len(query) < 2:
        await message.answer(
            "Запрос слишком короткий. Минимум 2 символа.\n\n"
            "Введите название магазина, телефон или ID продавца."
        )
        return

    await state.clear()

    try:
        await perform_seller_search(message, query)
    except Exception as e:
        logger.error(f"Ошибка при поиске продавцов: {e}", exc_info=True)
        await message.answer("Ошибка при поиске продавцов")


async def perform_seller_search(message: Message, query: str):
    """Выполнить поиск продавцов и показать результаты."""
    sellers_db = SellersDB(Config.DB_PATH)
    results = await sellers_db.search_sellers(query)

    if not results:
        await message.answer(
            f"<b>Поиск продавца: \"{html.escape(query)}\"</b>\n\n"
            "Ничего не найдено.\n\n"
            "Попробуйте название магазина, телефон или ID продавца.",
            parse_mode="HTML",
        )
        return

    text = f"<b>Результаты поиска продавцов: \"{html.escape(query)}\"</b>\n\n"
    text += f"Найдено: {len(results)}\n"
    text += f"{MANUAL_WHATSAPP_MARKER} — товары вручную отправлялись в WhatsApp\n\n"

    keyboard = []
    for idx, seller in enumerate(results, 1):
        text += _format_seller_list_line(idx, seller)
        keyboard.append([
            InlineKeyboardButton(
                text=_format_seller_button_text(seller),
                callback_data=f"seller_{seller['merchant_id']}",
            )
        ])

    text += "\n<i>Нажмите на продавца, чтобы открыть карточку</i>"

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


# ============================================================================
# ПОИСК ТОВАРОВ
# ============================================================================

@router.message(F.text == "Поиск")
async def button_search(message: Message):
    """Кнопка 'Поиск' - показать инструкцию"""
    await message.answer(
        "<b>Поиск товаров</b>\n\n"
        "Отправьте сообщение с запросом для поиска.\n\n"
        "<b>Примеры:</b>\n"
        "• Навиен\n"
        "• для мяса\n"
        "• 104886899\n"
        "• Mouse\n\n"
        "Или используйте команду:\n"
        "/search <code>&lt;запрос&gt;</code>\n\n"
        "<i>Поиск не чувствителен к регистру</i>",
        parse_mode="HTML"
    )


@router.message(Command("search"))
async def cmd_search(message: Message):
    """Команда /search <запрос> - поиск товаров"""
    # Парсинг аргументов
    args = message.text.split(maxsplit=1)
    
    if len(args) < 2:
        await message.answer(
            "<b>Поиск товаров</b>\n\n"
            "Введите запрос для поиска:\n"
            "/search <code>&lt;название или SKU&gt;</code>\n\n"
            "<b>Примеры:</b>\n"
            "/search Навиен\n"
            "/search 104886899\n"
            "/search Mouse",
            parse_mode="HTML"
        )
        return
    
    query = args[1].strip()
    
    if len(query) < 2:
        await message.answer("Запрос слишком короткий. Минимум 2 символа.")
        return
    
    try:
        await perform_search(message, query)
    except Exception as e:
        logger.error(f"Ошибка в cmd_search: {e}", exc_info=True)
        await message.answer("Ошибка при поиске товаров")


@router.callback_query(F.data == "search_products")
async def callback_search_products(callback: CallbackQuery):
    """Callback для запуска поиска из списка товаров"""
    await callback.message.answer(
        "<b>Поиск товаров</b>\n\n"
        "Отправьте сообщение с запросом для поиска:\n\n"
        "<b>Примеры:</b>\n"
        "• Навиен\n"
        "• 104886899\n"
        "• Mouse\n\n"
        "Или используйте команду:\n"
        "/search <code>&lt;запрос&gt;</code>",
        parse_mode="HTML"
    )
    await callback.answer()


async def perform_search(message: Message, query: str):
    """Выполнить поиск товаров и показать результаты"""
    products_db = ProductsDB(Config.DB_PATH)
    
    # Выполняем поиск
    results = await products_db.search_products(query)
    
    if not results:
        await message.answer(
            f"<b>Поиск: \"{query}\"</b>\n\n"
            f"Ничего не найдено.\n\n"
            f"Попробуйте другой запрос или проверьте правильность ввода.",
            parse_mode="HTML"
        )
        return
    
    # Формируем сообщение с результатами
    text = f"<b>Результаты поиска: \"{query}\"</b>\n\n"
    text += f"Найдено: {len(results)}\n\n"
    
    # Создаем кнопки для каждого найденного товара
    keyboard = []
    
    for idx, product in enumerate(results, 1):
        title = product.get('title') or 'Без названия'
        sku = product['master_sku']
        sellers_count = product.get('sellers_count', 0)
        
        # Сокращаем название
        display_title = title[:35] + '...' if len(title) > 35 else title
        
        # Показываем в тексте первые 10
        if idx <= 10:
            text += f"{idx}. <b>{title}</b> ({sellers_count})\n"
            text += f"   SKU: <code>{sku}</code>\n\n"
        
        # Кнопки для всех результатов с количеством продавцов
        keyboard.append([
            InlineKeyboardButton(
                text=f"{idx}. {display_title} ({sellers_count})",
                callback_data=f"product_{sku}_1_1"
            )
        ])
    
    if len(results) > 10:
        text += f"<i>...и ещё {len(results) - 10}</i>\n\n"
    
    text += "<i>Нажмите на товар, чтобы увидеть продавцов</i>"
    
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


# Универсальный обработчик текста для автоматического поиска
# Должен быть в конце, чтобы сработал только если другие обработчики не подошли
@router.message(F.text)
async def handle_text_search(message: Message):
    """Обработка обычного текста как поискового запроса"""
    # Игнорируем команды
    if message.text.startswith('/'):
        return
    
    # Игнорируем кнопки меню (они уже обработаны выше)
    if message.text in MENU_BUTTONS:
        return
    
    # Игнорируем URL Kaspi (они обработаны выше)
    if 'kaspi.kz' in message.text:
        return
    
    query = message.text.strip()
    
    # Минимальная длина запроса
    if len(query) < 2:
        await message.answer(
            "Запрос слишком короткий. Минимум 2 символа.\n\n"
            "Отправьте название товара или SKU для поиска."
        )
        return
    
    # Выполняем поиск
    try:
        await perform_search(message, query)
    except Exception as e:
        logger.error(f"Ошибка при автоматическом поиске: {e}", exc_info=True)
        await message.answer("Ошибка при поиске товаров")
