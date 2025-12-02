"""
Scanner - основная логика сканирования товаров
"""
import asyncio
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from config import Config
from database import ProductsDB, SellersDB, ProductSellersDB, ScanLogsDB
from parser import ProxyManager, KaspiParser

logger = logging.getLogger(__name__)


class NewSellerInfo:
    """Информация о новом продавце для уведомления"""
    
    def __init__(
        self, 
        product_sku: str,
        product_title: str,
        merchant_name: str,
        price: float,
        phone: Optional[str],
        total_sellers: int,
        other_products: Optional[List[Dict[str, Any]]] = None
    ):
        self.product_sku = product_sku
        self.product_title = product_title
        self.merchant_name = merchant_name
        self.price = price
        self.phone = phone
        self.total_sellers = total_sellers
        self.other_products = other_products or []  # Список других товаров продавца


class ProductScanner:
    """Сканер товаров Kaspi"""
    
    def __init__(self):
        self.db_path = Config.DB_PATH
        
        # База данных
        self.products_db = ProductsDB(self.db_path)
        self.sellers_db = SellersDB(self.db_path)
        self.product_sellers_db = ProductSellersDB(self.db_path)
        self.scan_logs_db = ScanLogsDB(self.db_path)
        
        # Proxy и parser
        self.proxy_manager = ProxyManager()
        self.parser = KaspiParser(self.proxy_manager.get_proxy_url())
        
        # Список новых продавцов для уведомлений
        self.new_sellers: List[NewSellerInfo] = []
    
    async def scan_product(self, product: Dict[str, Any]) -> bool:
        """
        Сканировать один товар
        
        Args:
            product: Словарь с данными товара из БД
        
        Returns:
            True если успешно, False при ошибке
        """
        master_sku = product['master_sku']
        product_title = product.get('title', 'Без названия')
        
        try:
            # 1. Получить offers
            success, offers = await self.parser.get_product_offers(master_sku)
            await self.proxy_manager.increment_and_check_ip()
            
            if not success:
                logger.error(f"Не удалось получить offers для {master_sku}")
                return False
            
            # Задержка после запроса товара
            await self.proxy_manager.delay_for_product()
            
            # Если название товара не было в БД или это fallback, получаем его
            needs_title_update = (
                not product_title or 
                product_title == 'Без названия' or 
                product_title.startswith('Товар ')  # Fallback название
            )
            
            if needs_title_update:
                # Сначала пробуем получить из offers (если есть)
                if offers and len(offers) > 0:
                    product_title = (
                        offers[0].get('productName') or 
                        offers[0].get('title') or 
                        offers[0].get('name')
                    )
                
                # Если в offers нет названия - запрашиваем через product API
                if not product_title or product_title == 'Без названия' or product_title.startswith('Товар '):
                    try:
                        product_info = await self.parser.get_product_info(master_sku)
                        await self.proxy_manager.increment_and_check_ip()
                        await self.proxy_manager.delay_for_product()
                        
                        if product_info:
                            product_title = (
                                product_info.get('title') or 
                                product_info.get('name') or 
                                product_info.get('productName')
                            )
                            logger.info(f"Получено название из product API: {product_title}")
                    except Exception as e:
                        logger.warning(f"Не удалось получить название через product API: {e}")
                
                # Обновим название в БД если нашли нормальное название
                if product_title and product_title != 'Без названия' and not product_title.startswith('Товар '):
                    await self.products_db.update_product_title(master_sku, product_title)
                    logger.info(f"Обновлено название товара {master_sku}: {product_title[:50]}")
            
            # 2. Обработать каждый offer
            active_seller_ids = []
            
            for offer in offers:
                parsed = self.parser.parse_offer(offer)
                merchant_id = parsed['merchant_id']
                merchant_name = parsed['merchant_name']
                price = parsed['price']
                
                if not merchant_id:
                    continue
                
                active_seller_ids.append(merchant_id)
                
                # 3. Проверить существование продавца
                seller_exists = await self.sellers_db.seller_exists(merchant_id)
                
                if not seller_exists:
                    # Новый продавец - получить телефон
                    phone = await self.parser.get_merchant_phone(merchant_id, master_sku)
                    await self.proxy_manager.increment_and_check_ip()
                    await self.proxy_manager.delay_for_merchant()
                    
                    # Сохранить продавца
                    await self.sellers_db.add_seller(merchant_id, merchant_name, phone)
                else:
                    phone = None
                
                # 4. Добавить/обновить связь товар-продавец
                is_new, was_inactive = await self.product_sellers_db.add_or_update_link(
                    master_sku, merchant_id, price
                )
                
                # 5. Если новый или вернулся - добавить в уведомления
                if is_new or was_inactive:
                    # Проверить, не является ли продавец исключенным (например, собственный магазин)
                    is_excluded = merchant_name in Config.EXCLUDED_SELLER_NAMES
                    
                    if is_excluded:
                        logger.info(
                            f"Пропуск уведомления для исключенного продавца: "
                            f"{merchant_name} (товар {master_sku})"
                        )
                    else:
                        # Получить телефон если еще не получили
                        if phone is None:
                            seller = await self.sellers_db.get_seller(merchant_id)
                            phone = seller.get('phone') if seller else None
                        
                        # Подсчитать общее количество продавцов
                        sellers_list = await self.product_sellers_db.get_sellers_for_product(
                            master_sku, active_only=True
                        )
                        total_sellers = len(sellers_list)
                        
                        # Получить список других товаров этого продавца
                        other_products = await self.product_sellers_db.get_other_products_for_seller(
                            merchant_id, master_sku
                        )
                        
                        # Добавить в список уведомлений
                        new_seller = NewSellerInfo(
                            product_sku=master_sku,
                            product_title=product_title,
                            merchant_name=merchant_name,
                            price=price,
                            phone=phone,
                            total_sellers=total_sellers,
                            other_products=other_products
                        )
                        self.new_sellers.append(new_seller)
                        
                        logger.info(
                            f"{'Новый' if is_new else 'Вернулся'} продавец: "
                            f"{merchant_name} для товара {master_sku}"
                        )
            
            # 6. Деактивировать отсутствующих продавцов
            await self.product_sellers_db.deactivate_missing_sellers(
                master_sku, active_seller_ids
            )
            
            # 7. Обновить last_checked
            await self.products_db.update_last_checked(master_sku)
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка сканирования товара {master_sku}: {e}", exc_info=True)
            return False
    
    async def scan_all_products(self) -> Dict[str, Any]:
        """
        Сканировать все товары из БД
        
        Returns:
            Статистика сканирования
        """
        logger.info("=" * 50)
        logger.info("НАЧАЛО СКАНИРОВАНИЯ")
        logger.info("=" * 50)
        
        # Сброс счетчиков
        self.proxy_manager.reset_counter()
        self.new_sellers = []
        
        # Создать запись в scan_logs
        scan_id = await self.scan_logs_db.start_scan()
        
        # Получить все товары
        products = await self.products_db.get_all_products()
        total_products = len(products)
        
        if total_products == 0:
            logger.warning("Нет товаров для сканирования")
            await self.scan_logs_db.finish_scan(scan_id, 0, 0, "Нет товаров")
            return {
                "scan_id": scan_id,
                "total_products": 0,
                "successful": 0,
                "failed": 0,
                "new_sellers_count": 0,
                "new_sellers": []
            }
        
        logger.info(f"Всего товаров для сканирования: {total_products}")
        
        # Счетчики
        successful = 0
        failed = 0
        errors = []
        
        # Сканирование
        for idx, product in enumerate(products, 1):
            logger.info(f"\n[{idx}/{total_products}] Сканирование: {product['master_sku']}")
            
            try:
                result = await self.scan_product(product)
                if result:
                    successful += 1
                else:
                    failed += 1
                    errors.append(f"Ошибка товара {product['master_sku']}")
            except Exception as e:
                failed += 1
                error_msg = f"Исключение при сканировании {product['master_sku']}: {e}"
                errors.append(error_msg)
                logger.error(error_msg, exc_info=True)
        
        # Финализация
        new_sellers_count = len(self.new_sellers)
        errors_text = "\n".join(errors[:10]) if errors else None  # Первые 10 ошибок
        
        await self.scan_logs_db.finish_scan(
            scan_id, successful, new_sellers_count, errors_text
        )
        
        logger.info("=" * 50)
        logger.info("ЗАВЕРШЕНИЕ СКАНИРОВАНИЯ")
        logger.info(f"Успешно: {successful}/{total_products}")
        logger.info(f"Ошибок: {failed}/{total_products}")
        logger.info(f"Новых продавцов: {new_sellers_count}")
        logger.info("=" * 50)
        
        return {
            "scan_id": scan_id,
            "total_products": total_products,
            "successful": successful,
            "failed": failed,
            "new_sellers_count": new_sellers_count,
            "new_sellers": self.new_sellers,
            "errors": errors
        }
    
    def get_new_sellers(self) -> List[NewSellerInfo]:
        """Получить список новых продавцов из последнего сканирования"""
        return self.new_sellers
