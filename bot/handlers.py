"""
Telegram bot handlers
Обработка команд пользователя
"""
import logging
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.markdown import hcode

from config import Config
from database import ProductsDB, ProductSellersDB, ScanLogsDB, SellersDB, RecentSellersDB
from parser import KaspiParser
from .utils import validate_kaspi_url, paginate_list

logger = logging.getLogger(__name__)

router = Router()


# Постоянная клавиатура для пользователей
def get_main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Получить главную клавиатуру с кнопками"""
    buttons = [
        [KeyboardButton(text="Мои товары"), KeyboardButton(text="Все продавцы")],
        [KeyboardButton(text="Новые продавцы"), KeyboardButton(text="Статистика")]
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
        "/sellers — все продавцы\n"
        "/recent — последние новые продавцы\n"
        "/remove <code>&lt;sku&gt;</code> — удалить товар\n"
        "/stats — статистика\n"
        "/scan — принудительная проверка (admin)\n\n"
        "Проверка каждые 12 часов автоматически",
        parse_mode="HTML",
        reply_markup=keyboard
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
                product_title = (
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
                    product_title = (
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
        products = await products_db.get_all_products()
        
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
            text += "⚠️ <i>Продавцов пока нет.\n"
            text += "Они появятся после следующего сканирования.</i>\n"
        else:
            # Есть продавцы - показываем их
            total_pages = (total + per_page - 1) // per_page
            
            # Вычисляем индексы для пагинации
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            sellers_page = sellers_list[start_idx:end_idx]
            
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
                
                # Получаем список других товаров этого продавца
                other_products = await product_sellers_db.get_other_products_for_seller(
                    seller_id, sku
                )
                
                text += f"{idx}. <b>{merchant_name}</b>\n"
                text += f"   Цена: {price:,.0f} ₸\n"
                text += f"   Телефон: <code>{phone}</code>\n"
                
                # Показываем информацию о других товарах
                if other_products:
                    count = len(other_products)
                    text += f"   Также на {count} других товарах [нажмите №{idx}]\n"
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
            if page > 1:
                nav_buttons.append(
                    InlineKeyboardButton(
                        text="Назад",
                        callback_data=f"product_{sku}_{page-1}"
                    )
                )
            
            # Кнопка "Вперед" (к следующей странице)
            if page < total_pages:
                nav_buttons.append(
                    InlineKeyboardButton(
                        text="Вперед",
                        callback_data=f"product_{sku}_{page+1}"
                    )
                )
            
            if nav_buttons:
                keyboard.append(nav_buttons)
        
        # Кнопка "К списку товаров"
        keyboard.append([
            InlineKeyboardButton(text="К списку товаров", callback_data="back_to_list")
        ])
        
        # Кнопка "Удалить товар" (только для админов)
        if is_admin(callback.from_user.id):
            keyboard.append([
                InlineKeyboardButton(text="Удалить товар", callback_data=f"confirm_delete_{sku}")
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
                    text="Вперед",
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
        # Извлекаем SKU из callback_data
        sku = callback.data.replace("confirm_delete_", "")
        
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
                InlineKeyboardButton(text="Отмена", callback_data=f"product_{sku}_1"),
                InlineKeyboardButton(text="Удалить", callback_data=f"delete_confirmed_{sku}")
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
        # Извлекаем SKU из callback_data
        sku = callback.data.replace("delete_confirmed_", "")
        
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
            
            # Возвращаем к списку товаров
            import asyncio
            await asyncio.sleep(2)
            
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
            
            if total_pages > 1:
                keyboard.append([
                    InlineKeyboardButton(
                        text="Вперед",
                        callback_data="list_page_2"
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
    
    total = len(sellers)
    total_pages = (total + per_page - 1) // per_page
    
    # Пагинация
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    sellers_page = sellers[start_idx:end_idx]
    
    # Формируем сообщение
    text = f"<b>Все продавцы</b>\n\n"
    text += f"Всего: {total} | Страница {page}/{total_pages}\n\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    # Создаем кнопки для каждого продавца
    keyboard = []
    
    for idx, seller in enumerate(sellers_page, start_idx + 1):
        merchant_name = seller['merchant_name']
        product_count = seller['product_count']
        merchant_id = seller['merchant_id']
        
        # Сокращаем название если длинное
        display_name = merchant_name[:35] + '...' if len(merchant_name) > 35 else merchant_name
        
        text += f"{idx}. <b>{merchant_name}</b> ({product_count})\n"
        
        # Кнопка для открытия продавца
        keyboard.append([
            InlineKeyboardButton(
                text=f"{display_name} ({product_count})",
                callback_data=f"seller_{merchant_id}"
            )
        ])
    
    # Кнопки навигации
    nav_buttons = []
    if page > 1:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Назад",
                callback_data=f"sellers_page_{page-1}"
            )
        )
    
    if page < total_pages:
        nav_buttons.append(
            InlineKeyboardButton(
                text="Вперед",
                callback_data=f"sellers_page_{page+1}"
            )
        )
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
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
        
        total = len(sellers)
        total_pages = (total + per_page - 1) // per_page
        
        # Пагинация
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        sellers_page = sellers[start_idx:end_idx]
        
        # Формируем сообщение
        text = f"<b>Все продавцы</b>\n\n"
        text += f"Всего: {total} | Страница {page}/{total_pages}\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        # Создаем кнопки
        keyboard = []
        
        for idx, seller in enumerate(sellers_page, start_idx + 1):
            merchant_name = seller['merchant_name']
            product_count = seller['product_count']
            merchant_id = seller['merchant_id']
            
            display_name = merchant_name[:35] + '...' if len(merchant_name) > 35 else merchant_name
            
            text += f"{idx}. <b>{merchant_name}</b> ({product_count})\n"
            
            keyboard.append([
                InlineKeyboardButton(
                    text=f"{display_name} ({product_count})",
                    callback_data=f"seller_{merchant_id}"
                )
            ])
        
        # Навигация
        nav_buttons = []
        if page > 1:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Назад",
                    callback_data=f"sellers_page_{page-1}"
                )
            )
        
        if page < total_pages:
            nav_buttons.append(
                InlineKeyboardButton(
                    text="Вперед",
                    callback_data=f"sellers_page_{page+1}"
                )
            )
        
        if nav_buttons:
            keyboard.append(nav_buttons)
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка в sellers_page_navigation: {e}", exc_info=True)
        await callback.answer("Ошибка", show_alert=True)


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
        
        # Формируем сообщение
        merchant_name = seller_data['merchant_name']
        phone = seller_data.get('phone') or 'недоступен'
        products = seller_data.get('products', [])
        
        text = f"<b>{merchant_name}</b>\n\n"
        text += f"<b>Телефон:</b> <code>{phone}</code>\n"
        text += f"<b>Товаров:</b> {len(products)}\n\n"
        text += "━━━━━━━━━━━━━━━━━━━━\n\n"
        
        if not products:
            text += "<i>У продавца нет активных товаров</i>"
        else:
            text += "<b>Товары:</b>\n\n"
            
            for idx, product in enumerate(products, 1):
                title = product.get('title') or 'Без названия'
                price = product.get('price', 0)
                product_id = product['product_id']
                url = product.get('url', '')
                
                # Сокращаем название
                if len(title) > 40:
                    title = title[:40] + '...'
                
                text += f"{idx}. <b>{title}</b>\n"
                text += f"   {price:,.0f} ₸\n"
                
                # Добавляем ссылку на Kaspi
                if url:
                    text += f"   <a href='{url}'>Открыть на Kaspi</a>\n"
                else:
                    # Если URL нет, создаем базовую ссылку по SKU
                    kaspi_url = f"https://kaspi.kz/shop/p/{product_id}/"
                    text += f"   <a href='{kaspi_url}'>Открыть на Kaspi</a>\n"
                
                text += f"   <code>SKU: {product_id}</code>\n\n"
        
        # Кнопка "Назад к списку"
        keyboard = [[
            InlineKeyboardButton(
                text="Назад к списку",
                callback_data="sellers_page_1"
            )
        ]]
        
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        
        await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        await callback.answer()
        
    except Exception as e:
        logger.error(f"Ошибка в show_seller_details: {e}", exc_info=True)
        await callback.answer("Ошибка", show_alert=True)

