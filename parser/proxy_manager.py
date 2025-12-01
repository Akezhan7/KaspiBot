"""
Менеджер прокси - управление MobileProxy.Space
Ротация IP, счетчик запросов, задержки
"""
import httpx
import asyncio
import random
import logging
from typing import Optional
from config import Config

logger = logging.getLogger(__name__)


class ProxyManager:
    """Управление прокси и ротацией IP"""
    
    def __init__(self):
        self.proxy_url = Config.PROXY_URL
        self.change_api_url = Config.PROXY_CHANGE_API
        self.batch_size = Config.BATCH_SIZE
        
        # Счетчик запросов (сбрасывается после смены IP)
        self.request_counter = 0
        
        # Лимиты задержек
        self.product_delay = (Config.PRODUCT_DELAY_MIN, Config.PRODUCT_DELAY_MAX)
        self.merchant_delay = (Config.MERCHANT_DELAY_MIN, Config.MERCHANT_DELAY_MAX)
        self.ip_change_delay = (Config.IP_CHANGE_DELAY_MIN, Config.IP_CHANGE_DELAY_MAX)
    
    def get_proxy_url(self) -> Optional[str]:
        """Получить URL прокси для httpx (строка)"""
        return self.proxy_url if self.proxy_url else None
    
    async def change_ip(self) -> bool:
        """
        Сменить IP через API MobileProxy.Space
        
        Returns:
            True если успешно, False при ошибке
        """
        # Если API не настроен - пропускаем
        if not self.change_api_url:
            logger.debug("Смена IP пропущена (API не настроен)")
            self.request_counter = 0
            return True
        
        try:
            logger.info("Смена IP адреса...")
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self.change_api_url)
                response.raise_for_status()
            
            # Сброс счетчика
            self.request_counter = 0
            
            # Задержка после смены
            delay = random.uniform(*self.ip_change_delay)
            logger.info(f"IP изменен. Ожидание {delay:.1f} сек...")
            await asyncio.sleep(delay)
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка смены IP: {e}")
            return False
    
    async def should_change_ip(self) -> bool:
        """Проверить нужна ли смена IP"""
        return self.request_counter >= self.batch_size
    
    async def increment_and_check_ip(self) -> None:
        """
        Увеличить счетчик и при необходимости сменить IP
        Вызывать после каждого запроса к Kaspi
        """
        self.request_counter += 1
        logger.debug(f"Запросов выполнено: {self.request_counter}/{self.batch_size}")
        
        if await self.should_change_ip():
            await self.change_ip()
    
    async def delay_for_product(self) -> None:
        """Задержка между запросами товаров"""
        delay = random.uniform(*self.product_delay)
        logger.debug(f"Задержка (товар): {delay:.2f} сек")
        await asyncio.sleep(delay)
    
    async def delay_for_merchant(self) -> None:
        """Задержка для запроса страницы магазина"""
        delay = random.uniform(*self.merchant_delay)
        logger.debug(f"Задержка (магазин): {delay:.2f} сек")
        await asyncio.sleep(delay)
    
    def reset_counter(self) -> None:
        """Сброс счетчика запросов (например, при начале нового цикла)"""
        self.request_counter = 0
        logger.debug("Счетчик запросов сброшен")
