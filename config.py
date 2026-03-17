"""
Конфигурация приложения
Загружает переменные окружения и валидирует настройки
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from typing import List

# Загружаем переменные из .env
load_dotenv()

# Базовая директория проекта
BASE_DIR = Path(__file__).parent


class Config:
    """Класс конфигурации приложения"""
    
    # === TELEGRAM ===
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    ADMIN_USER_IDS: List[int] = [
        int(uid.strip()) 
        for uid in os.getenv("ADMIN_USER_IDS", "").split(",") 
        if uid.strip()
    ]
    
    # === PROXY ===
    PROXY_URL: str = os.getenv("PROXY_URL", "")
    PROXY_CHANGE_API: str = os.getenv("PROXY_CHANGE_API", "")
    
    # === SCANNER ===
    SCAN_INTERVAL_HOURS: float = float(os.getenv("SCAN_INTERVAL_HOURS", "12"))
    BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "50"))
    
    # === RATE LIMITS ===
    PRODUCT_DELAY_MIN: int = int(os.getenv("PRODUCT_DELAY_MIN", "2"))
    PRODUCT_DELAY_MAX: int = int(os.getenv("PRODUCT_DELAY_MAX", "5"))
    MERCHANT_DELAY_MIN: int = int(os.getenv("MERCHANT_DELAY_MIN", "3"))
    MERCHANT_DELAY_MAX: int = int(os.getenv("MERCHANT_DELAY_MAX", "6"))
    IP_CHANGE_DELAY_MIN: int = int(os.getenv("IP_CHANGE_DELAY_MIN", "30"))
    IP_CHANGE_DELAY_MAX: int = int(os.getenv("IP_CHANGE_DELAY_MAX", "60"))
    
    # === DATABASE ===
    DB_PATH: Path = BASE_DIR / os.getenv("DB_PATH", "data/kaspi_monitor.db")
    
    # === KASPI API ===
    KASPI_OFFERS_URL = "https://kaspi.kz/yml/offer-view/offers/{master_sku}"
    KASPI_PRODUCT_URL = "https://kaspi.kz/shop/api/v2/products/{master_sku}"
    KASPI_MERCHANT_URL = "https://kaspi.kz/shop/info/merchant/{merchant_id}/review/?productCode={product_sku}"
    KASPI_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/json",
        "Referer": "https://kaspi.kz/shop/",
        "Origin": "https://kaspi.kz",
    }
    
    # === RETRY SETTINGS ===
    REQUEST_TIMEOUT: int = 30
    RETRY_ATTEMPTS: int = 3
    RETRY_DELAYS: List[int] = [3, 8, 20]  # секунды между попытками (увеличено для стабильности)
    
    # === GREEN API (WhatsApp) ===
    GREEN_API_URL: str = os.getenv("GREEN_API_URL", "https://api.green-api.com")
    GREEN_API_INSTANCE_ID: str = os.getenv("GREEN_API_INSTANCE_ID", "")
    GREEN_API_TOKEN: str = os.getenv("GREEN_API_TOKEN", "")
    WHATSAPP_WEBHOOK_PORT: int = int(os.getenv("WHATSAPP_WEBHOOK_PORT", "8443"))
    WHATSAPP_WEBHOOK_HOST: str = os.getenv("WHATSAPP_WEBHOOK_HOST", "0.0.0.0")

    # === OPENAI (LLM-классификация) ===
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    LLM_CLASSIFICATION_TIMEOUT: float = float(os.getenv("LLM_CLASSIFICATION_TIMEOUT", "5.0"))

    # === ESCALATION (Планировщик эскалации) ===
    ESCALATION_INTERVAL_MINUTES: int = int(os.getenv("ESCALATION_INTERVAL_MINUTES", "30"))
    DIALOG_TIMEOUT_CHECK_HOURS: int = int(os.getenv("DIALOG_TIMEOUT_CHECK_HOURS", "1"))
    WARN1_TIMEOUT_HOURS: int = int(os.getenv("WARN1_TIMEOUT_HOURS", "24"))
    WARN2_TIMEOUT_HOURS: int = int(os.getenv("WARN2_TIMEOUT_HOURS", "24"))
    DIALOG_TIMEOUT_HOURS: int = int(os.getenv("DIALOG_TIMEOUT_HOURS", "24"))

    # === PURCHASE DOCUMENTS ===
    PURCHASE_DOCUMENTS_DIR: Path = BASE_DIR / "data" / "legal"

    # === EXCLUDED SELLERS ===
    # Магазины, которые нужно исключить из уведомлений (например, собственный магазин)
    EXCLUDED_SELLER_NAMES: List[str] = ["PKS Ltd"]
    
    @classmethod
    def validate(cls) -> None:
        """Проверка обязательных параметров"""
        errors = []
        warnings = []
        
        if not cls.TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN не установлен")
        
        if not cls.ADMIN_USER_IDS:
            errors.append("ADMIN_USER_IDS не установлен")
        
        if not cls.PROXY_URL:
            warnings.append("PROXY_URL не установлен - работа без прокси (не рекомендуется)")
        
        if not cls.PROXY_CHANGE_API:
            warnings.append("PROXY_CHANGE_API не установлен - ротация IP отключена")
        
        if not cls.GREEN_API_INSTANCE_ID or not cls.GREEN_API_TOKEN:
            warnings.append("GREEN_API_INSTANCE_ID/GREEN_API_TOKEN не установлены - WhatsApp отключен")
        
        if not cls.OPENAI_API_KEY:
            warnings.append("OPENAI_API_KEY не установлен - LLM-классификация отключена")
        
        if errors:
            raise ValueError(
                "Ошибки конфигурации:\n" + "\n".join(f"- {err}" for err in errors)
            )
        
        if warnings:
            import logging
            logger = logging.getLogger(__name__)
            for warning in warnings:
                logger.warning(warning)
    
    @classmethod
    def ensure_dirs(cls) -> None:
        """Создание необходимых директорий"""
        cls.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        cls.PURCHASE_DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)


# Валидация при импорте
Config.validate()
Config.ensure_dirs()
