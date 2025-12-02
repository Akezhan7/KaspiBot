"""
Утилиты для Telegram бота
Форматирование сообщений, валидация, пагинация
"""
from typing import List
from parser import NewSellerInfo


def format_new_seller_notification(seller: NewSellerInfo) -> str:
    """Форматировать уведомление о новом продавце"""
    phone_text = seller.phone if seller.phone else "недоступен"
    title = seller.product_title if seller.product_title else "Без названия"
    
    message = f"""<b>Новый продавец</b>

<b>Товар:</b> {title}
<b>Магазин:</b> {seller.merchant_name}
<b>Цена:</b> {seller.price:,.0f} ₸
<b>Телефон:</b> {phone_text}
<b>Всего продавцов:</b> {seller.total_sellers}"""

    # Добавить информацию о других товарах этого продавца
    if seller.other_products and len(seller.other_products) > 0:
        count = len(seller.other_products)
        message += f"\n\n<b>Этот продавец уже на {count} других ваших товарах:</b>\n"
        
        # Показать до 5 товаров
        for idx, product in enumerate(seller.other_products[:5], 1):
            product_title = product.get('title', 'Без названия')
            product_price = product.get('price', 0)
            # Сокращаем длинное название
            if len(product_title) > 35:
                product_title = product_title[:35] + '...'
            message += f"  • {product_title} ({product_price:,.0f} ₸)\n"
        
        if count > 5:
            message += f"  <i>...и ещё {count - 5}</i>\n"
    
    message += f"\n<i>SKU: {seller.product_sku}</i>"
    
    return message


def format_grouped_notifications(sellers: List[NewSellerInfo]) -> str:
    """
    Форматировать групповое уведомление (30+ новых продавцов)
    Показывает первые 20 продавцов
    """
    count = len(sellers)
    
    message = f"<b>🆕 Найдено {count} новых продавцов</b>\n\n"
    
    # Показываем первые 20
    for idx, seller in enumerate(sellers[:20], 1):
        phone_text = seller.phone if seller.phone else "—"
        title = seller.product_title if seller.product_title else "Без названия"
        title_short = title[:40] + '...' if len(title) > 40 else title
        
        message += (
            f"{idx}. <b>{seller.merchant_name}</b>\n"
            f"   {title_short}\n"
            f"   {seller.price:,.0f} ₸  |  {phone_text}\n\n"
        )
    
    if count > 20:
        message += f"<i>...и ещё {count - 20}</i>\n\n"
    
    message += "<i>📊 Используйте /list чтобы увидеть всех</i>"
    
    return message


def validate_kaspi_url(url: str) -> bool:
    """
    Проверить валидность URL Kaspi
    
    Примеры валидных URL:
    - https://kaspi.kz/shop/p/название-107664472/
    - https://kaspi.kz/shop/p/название-107664472
    - http://kaspi.kz/shop/p/107664472
    """
    import re
    # Проверяем, что это URL Kaspi с товаром и есть SKU (8+ цифр)
    pattern = r'kaspi\.kz/shop/p/.*\d{8,}'
    return bool(re.search(pattern, url))


def paginate_list(items: list, page: int, per_page: int = 10) -> tuple:
    """
    Пагинация списка
    
    Returns:
        (items_on_page, total_pages, has_next, has_prev)
    """
    total = len(items)
    total_pages = (total + per_page - 1) // per_page  # Округление вверх
    
    if total_pages == 0:
        return ([], 0, False, False)
    
    # Корректировка номера страницы
    if page < 1:
        page = 1
    elif page > total_pages:
        page = total_pages
    
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    
    items_on_page = items[start_idx:end_idx]
    has_next = page < total_pages
    has_prev = page > 1
    
    return (items_on_page, total_pages, has_next, has_prev)
