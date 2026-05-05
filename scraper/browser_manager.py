"""
Управление жизненным циклом Playwright-браузера.
Запуск Chromium, создание контекста с/без storage_state, сохранение сессии.
"""
import logging
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright

from config import Config

logger = logging.getLogger(__name__)

# User-Agent реального Chrome для обхода примитивных проверок
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class BrowserManager:
    """Управление Playwright browser lifecycle."""

    def __init__(
        self,
        storage_state_path: Path | None = None,
        proxy_url: str | None = None,
        headless: bool = True,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> None:
        self._storage_state_path = storage_state_path
        self._proxy_url = proxy_url
        self._headless = headless
        self._user_agent = user_agent
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    @property
    def context(self) -> BrowserContext | None:
        return self._context

    async def launch(self) -> BrowserContext:
        """Запустить браузер и создать контекст.

        Если storage_state_path указан и файл существует — загружает
        сохранённую сессию (cookies + localStorage).
        """
        self._playwright = await async_playwright().start()

        launch_kwargs: dict = {
            "headless": self._headless,
        }
        if self._proxy_url:
            launch_kwargs["proxy"] = {"server": self._proxy_url}

        self._browser = await self._playwright.chromium.launch(**launch_kwargs)

        context_kwargs: dict = {
            "user_agent": self._user_agent,
            "viewport": {"width": 1280, "height": 720},
            "locale": "ru-RU",
            "timezone_id": "Asia/Almaty",
            "accept_downloads": True,
        }

        # Загрузка сохранённого состояния, если файл существует
        if self._storage_state_path and self._storage_state_path.exists():
            context_kwargs["storage_state"] = str(self._storage_state_path)
            logger.info("BrowserManager: загружен storage_state из %s", self._storage_state_path.name)

        self._context = await self._browser.new_context(**context_kwargs)

        logger.info(
            "BrowserManager: браузер запущен (headless=%s, proxy=%s)",
            self._headless,
            bool(self._proxy_url),
        )
        return self._context

    async def save_state(self) -> None:
        """Сохранить текущий storage_state (cookies + localStorage) в файл."""
        if not self._context:
            logger.warning("BrowserManager.save_state: контекст не инициализирован")
            return
        if not self._storage_state_path:
            logger.warning("BrowserManager.save_state: storage_state_path не задан")
            return

        self._storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        await self._context.storage_state(path=str(self._storage_state_path))
        logger.info("BrowserManager: storage_state сохранён в %s", self._storage_state_path.name)

    async def close(self) -> None:
        """Корректное закрытие контекста и браузера."""
        if self._context:
            try:
                await self._context.close()
            except Exception as e:
                logger.debug("BrowserManager: ошибка закрытия контекста: %s", e)
            self._context = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.debug("BrowserManager: ошибка закрытия браузера: %s", e)
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.debug("BrowserManager: ошибка остановки playwright: %s", e)
            self._playwright = None

        logger.info("BrowserManager: браузер закрыт")
