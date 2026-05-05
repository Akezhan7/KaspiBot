"""
Auth & Session Manager для Kaspi Pay кабинета.

Логин через Playwright:
1. Проверка сохранённой сессии (storage_state)
2. Если истекла — открыть страницу логина
3. Ввести номер телефона
4. Запросить SMS-код через Telegram (asyncio.Event)
5. Ввести код → дождаться загрузки кабинета
6. Сохранить storage_state
"""
import asyncio
import logging
import random
from urllib.parse import urlsplit

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from config import Config
from database.ads_data import BrowserSessionsDB
from scraper.browser_manager import BrowserManager

logger = logging.getLogger(__name__)

# URL Kaspi Pay по умолчанию (используется как fallback)
_DEFAULT_LOGIN_URL = "https://kaspi.kz/mc"


class KaspiAuthManager:
    """Менеджер авторизации в Kaspi Pay с поддержкой 2FA через Telegram."""

    def __init__(self, browser_manager: BrowserManager, db_path: str | None = None) -> None:
        self._browser_manager = browser_manager
        self._sessions_db: BrowserSessionsDB | None = BrowserSessionsDB(db_path) if db_path else None
        # Механизм передачи SMS-кода из Telegram handler-а
        self._sms_code: str | None = None
        self._sms_event: asyncio.Event = asyncio.Event()
        # Callback для отправки уведомлений в Telegram (устанавливается при интеграции)
        self._notify_callback = None

    @staticmethod
    def _get_login_url() -> str:
        """Актуальный URL кабинета Kaspi Pay из конфигурации."""
        configured = Config.KASPI_PAY_URL.strip()
        return configured or _DEFAULT_LOGIN_URL

    @classmethod
    def _get_cabinet_url_prefix(cls) -> str:
        """Префикс URL авторизованного кабинета."""
        login_url = cls._get_login_url().rstrip("/")
        parsed = urlsplit(login_url)

        if not parsed.scheme or not parsed.netloc:
            return login_url

        base = f"{parsed.scheme}://{parsed.netloc}"
        path = parsed.path.rstrip("/")

        if not path:
            return base

        # Если указан URL страницы входа — берём родительский путь кабинета
        if path.endswith("/login") or path.endswith("/sign-in"):
            parent = path.rsplit("/", 1)[0]
            return f"{base}{parent}" if parent else base

        return f"{base}{path}"

    @staticmethod
    def _is_authenticated_url(current_url: str, cabinet_prefix: str) -> bool:
        """Проверка, что URL относится к авторизованной зоне кабинета."""
        url = (current_url or "").lower()
        return current_url.startswith(cabinet_prefix) and "/login" not in url and "/sign-in" not in url

    @staticmethod
    def _nav_timeout_ms() -> int:
        """Таймаут навигации Playwright в миллисекундах."""
        seconds = max(10, int(Config.KASPI_PAY_NAV_TIMEOUT_SECONDS))
        return seconds * 1000

    def set_notify_callback(self, callback) -> None:
        """Установить callback для отправки уведомлений в Telegram.

        callback: async (text: str) -> None
        """
        self._notify_callback = callback

    async def _notify(self, text: str) -> None:
        """Отправить уведомление админам через установленный callback."""
        if self._notify_callback:
            try:
                await self._notify_callback(text)
            except Exception as e:
                logger.error("KaspiAuth: ошибка отправки уведомления: %s", e)

    def submit_sms_code(self, code: str) -> None:
        """Вызывается из Telegram /login_kaspi для передачи SMS-кода."""
        self._sms_code = code
        self._sms_event.set()

    async def _wait_for_sms_code(self, timeout_seconds: int) -> str | None:
        """Ожидание ввода SMS-кода через Telegram."""
        self._sms_code = None
        self._sms_event.clear()

        try:
            await asyncio.wait_for(self._sms_event.wait(), timeout=timeout_seconds)
            return self._sms_code
        except asyncio.TimeoutError:
            logger.warning("KaspiAuth: таймаут ожидания SMS-кода (%d сек)", timeout_seconds)
            return None

    async def wait_for_sms_code(self, timeout_seconds: int = 300) -> str:
        """Публичный метод ожидания SMS-кода.

        Raises:
            TimeoutError: если код не был получен за timeout_seconds.
        """
        code = await self._wait_for_sms_code(timeout_seconds)
        if not code:
            raise TimeoutError("SMS code was not received in time")
        return code

    async def is_session_valid(self) -> bool:
        """Проверить, что текущая сессия позволяет зайти в кабинет без логина."""
        context = self._browser_manager.context
        if not context:
            return False

        login_url = self._get_login_url()
        cabinet_prefix = self._get_cabinet_url_prefix()
        nav_timeout_ms = self._nav_timeout_ms()

        page: Page | None = None
        try:
            page = await context.new_page()
            await page.goto(login_url, wait_until="domcontentloaded", timeout=nav_timeout_ms)

            # Ждём 3 секунды, пока страница стабилизируется (SPA-редиректы)
            await page.wait_for_timeout(3000)

            current_url = page.url
            # Если остались на странице кабинета (не перекинуло на логин) — сессия валидна
            if self._is_authenticated_url(current_url, cabinet_prefix):
                logger.info("KaspiAuth: сессия валидна (URL: %s)", current_url[:60])
                if self._sessions_db:
                    active = await self._sessions_db.get_active_session()
                    if active:
                        await self._sessions_db.update_last_used(active["id"])
                return True

            logger.info("KaspiAuth: сессия невалидна (URL: %s)", current_url[:60])
            if self._sessions_db:
                await self._sessions_db.invalidate_all()
            return False

        except PlaywrightTimeoutError:
            logger.warning(
                "KaspiAuth: таймаут при проверке сессии (url=%s, timeout=%dms)",
                login_url,
                nav_timeout_ms,
            )
            return False
        except Exception as e:
            logger.error("KaspiAuth: ошибка проверки сессии: %s", e)
            return False
        finally:
            if page:
                await page.close()

    async def login(self) -> bool:
        """Полный цикл авторизации в Kaspi Pay.

        Возвращает True при успешном входе.
        """
        phone = Config.KASPI_PAY_PHONE.strip()
        login_value = Config.KASPI_PAY_LOGIN.strip()
        password_value = Config.KASPI_PAY_PASSWORD

        has_phone_auth = bool(phone)
        has_password_auth = bool(login_value and password_value)

        if (login_value and not password_value) or (password_value and not login_value):
            logger.error("KaspiAuth: задан только один из параметров KASPI_PAY_LOGIN/KASPI_PAY_PASSWORD")
            await self._notify(
                "Ошибка конфигурации: для входа по паролю нужно задать и KASPI_PAY_LOGIN, и KASPI_PAY_PASSWORD."
            )
            return False

        # Предпочитаем логин/пароль, если он задан; иначе fallback на телефон + SMS.
        auth_mode = "password" if has_password_auth else ("phone" if has_phone_auth else None)

        if auth_mode is None:
            logger.error("KaspiAuth: не задан способ авторизации в конфигурации")
            await self._notify(
                "Ошибка: не задан способ авторизации Kaspi Pay.\n"
                "Укажите либо KASPI_PAY_LOGIN/KASPI_PAY_PASSWORD, либо KASPI_PAY_PHONE в .env"
            )
            return False

        # 1. Проверяем сохранённую сессию
        if auth_mode == "password":
            logger.info("KaspiAuth: запуск авторизации по логину %s****", login_value[:4])
        else:
            logger.info("KaspiAuth: запуск авторизации по телефону %s****", phone[:4])

        context = self._browser_manager.context
        if not context:
            logger.error("KaspiAuth: браузерный контекст не инициализирован")
            return False

        login_url = self._get_login_url()
        cabinet_prefix = self._get_cabinet_url_prefix()
        nav_timeout_ms = self._nav_timeout_ms()

        if await self.is_session_valid():
            logger.info("KaspiAuth: существующая сессия валидна, логин не нужен")
            return True

        # 2. Логин заново
        page: Page | None = None
        try:
            page = await context.new_page()
            await page.goto(login_url, wait_until="domcontentloaded", timeout=nav_timeout_ms)
            await self._random_delay()

            # 3. Ввод первичных учётных данных
            if auth_mode == "password":
                login_input = page.locator(
                    "input[name='login'], "
                    "input[name='username'], "
                    "input[autocomplete='username'], "
                    "input[type='email'], "
                    "input[placeholder*='логин'], "
                    "input[placeholder*='email'], "
                    "input[type='text'], "
                    "input[type='tel']"
                ).first
                await login_input.wait_for(state="visible", timeout=15_000)
                await login_input.click()
                await login_input.fill(login_value)
                await self._random_delay()

                password_input = page.locator(
                    "input[type='password'], "
                    "input[name='password'], "
                    "input[autocomplete='current-password'], "
                    "input[placeholder*='пароль']"
                ).first
                await password_input.wait_for(state="visible", timeout=15_000)
                await password_input.click()
                await password_input.fill(password_value)
                await self._random_delay()
            else:
                phone_input = page.locator(
                    "input[type='tel'], input[name='phone'], input[placeholder*='номер'], input[placeholder*='телефон']"
                ).first
                await phone_input.wait_for(state="visible", timeout=15_000)
                await phone_input.click()
                await phone_input.fill(phone)
                await self._random_delay()

            # Нажать кнопку «Далее» / «Войти» / submit
            submit_btn = page.locator(
                "button[type='submit'], "
                "button:has-text('Далее'), "
                "button:has-text('Войти'), "
                "button:has-text('Продолжить')"
            ).first
            await submit_btn.click()
            await self._random_delay()

            # 4. Обнаружение поля для SMS-кода
            sms_input = page.locator(
                "input[type='tel'][maxlength], "
                "input[name='code'], "
                "input[placeholder*='код'], "
                "input[placeholder*='SMS']"
            ).first

            try:
                await sms_input.wait_for(state="visible", timeout=15_000)
            except PlaywrightTimeoutError:
                # Может уже залогинились без SMS или ошибка
                current_url = page.url
                if self._is_authenticated_url(current_url, cabinet_prefix):
                    logger.info("KaspiAuth: вход без SMS (URL: %s)", current_url[:60])
                    await self._browser_manager.save_state()
                    if self._sessions_db:
                        await self._sessions_db.create_session(str(Config.KASPI_STORAGE_STATE_PATH))
                    await self._notify("Kaspi Pay: успешный вход без SMS-кода")
                    return True

                logger.error("KaspiAuth: не обнаружено поле SMS-кода (URL: %s)", current_url[:60])
                await self._notify(
                    "Kaspi Pay: не удалось завершить логин (поле SMS не найдено и редирект не произошёл).\n"
                    f"Текущий URL: {current_url[:80]}"
                )
                return False

            # 5. Запросить SMS через Telegram
            await self._notify(
                "Kaspi Pay запрашивает SMS-код.\n"
                f"Аккаунт: {(login_value or phone)[:4]}****\n\n"
                "Отправьте код ответом на это сообщение (команда /login_kaspi <код>)"
            )
            logger.info("KaspiAuth: ожидание SMS-кода от админа...")

            try:
                code = await self.wait_for_sms_code(Config.SMS_CODE_TIMEOUT_SECONDS)
            except TimeoutError:
                await self._notify("Kaspi Pay: таймаут ожидания SMS-кода. Авторизация отменена.")
                return False

            # 6. Ввод SMS-кода
            await sms_input.fill(code)
            await self._random_delay()

            # Подтвердить код (кнопка или автосабмит)
            confirm_btn = page.locator(
                "button[type='submit'], "
                "button:has-text('Подтвердить'), "
                "button:has-text('Далее'), "
                "button:has-text('Войти')"
            ).first
            try:
                await confirm_btn.click(timeout=5_000)
            except PlaywrightTimeoutError:
                # Возможно, форма засабмитилась автоматически
                pass

            # 7. Ожидание загрузки кабинета
            try:
                await page.wait_for_url(
                    f"{cabinet_prefix}/**",
                    wait_until="domcontentloaded",
                    timeout=nav_timeout_ms,
                )
            except PlaywrightTimeoutError:
                current_url = page.url
                # Проверим ещё раз — может URL уже правильный
                if self._is_authenticated_url(current_url, cabinet_prefix):
                    pass  # Всё ок
                else:
                    logger.error("KaspiAuth: неудачный вход, URL после кода: %s", current_url[:80])
                    await self._notify(
                        "Kaspi Pay: не удалось войти после ввода SMS.\n"
                        f"URL: {current_url[:80]}\n"
                        "Возможно, неверный код."
                    )
                    return False

            # 8. Сохранение сессии
            await self._browser_manager.save_state()
            if self._sessions_db:
                await self._sessions_db.create_session(str(Config.KASPI_STORAGE_STATE_PATH))
            logger.info("KaspiAuth: успешный вход в Kaspi Pay")
            await self._notify("Kaspi Pay: успешная авторизация! Сессия сохранена.")
            return True

        except PlaywrightTimeoutError as e:
            logger.error("KaspiAuth: таймаут при логине: %s", e)
            await self._notify(f"Kaspi Pay: таймаут при логине — {e}")
            return False
        except Exception as e:
            logger.error("KaspiAuth: непредвиденная ошибка при логине: %s", e, exc_info=True)
            await self._notify(f"Kaspi Pay: ошибка авторизации — {e}")
            return False
        finally:
            if page:
                await page.close()

    async def ensure_authenticated(self) -> bool:
        """Гарантировать наличие авторизованной сессии.

        Если сессия валидна — return True без действий.
        Если нет — запустить полный цикл login().
        """
        if await self.is_session_valid():
            return True
        return await self.login()

    async def _random_delay(self) -> None:
        """Рандомная задержка между действиями для имитации человека."""
        delay = random.uniform(
            Config.SCRAPE_ACTION_DELAY_MIN,
            Config.SCRAPE_ACTION_DELAY_MAX,
        )
        await asyncio.sleep(delay)
