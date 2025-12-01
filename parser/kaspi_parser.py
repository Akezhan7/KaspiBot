"""
Парсер Kaspi.kz
Получение offers и телефонов продавцов
"""
import httpx
import re
import asyncio
import logging
from typing import List, Dict, Any, Optional, Tuple
from config import Config

logger = logging.getLogger(__name__)


class KaspiParser:
    """Парсинг данных с Kaspi.kz"""
    
    def __init__(self, proxy_url: Optional[str] = None):
        self.proxy_url = proxy_url
        self.timeout = Config.REQUEST_TIMEOUT
        self.retry_attempts = Config.RETRY_ATTEMPTS
        self.retry_delays = Config.RETRY_DELAYS
        
        # Headers для запросов
        self.headers = Config.KASPI_HEADERS.copy()
    
    async def _make_request(
        self, 
        url: str, 
        response_type: str = "json",
        method: str = "GET",
        json_body: dict = None
    ) -> Tuple[bool, Any]:
        """
        Выполнить HTTP запрос с retry логикой
        
        Args:
            url: URL для запроса
            response_type: 'json' или 'text'
            method: 'GET' или 'POST'
            json_body: JSON тело для POST запроса
        
        Returns:
            (success, data): True/False и данные или None
        """
        for attempt in range(self.retry_attempts):
            try:
                # Создаем клиента с прокси (если указан)
                client_kwargs = {
                    "timeout": self.timeout,
                    "follow_redirects": True
                }
                
                # В httpx прокси передается как строка, не словарь
                if self.proxy_url:
                    client_kwargs["proxy"] = self.proxy_url
                
                async with httpx.AsyncClient(**client_kwargs) as client:
                    if method == "POST":
                        response = await client.post(url, headers=self.headers, json=json_body)
                    else:
                        response = await client.get(url, headers=self.headers)
                    
                    response.raise_for_status()
                    
                    if response_type == "json":
                        data = response.json()
                    else:
                        data = response.text
                    
                    return (True, data)
                    
            except httpx.HTTPStatusError as e:
                logger.warning(
                    f"HTTP ошибка {e.response.status_code} для {url} "
                    f"(попытка {attempt + 1}/{self.retry_attempts})"
                )
                
                # Если 404 или 403 - не retry (страница не существует)
                if e.response.status_code in [404, 403]:
                    return (False, None)
                    
            except Exception as e:
                logger.warning(
                    f"Ошибка запроса {url}: {e} "
                    f"(попытка {attempt + 1}/{self.retry_attempts})"
                )
            
            # Задержка перед следующей попыткой
            if attempt < self.retry_attempts - 1:
                delay = self.retry_delays[attempt]
                logger.debug(f"Ожидание {delay} сек перед повтором...")
                await asyncio.sleep(delay)
        
        logger.error(f"Все попытки исчерпаны для {url}")
        return (False, None)
    
    async def get_product_offers(self, master_sku: str) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        Получить список предложений (offers) для товара
        
        Returns:
            (success, offers): True/False и список offers или пустой список
        """
        url = Config.KASPI_OFFERS_URL.format(master_sku=master_sku)
        logger.info(f"Запрос offers для SKU={master_sku}")
        
        # Kaspi API требует POST запрос с JSON body
        json_body = {
            "cityId": "750000000",  # Алматы
            "limit": 50,
            "page": 0
        }
        
        success, data = await self._make_request(url, "json", method="POST", json_body=json_body)
        
        if not success or not data:
            logger.error(f"Не удалось получить offers для SKU={master_sku}")
            return (False, [])
        
        offers = data.get("offers", [])
        logger.info(f"Получено {len(offers)} offers для SKU={master_sku}")
        
        # Если есть название товара в корне ответа, сохраняем его
        product_name = data.get("name") or data.get("productName") or data.get("title")
        if product_name and offers:
            # Добавляем название в первый offer для удобства
            offers[0]["productName"] = product_name
        
        return (True, offers)
    
    async def get_merchant_phone(self, merchant_id: str, product_sku: str = "") -> Optional[str]:
        """
        Получить телефон продавца из HTML страницы магазина
        
        Парсит JavaScript объект BACKEND.components.merchant
        Использует увеличенное количество попыток для надежности
        
        Args:
            merchant_id: ID продавца
            product_sku: SKU товара (необязательно, но помогает загрузить страницу)
        
        Returns:
            Телефон или None если не найден
        """
        url = Config.KASPI_MERCHANT_URL.format(
            merchant_id=merchant_id,
            product_sku=product_sku if product_sku else ""
        )
        
        # Для телефонов делаем больше попыток (5 вместо 3)
        original_attempts = self.retry_attempts
        self.retry_attempts = 5
        
        try:
            success, html = await self._make_request(url, "text", method="GET")
            
            if not success or not html:
                logger.debug(f"Страница магазина {merchant_id} недоступна")
                return None
            
            # Regex для поиска телефона
            patterns = [
                r'"phone":\s*"([^"]+)"',
                r'BACKEND\.components\.merchant.*?"phone":\s*"([^"]+)"'
            ]
            
            for pattern in patterns:
                match = re.search(pattern, html, re.DOTALL)
                if match:
                    phone = match.group(1)
                    logger.info(f"✅ Найден телефон для merchant_id={merchant_id}: {phone}")
                    return phone
            
            logger.debug(f"Телефон не найден в HTML для merchant_id={merchant_id}")
            return None
            
        finally:
            # Восстанавливаем оригинальное количество попыток
            self.retry_attempts = original_attempts
    
    @staticmethod
    async def resolve_short_url(short_url: str) -> Optional[str]:
        """
        Развернуть короткую ссылку Kaspi (l.kaspi.kz) в полную
        
        Args:
            short_url: Короткая ссылка типа https://l.kaspi.kz/shp/9H2yCnpPuH6
        
        Returns:
            Полный URL или None при ошибке
        """
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=10) as client:
                response = await client.get(short_url)
                
                # Проверяем редирект
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location')
                    if location:
                        logger.info(f"Короткая ссылка развернута: {short_url} → {location}")
                        return location
                
                logger.warning(f"Не удалось развернуть короткую ссылку: {short_url}")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка разворачивания короткой ссылки {short_url}: {e}")
            return None
    
    @staticmethod
    async def extract_master_sku_async(url: str) -> Optional[str]:
        """
        Извлечь master_sku из URL Kaspi (async версия, поддерживает короткие ссылки)
        
        Поддерживаемые форматы:
        - https://kaspi.kz/shop/p/название-107664472/
        - https://kaspi.kz/shop/p/название-107664472
        - https://kaspi.kz/shop/p/107664472/
        - https://kaspi.kz/shop/p/название-116608250/?c=750000000&sr=16...
        - https://l.kaspi.kz/shp/9H2yCnpPuH6 (короткая ссылка)
        
        Returns:
            master_sku или None
        """
        # Проверяем, является ли это короткой ссылкой
        if 'l.kaspi.kz' in url:
            logger.info(f"Обнаружена короткая ссылка: {url}")
            full_url = await KaspiParser.resolve_short_url(url)
            if full_url:
                url = full_url
            else:
                return None
        
        # Извлекаем SKU из полного URL
        return KaspiParser.extract_master_sku(url)
    
    @staticmethod
    def extract_master_sku(url: str) -> Optional[str]:
        """
        Извлечь master_sku из URL Kaspi
        
        Поддерживаемые форматы:
        - https://kaspi.kz/shop/p/название-107664472/
        - https://kaspi.kz/shop/p/название-107664472
        - https://kaspi.kz/shop/p/107664472/
        - https://kaspi.kz/shop/p/название-116608250/?c=750000000&sr=16...
        
        Returns:
            master_sku или None
        """
        # Ищем последнюю последовательность из 8+ цифр в пути URL (до ? или конца)
        # Разделяем URL на путь и query параметры
        path = url.split('?')[0]
        
        # Ищем все последовательности из 8+ цифр
        matches = re.findall(r'\d{8,}', path)
        
        if matches:
            # Возвращаем последнюю найденную (это обычно SKU)
            return matches[-1]
        
        return None
    
    @staticmethod
    def parse_offer(offer: Dict[str, Any]) -> Dict[str, Any]:
        """
        Извлечь нужные поля из offer
        
        Returns:
            {merchant_id, merchant_name, price}
        """
        return {
            "merchant_id": offer.get("merchantId", ""),
            "merchant_name": offer.get("merchantName", ""),
            "price": float(offer.get("price", 0.0))
        }
