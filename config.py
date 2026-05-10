"""
Конфигурация приложения
Загружает переменные окружения и валидирует настройки
"""
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from typing import List

# Загружаем переменные из .env
load_dotenv()

# Базовая директория проекта
BASE_DIR = Path(__file__).parent

# Часовой пояс Алматы
ALMATY_TZ = ZoneInfo("Asia/Almaty")


def now_kz() -> datetime:
    """Текущее время в часовом поясе Алматы."""
    return datetime.now(ALMATY_TZ)


def now_kz_str() -> str:
    """Текущее время Алматы как строка для SQL."""
    return now_kz().strftime("%Y-%m-%d %H:%M:%S")


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
    GREEN_API_MEDIA_URL: str = os.getenv("GREEN_API_MEDIA_URL", "")
    GREEN_API_INSTANCE_ID: str = os.getenv("GREEN_API_INSTANCE_ID", "")
    GREEN_API_TOKEN: str = os.getenv("GREEN_API_TOKEN", "")
    WHATSAPP_WEBHOOK_PORT: int = int(os.getenv("WHATSAPP_WEBHOOK_PORT", "8443"))
    WHATSAPP_WEBHOOK_HOST: str = os.getenv("WHATSAPP_WEBHOOK_HOST", "0.0.0.0")
    WHATSAPP_WEBHOOK_IP_WHITELIST: str = os.getenv("WHATSAPP_WEBHOOK_IP_WHITELIST", "")

    # === OPENAI (LLM-классификация) ===
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    LLM_CLASSIFICATION_TIMEOUT: float = float(os.getenv("LLM_CLASSIFICATION_TIMEOUT", "5.0"))

    # === ESCALATION (Планировщик эскалации) ===
    ESCALATION_INTERVAL_MINUTES: float = float(os.getenv("ESCALATION_INTERVAL_MINUTES", "30"))
    DIALOG_TIMEOUT_CHECK_HOURS: float = float(os.getenv("DIALOG_TIMEOUT_CHECK_HOURS", "1"))
    WARN1_TIMEOUT_HOURS: float = float(os.getenv("WARN1_TIMEOUT_HOURS", "24"))
    WARN2_TIMEOUT_HOURS: float = float(os.getenv("WARN2_TIMEOUT_HOURS", "24"))
    DIALOG_TIMEOUT_HOURS: float = float(os.getenv("DIALOG_TIMEOUT_HOURS", "24"))
    WORKFLOW_COOLDOWN_DAYS: int = int(os.getenv("WORKFLOW_COOLDOWN_DAYS", "30"))
    DAILY_MESSAGE_LIMIT: int = int(os.getenv("DAILY_MESSAGE_LIMIT", "10"))

    # === PURCHASE DOCUMENTS ===
    PURCHASE_DOCUMENTS_DIR: Path = BASE_DIR / "data" / "legal"

    # === WARN DOCUMENTS (файлы-вложения к WARN1/WARN2) ===
    DOCUMENTS_DIR: Path = BASE_DIR / "data" / "documents"
    WARN1_DOCUMENTS: List[Path] = [
        BASE_DIR / "data" / "documents" / "copyright_certificate_1.jpg",
        BASE_DIR / "data" / "documents" / "copyright_certificate_2.jpeg",
    ]
    WARN2_DOCUMENTS: List[Path] = [
        BASE_DIR / "data" / "documents" / "court_decision.pdf",
    ]

    # === KASPI PAY (Scraper / Marketing) ===
    KASPI_PAY_PHONE: str = os.getenv("KASPI_PAY_PHONE", "")
    KASPI_PAY_LOGIN: str = os.getenv("KASPI_PAY_LOGIN", "")
    KASPI_PAY_PASSWORD: str = os.getenv("KASPI_PAY_PASSWORD", "")
    KASPI_PAY_URL: str = os.getenv("KASPI_PAY_URL", "https://kaspi.kz/mc")
    KASPI_MARKETING_ADS_URL: str = os.getenv(
        "KASPI_MARKETING_ADS_URL",
        "https://marketing.kaspi.kz/advertising/overview?activeTab=Enabled",
    )
    KASPI_BONUSES_REVIEWS_URL: str = os.getenv(
        "KASPI_BONUSES_REVIEWS_URL",
        "https://marketing.kaspi.kz/bonuses/reviews/promotions/list?state=Enabled",
    )
    KASPI_BONUSES_PRODUCTS_URL: str = os.getenv(
        "KASPI_BONUSES_PRODUCTS_URL",
        "https://marketing.kaspi.kz/bonuses/products/promotions/list?state=Enabled",
    )
    KASPI_STORAGE_STATE_PATH: Path = BASE_DIR / "data" / "kaspi_auth_state.json"
    PLAYWRIGHT_HEADLESS: bool = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
    SCRAPE_SCHEDULE_HOUR: int = int(os.getenv("SCRAPE_SCHEDULE_HOUR", "3"))
    SCRAPE_SCHEDULE_MINUTE: int = int(os.getenv("SCRAPE_SCHEDULE_MINUTE", "0"))
    SCRAPE_ACTION_DELAY_MIN: float = float(os.getenv("SCRAPE_ACTION_DELAY_MIN", "1.0"))
    SCRAPE_ACTION_DELAY_MAX: float = float(os.getenv("SCRAPE_ACTION_DELAY_MAX", "3.0"))
    SMS_CODE_TIMEOUT_SECONDS: int = int(os.getenv("SMS_CODE_TIMEOUT_SECONDS", "300"))
    KASPI_PAY_NAV_TIMEOUT_SECONDS: int = int(os.getenv("KASPI_PAY_NAV_TIMEOUT_SECONDS", "60"))
    KASPI_MARKETING_REPORT_DAYS: int = int(os.getenv("KASPI_MARKETING_REPORT_DAYS", "7"))
    # Список периодов отчётов для двойной выгрузки (CSV из env, дефолт "7,30").
    # Скрапер пройдёт по списку и для каждого периода скачает свой XLSX,
    # сохранив в БД с соответствующим period_days.
    KASPI_MARKETING_REPORT_PERIODS: List[int] = sorted({
        int(p.strip())
        for p in os.getenv("KASPI_MARKETING_REPORT_PERIODS", "7,30").split(",")
        if p.strip().isdigit() and int(p.strip()) > 0
    }) or [7]

    # === TMA API (REST API для Telegram Mini App) ===
    TMA_API_HOST: str = os.getenv("TMA_API_HOST", "0.0.0.0")
    TMA_API_PORT: int = int(os.getenv("TMA_API_PORT", "8080"))
    TMA_CORS_ORIGINS: List[str] = [
        o.strip()
        for o in os.getenv("TMA_CORS_ORIGINS", "https://web.telegram.org,https://t.me").split(",")
        if o.strip()
    ]
    TMA_URL: str = os.getenv("TMA_URL", "")  # публичный URL TMA (для inline-кнопки /analytics)
    TMA_DIST_PATH: Path = BASE_DIR / "tma" / "dist"  # путь к собранному фронтенду

    # === EXCLUDED SELLERS ===
    # Магазины, которые нужно исключить из уведомлений (например, собственный магазин)
    # Сравнение регистронезависимое — хранить в нижнем регистре
    EXCLUDED_SELLER_NAMES: List[str] = ["pks ltd", "pks market"]

    @classmethod
    def has_kaspi_phone_auth(cls) -> bool:
        """Настроена авторизация Kaspi Pay через телефон (+ SMS)."""
        return bool(cls.KASPI_PAY_PHONE.strip())

    @classmethod
    def has_kaspi_password_auth(cls) -> bool:
        """Настроена авторизация Kaspi Pay через логин/пароль."""
        return bool(cls.KASPI_PAY_LOGIN.strip() and cls.KASPI_PAY_PASSWORD.strip())

    @classmethod
    def is_kaspi_pay_enabled(cls) -> bool:
        """Есть минимум один валидный способ авторизации в Kaspi Pay."""
        return cls.has_kaspi_password_auth() or cls.has_kaspi_phone_auth()

    @classmethod
    def get_kaspi_pay_proxy_url(cls) -> str:
        """Proxy URL для Kaspi Pay.

        Если KASPI_PAY_PROXY_URL не задан, используется общий PROXY_URL.
        Если KASPI_PAY_PROXY_URL задан пустым, прокси для Kaspi Pay отключается.
        """
        override = os.getenv("KASPI_PAY_PROXY_URL")
        if override is None:
            return cls.PROXY_URL.strip()
        return override.strip()
    
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
        cls.DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)


# Валидация при импорте
Config.validate()
Config.ensure_dirs()
