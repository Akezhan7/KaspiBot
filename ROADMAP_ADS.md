# ROADMAP: Kaspi Ads Intelligence & TMA Dashboard

> Поэтапный план доработки KaspiBot.
> Каждая фаза — самодостаточный инкремент, который можно тестировать отдельно.
> Новый функционал НЕ ломает существующую систему (фазы 1–8 из ROADMAP.md).

---

## Архитектурное решение

### Новые модули (не трогаем старые без необходимости)

```
KaspiBot/
├── scraper/                      # НОВЫЙ пакет — Playwright-скрапер Kaspi Pay
│   ├── __init__.py
│   ├── auth.py                   # Auth & Session Manager (логин, 2FA, сохранение сессии)
│   ├── marketing.py              # Marketing Scraper (Kaspi Marketing, Бонусы)
│   ├── browser_manager.py        # Управление Playwright browser lifecycle
│   └── models.py                 # Dataclass-модели скрапера (AdCampaign, BonusInfo и пр.)
│
├── analytics/                    # НОВЫЙ пакет — Data Processor (расчёт ROI/ROAS)
│   ├── __init__.py
│   ├── processor.py              # Основные расчёты (ROI, ROAS, CPC efficiency)
│   └── aggregator.py             # Агрегация по периодам, группировка по SKU/категориям
│
├── api/                          # НОВЫЙ пакет — REST API для TMA
│   ├── __init__.py
│   ├── server.py                 # aiohttp API-сервер (эндпоинты для фронтенда)
│   ├── auth_middleware.py        # Валидация Telegram WebApp initData (HMAC-SHA256)
│   └── routes.py                 # Маршруты: /api/dashboard, /api/products, /api/ads, ...
│
├── tma/                          # НОВЫЙ — React фронтенд (Telegram Mini App)
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   ├── api/                  # API-клиент (fetch к /api/*)
│   │   ├── components/           # UI-компоненты дашборда
│   │   ├── pages/                # Страницы: Dashboard, Products, Ads, Bonuses
│   │   └── hooks/                # React hooks (useTelegram, useApi, useTheme)
│   └── public/
│       └── index.html
│
├── database/
│   ├── ads_data.py               # НОВЫЙ DAO: таблицы рекламных данных
│   └── ...                       # Существующие DAO — без изменений
│
├── config.py                     # Расширяем: +Kaspi Pay настройки, +TMA настройки
├── main.py                       # Расширяем: +API сервер, +scraping job
└── requirements.txt              # Расширяем: +playwright, +pytest-playwright
```

### Связь новых модулей с существующими

```
┌─────────────────────────────────────────────────────────────────────┐
│  Telegram Mini App (React)                                         │
│  tma/ → https://your-domain/tma                                    │
│  Открывается через кнопку в боте или Menu Button                   │
└────────────────────┬────────────────────────────────────────────────┘
                     │ HTTPS (initData + API calls)
                     ▼
┌─────────────────────────────────────────────────────────────────────┐
│  API Server (aiohttp)      api/server.py                           │
│  :8080/api/*                                                        │
│  Работает параллельно с WhatsApp webhook (:8443)                   │
│  Валидация: HMAC-SHA256 от bot_token + initData                    │
└────────┬──────────────────────────┬─────────────────────────────────┘
         │                          │
         ▼                          ▼
┌────────────────────┐   ┌─────────────────────────┐
│  analytics/        │   │  database/               │
│  processor.py      │   │  ads_data.py (НОВЫЙ)     │
│  aggregator.py     │   │  products.py (Существ.)  │
│  ROI, ROAS, CPC    │   │  product_sellers.py      │
└────────┬───────────┘   └─────────────────────────┘
         │                          ▲
         │                          │
         ▼                          │
┌─────────────────────────────────────────────────────────────────────┐
│  scraper/                                                           │
│  Playwright browser → kaspi.kz/pay/merchantcabinet                 │
│  auth.py: логин + 2FA через Telegram + storage_state               │
│  marketing.py: парсинг таблиц маркетинга, бонусов                  │
│  Запуск: APScheduler job (1 раз/сутки, ночью) или по команде       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Схема БД (новые таблицы)

```sql
-- Рекламные кампании / данные по SKU из Kaspi Marketing
CREATE TABLE IF NOT EXISTS ads_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_sku TEXT NOT NULL,           -- master_sku из products
    scraped_at TIMESTAMP NOT NULL,       -- когда собрано
    period_start DATE,                   -- начало периода данных
    period_end DATE,                     -- конец периода данных
    source TEXT NOT NULL DEFAULT 'kaspi_marketing',  -- kaspi_marketing | kaspi_bonus
    impressions INTEGER DEFAULT 0,       -- охваты
    clicks INTEGER DEFAULT 0,           -- клики
    ctr REAL DEFAULT 0,                 -- CTR (%)
    spend REAL DEFAULT 0,               -- затраты (тенге)
    cpc REAL DEFAULT 0,                 -- стоимость клика
    orders INTEGER DEFAULT 0,           -- заказы (если есть)
    revenue REAL DEFAULT 0,             -- выручка (если есть)
    bonus_active INTEGER DEFAULT 0,     -- бонус активен (1/0)
    bonus_percent REAL DEFAULT 0,       -- процент бонуса
    raw_data TEXT,                       -- JSON с полным набором данных
    FOREIGN KEY (product_sku) REFERENCES products(master_sku)
);
CREATE INDEX IF NOT EXISTS idx_ads_data_sku ON ads_data(product_sku);
CREATE INDEX IF NOT EXISTS idx_ads_data_scraped ON ads_data(scraped_at);
CREATE INDEX IF NOT EXISTS idx_ads_data_source ON ads_data(source);

-- Логи скрапинга кабинета
CREATE TABLE IF NOT EXISTS scrape_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP,
    products_scraped INTEGER DEFAULT 0,
    errors TEXT,
    status TEXT DEFAULT 'running'        -- running | completed | failed
);

-- Состояние сессии браузера (метаданные, сам файл хранится на диске)
CREATE TABLE IF NOT EXISTS browser_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP,
    storage_state_path TEXT NOT NULL,     -- путь к state.json
    is_valid INTEGER DEFAULT 1,
    last_used_at TIMESTAMP
);
```

---

## Фаза 10: Auth & Session Manager (scraper/auth.py)

**Цель:** Модуль для входа в Kaspi Pay кабинет через Playwright с сохранением сессии.

### Подзадачи

<!-- [x] --> 10.1. **Настройка Playwright**
- Добавить `playwright` в requirements.txt
- Команда: `playwright install chromium`
- Создать `scraper/__init__.py`, `scraper/browser_manager.py`
- Класс `BrowserManager`:
  - `__init__(self, storage_state_path: Path, proxy_url: str | None)`
  - `async launch() -> BrowserContext` — запуск с `persistent_context` или `storage_state`
  - `async save_state() -> None` — сохранение `storage_state` в JSON
  - `async close() -> None` — корректное закрытие
  - Использовать `headless=True` по умолчанию (переключаемо через Config)
  - Прокси: через `Config.PROXY_URL` (прокси уже есть в проекте)

<!-- [x] --> 10.2. **Логин с 2FA через Telegram**
- Класс `KaspiAuthManager`:
  - `__init__(self, browser_manager, notification_service, db_path)`
  - `async login() -> bool` — полный цикл логина
  - `async is_session_valid() -> bool` — проверка текущей сессии (открыть главную, проверить не редирект на логин)
  - `async wait_for_sms_code(timeout_seconds=300) -> str` — ожидание ввода кода через Telegram
- Алгоритм:
  1. Проверить `storage_state.json` — если есть, загрузить и попробовать зайти
  2. Если сессия валидна → return True
  3. Если нет → открыть страницу логина
  4. Ввести номер телефона (из Config)
  5. Обнаружить поле 2FA → отправить уведомление в Telegram: «Введите код из СМС»
  6. Ждать ответа от админа (FSM state в aiogram)
  7. Ввести код → дождаться загрузки кабинета
  8. Сохранить `storage_state.json`
  9. Записать в `browser_sessions`
- Использовать `wait_for_selector` с таймаутом (кнопки/поля ввода)
- Обработка случаев: неверный код, таймаут ожидания, блокировка

<!-- [x] --> 10.3. **Telegram-команда `/login_kaspi`**
- FSM-стейт `waiting_sms_code`
- Admin-only команда
- При получении кода — передать его в `KaspiAuthManager`
- Результат: «Успешно» / «Ошибка»

<!-- [x] --> 10.4. **Config: новые настройки**
- `KASPI_PAY_PHONE` — номер телефона для логина
- `KASPI_PAY_URL` — URL кабинета (https://kaspi.kz/pay/merchantcabinet)
- `KASPI_STORAGE_STATE_PATH` — путь к `data/kaspi_auth_state.json`
- `PLAYWRIGHT_HEADLESS` — True/False
- `SCRAPE_SCHEDULE_HOUR` — час запуска (по умолчанию 3, ночью)

- [x] **Фаза 10 завершена**

### Критерий завершения
- [x] `KaspiAuthManager` логинится в кабинет с вводом SMS через Telegram
- [x] `storage_state.json` сохраняется и переиспользуется при следующем запуске
- [x] При смене IP/истечении сессии — автоматически запрашивает повторный логин

---

## Фаза 11: Marketing Scraper (scraper/marketing.py)

**Цель:** Сбор данных из разделов «Kaspi Marketing» и «Бонусы» в кабинете.

### Подзадачи

<!-- [x] --> 11.1. **Модели данных (scraper/models.py)**
```python
@dataclass
class AdCampaignData:
    product_sku: str
    product_name: str
    impressions: int
    clicks: int
    ctr: float
    spend: float          # затраты в тенге
    cpc: float            # стоимость клика
    period_start: date
    period_end: date
    source: str           # 'kaspi_marketing'

@dataclass
class BonusData:
    product_sku: str
    product_name: str
    bonus_active: bool
    bonus_percent: float
    source: str           # 'kaspi_bonus'

@dataclass
class ScrapeResult:
    campaigns: list[AdCampaignData]
    bonuses: list[BonusData]
    errors: list[str]
    scraped_at: datetime
```

<!-- [x] --> 11.2. **Kaspi Marketing Scraper**
- Класс `MarketingScraper`:
  - `__init__(self, browser_context, db_path)`
  - `async scrape_marketing() -> list[AdCampaignData]` — данные из раздела «Kaspi Marketing»
  - `async scrape_bonuses() -> list[BonusData]` — данные из раздела «Бонусы»
  - `async scrape_all() -> ScrapeResult` — полный сбор
- Навигация:
  - Перейти в раздел «Маркетинг» → «Kaspi Marketing»
  - Дождаться загрузки таблицы (`wait_for_selector`)
  - Собрать данные из таблицы маркетинга
- **Бесконечный скролл:** прокачивать до конца, пока появляются новые строки
  ```python
  prev_count = 0
  while True:
      await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
      await page.wait_for_timeout(1500)
      rows = await page.query_selector_all("table tbody tr")  # или аналогичный селектор
      if len(rows) == prev_count:
          break
      prev_count = len(rows)
  ```
- Парсинг строк: извлечь SKU, название, охваты, клики, CTR, затраты, CPC
- **Важно:** селекторы искать по тексту/aria-label, не по автогенерированным ID

<!-- [x] --> 11.3. **DAO для рекламных данных (database/ads_data.py)**
- Класс `AdsDataDB`:
  - `__init__(self, db_path)`
  - `async save_campaign(data: AdCampaignData) -> int`
  - `async save_campaigns_batch(data: list[AdCampaignData]) -> int`
  - `async save_bonus(data: BonusData) -> int`
  - `async get_latest_by_sku(sku: str) -> dict | None`
  - `async get_campaigns_for_period(start: date, end: date, sku: str = None) -> list[dict]`
  - `async get_bonuses_status() -> list[dict]` — актуальный список бонусов
  - `async get_top_spenders(limit: int = 20) -> list[dict]`
  - `async get_products_without_bonuses() -> list[dict]`
  - `async get_most_clickable(limit: int = 20) -> list[dict]`

<!-- [x] --> 11.4. **Миграция БД**
- Добавить в `database/migrations.py`
  - Миграция v4: CREATE TABLE ads_data, scrape_logs, browser_sessions

<!-- [x] --> 11.5. **Планировщик (APScheduler job)**
- Добавить в `main.py`:
  - Job `scheduled_scrape` — 1 раз в сутки (по `Config.SCRAPE_SCHEDULE_HOUR`, по умолчанию 03:00 ночью)
  - Использует `KaspiAuthManager.is_session_valid()` → если нет, уведомить админа
  - При успешном сборе → сохранить в `ads_data` + `scrape_logs`
  - При ошибке → уведомление в Telegram

<!-- [x] --> 11.6. **Telegram-команда `/scrape`**
- Admin-only: запустить сбор данных вручную
- Показать результат: сколько SKU обработано, ошибки

### Критерий завершения
- [x] Скрапер заходит в кабинет, собирает данные маркетинга по всем товарам
- [x] Данные сохраняются в SQLite
- [x] Ежедневный автосбор ночью
- [x] Бесконечный скролл обрабатывает 600+ товаров
- [x] Telegram-уведомление при ошибке или необходимости повторного логина

- [x] **Фаза 11 завершена**

---

## Фаза 12: Data Processor (analytics/)

**Цель:** Расчёт аналитических показателей на основе данных маркетинга + продаж.

### Подзадачи

<!-- [x] --> 12.1. **Модуль расчётов (analytics/processor.py)**
- Класс `AdsAnalyticsProcessor`:
  - `__init__(self, ads_db, products_db, product_sellers_db)`
  - `async calculate_roi(sku: str, period_days: int = 30) -> dict`
    - ROI = (Revenue - Ad Spend) / Ad Spend * 100
  - `async calculate_roas(sku: str, period_days: int = 30) -> float`
    - ROAS = Revenue / Ad Spend
  - `async get_cpc_efficiency(sku: str) -> dict`
    - CPC vs средний чек
  - `async get_wasted_budget(threshold_roi: float = 0) -> list[dict]`
    - Товары с отрицательным ROI (сливают бюджет)
  - `async get_top_performers(limit: int = 20) -> list[dict]`
    - Товары с лучшим ROAS
  - `async get_no_bonus_products() -> list[dict]`
    - Товары без бонусов
  - `async get_most_clickable(limit: int = 20) -> list[dict]`
    - Лучший CTR

<!-- [x] --> 12.2. **Агрегатор (analytics/aggregator.py)**
- Класс `DataAggregator`:
  - `async aggregate_daily(date: date) -> dict` — сводка за день
  - `async aggregate_weekly() -> dict` — сводка за неделю
  - `async aggregate_monthly() -> dict` — сводка за месяц
  - `async get_trends(sku: str, days: int = 30) -> list[dict]` — тренд show/clicks/spend по дням
  - `async get_total_stats() -> dict` — общие метрики:
    - Всего потрачено, средний CPC, средний CTR, товаров с рекламой, товаров без бонусов

### Критерий завершения
- [x] ROI/ROAS рассчитываются корректно
- [x] «Топ сливного бюджета» возвращает товары с ROI < 0
- [x] Тренды показывают динамику за выбранный период

- [x] **Фаза 12 завершена**

---

## Фаза 13: REST API для TMA (api/)

**Цель:** Backend API (aiohttp) для обслуживания Telegram Mini App.

### Подзадачи

<!-- [x] --> 13.1. **API-сервер (api/server.py)**
- Класс `TMAApiServer`:
  - `__init__(self, analytics, ads_db, products_db, bot_token, host, port)`
  - `async start() -> None` — запуск aiohttp сервера
  - `async stop() -> None` — остановка
- Запуск параллельно с WhatsApp webhook (другой порт: 8080)
- CORS: разрешить `https://web.telegram.org` и dev-режим (localhost)

<!-- [x] --> 13.2. **Auth middleware (api/auth_middleware.py)**
- Валидация `initData` от Telegram WebApp
- Алгоритм HMAC-SHA256:
  ```python
  secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
  # Проверка hash из initData
  ```
- Использовать `aiogram.utils.web_app.safe_parse_webapp_init_data()`
- Возвращать `401 Unauthorized` при невалидных данных
- Проверять `auth_date` — отвергать данные старше 1 часа
- Проверять `user.id in ADMIN_USER_IDS` — только админы

<!-- [x] --> 13.3. **API-эндпоинты (api/routes.py)**

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/dashboard` | Главная сводка: total spend, avg CPC, CTR, ROI, кол-во товаров |
| GET | `/api/products` | Список товаров с рекламными метриками (пагинация, сортировка) |
| GET | `/api/products/{sku}` | Детальная карточка товара: тренды, ROI, история |
| GET | `/api/ads/top-spenders` | Топ товаров по затратам (потенциальный слив) |
| GET | `/api/ads/top-performers` | Топ по ROAS |
| GET | `/api/ads/no-bonus` | Товары без бонусов |
| GET | `/api/ads/most-clickable` | Лучший CTR |
| GET | `/api/ads/wasted-budget` | ROI < 0 |
| GET | `/api/ads/trends/{sku}` | Тренды по конкретному товару |
| GET | `/api/summary/daily` | Сводка за сегодня |
| GET | `/api/summary/weekly` | Сводка за неделю |
| GET | `/api/summary/monthly` | Сводка за месяц |
| POST | `/api/scrape/trigger` | Запуск ручного скрапинга |
| GET | `/api/scrape/status` | Статус последнего скрапинга |

- Все эндпоинты возвращают JSON
- Query params: `?sort=spend_desc&limit=20&offset=0&period=30d`

<!-- [x] --> 13.4. **Config: TMA настройки**
- `TMA_API_HOST` — хост (0.0.0.0)
- `TMA_API_PORT` — порт (8080)
- `TMA_CORS_ORIGINS` — разрешённые origins

<!-- [x] --> 13.5. **Интеграция в main.py**
- Запуск API-сервера параллельно с Telegram bot и WhatsApp webhook
- Graceful shutdown

- [x] **Фаза 13 завершена**

### Критерий завершения: Telegram Mini App — Frontend (tma/)

**Цель:** React-приложение внутри Telegram, визуализация данных из API.

### Технологический стек фронтенда
- **React 18** + **TypeScript**
- **Vite** — сборка и dev-сервер
- **@telegram-apps/sdk-react** — официальный SDK для TMA
- **Recharts** или **Chart.js** — графики трендов
- **CSS**: Telegram theme variables (`var(--tg-theme-bg-color)`, и т.д.)
- Без тяжёлых UI-библиотек — стилизация через CSS и Telegram тему

### Подзадачи

<!-- [x] --> 14.1. **Инициализация проекта**
- `npm create vite@latest tma -- --template react-ts`
- Установить зависимости:
  ```json
  {
    "@telegram-apps/sdk-react": "latest",
    "recharts": "latest",
    "react-router-dom": "latest"
  }
  ```
- Настроить Vite: base path, proxy для dev

<!-- [x] --> 14.2. **Telegram SDK интеграция (hooks/useTelegram.ts)**
- `useTelegram()` hook:
  - `initData` — сырые данные для авторизации API-запросов
  - `user` — данные пользователя (id, name)
  - `themeParams` — цвета темы
  - `colorScheme` — light/dark
  - `ready()` — вызвать после загрузки интерфейса
  - `expand()` — развернуть на полный экран
  - `BackButton` — управление кнопкой «Назад»
- Инициализация:
  ```html
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  ```

<!-- [x] --> 14.3. **API-клиент (api/client.ts)**
- Базовый fetch-клиент:
  ```typescript
  async function apiRequest(endpoint: string, params?: Record<string, string>) {
    const url = new URL(endpoint, API_BASE_URL);
    if (params) Object.entries(params).forEach(([k,v]) => url.searchParams.set(k,v));
    const res = await fetch(url, {
      headers: { "Authorization": `tma ${window.Telegram.WebApp.initData}` }
    });
    if (!res.ok) throw new Error(`API error: ${res.status}`);
    return res.json();
  }
  ```

<!-- [x] --> 14.4. **Страницы**

**Dashboard (главная)**
- Карточки-метрики: Всего потрачено, Средний CPC, Средний CTR, Общий ROI
- Мини-график расходов за 7 дней
- Alerts: «5 товаров сливают бюджет», «12 товаров без бонусов»
- Быстрые ссылки на разделы

**Products (список товаров)**
- Таблица: Название | Охваты | Клики | CTR | Затраты | ROI
- Сортировка по любому столбцу
- Поиск по названию
- Фильтры: С бонусом / Без бонуса / ROI > 0 / ROI < 0
- Пагинация (по 20 штук)

**Product Detail (карточка товара)**
- Основные метрики
- Линейный график: тренды за 7/14/30 дней (клики, затраты, ROI)
- Статус бонуса
- История изменений CPC

**Wasted Budget (слив бюджета)**
- Список товаров с ROI < 0, сортировка по потерям
- Каждый элемент: название, потрачено, ROI%, рекомендация

**No Bonus (товары без бонусов)**
- Список товаров без активных бонусов
- Каждый элемент: название, текущие охваты и клики (или «нет данных»)

**Most Clickable (самые кликабельные)**
- Топ товаров по CTR
- Элемент: название, CTR%, клики, затраты

<!-- [x] --> 14.5. **Стилизация под Telegram**
- Использовать CSS-переменные Telegram:
  ```css
  body {
    background: var(--tg-theme-bg-color);
    color: var(--tg-theme-text-color);
  }
  .card {
    background: var(--tg-theme-secondary-bg-color);
    border-radius: 12px;
  }
  .btn-primary {
    background: var(--tg-theme-button-color);
    color: var(--tg-theme-button-text-color);
  }
  ```
- Mobile-first, адаптивный дизайн
- Анимации 60fps, минимально (учитываем LOW performance class на Android)

<!-- [x] --> 14.6. **Навигация и BackButton**
- React Router для страниц
- На всех страницах кроме Dashboard — показывать BackButton
- Swipe-навигация

### Критерий завершения
- [x] TMA открывается из Telegram-бота
- [x] Авторизация через initData работает
- [x] Dashboard показывает актуальные данные
- [x] Все 6 страниц работают и корректно отображаются
- [x] Тёмная/светлая тема из Telegram подхватывается
- [x] Графики рендерятся корректно

---

## Фаза 15: Деплой и интеграция

**Цель:** Связать всё вместе, настроить бот, развернуть.

### Подзадачи

<!-- [x] --> 15.1. **Сборка TMA**
- `npm run build` → генерирует `tma/dist/`
- API-сервер раздаёт статику из `tma/dist/`
- Эндпоинт: `GET /tma/*` → файлы фронтенда

<!-- [ ] --> 15.2. **Регистрация TMA в BotFather**
- `/mybots` → Select Bot → Bot Settings → Configure Mini App
- Установить URL: `https://your-domain:8080/tma`
- Или установить Menu Button: text = «📊 Аналитика», url = TMA URL

<!-- [x] --> 15.3. **Telegram-команда `/analytics`**
- Отправляет inline-кнопку с `web_app` URL
- При нажатии — открывается TMA в Telegram

<!-- [ ] --> 15.4. **HTTPS (для production)**
- TMA требует HTTPS
- Варианты:
  - Cloudflare Tunnel (бесплатно, просто)
  - Nginx + Let's Encrypt
  - ngrok (для dev)

<!-- [ ] --> 15.5. **Итоговое тестирование**
- Полный цикл: скрапинг → обработка → API → TMA
- Тест на 600+ товарах
- Проверка на реальном устройстве (iOS + Android)

### Критерий завершения
- [x] Бот работает со всей существующей функциональностью (фазы 1–8)
- [x] TMA открывается из бота и показывает реальные данные
- [x] Ежедневный автосбор работает без вмешательства
- [x] При истечении сессии — бот запрашивает SMS через Telegram

---

## Порядок разработки

| # | Фаза | Зависимости | Приоритет |
|---|-------|-------------|-----------|
| 10 | Auth & Session Manager | — | **P0** |
| 11 | Marketing Scraper | Фаза 10 | **P0** |
| 12 | Data Processor | Фаза 11 | **P1** |
| 13 | REST API для TMA | Фазы 11, 12 | **P1** |
| 14 | TMA Frontend | Фаза 13 | **P1** |
| 15 | Деплой и интеграция | Все | **P2** |

---

## Технические ограничения и риски

### 1. Kaspi 2FA (СМС-код)
- **Риск:** каждый вход требует SMS
- **Решение:** сохранение `storage_state.json` через Playwright; сессия живёт ~30 дней
- **Fallback:** FSM в aiogram для ввода SMS через Telegram

### 2. Динамический контент Kaspi Pay
- **Риск:** ID элементов меняются при обновлениях
- **Решение:**
  - Искать по тексту: `page.get_by_text("Kaspi Marketing")`
  - По aria-label: `page.get_by_role("button", name="...")`
  - Сложные CSS-селекторы как fallback
  - Тесты-детекторы: если структура изменилась → уведомить админа

### 3. Антифрод Kaspi
- **Риск:** частые заходы → блокировка IP/сессии
- **Решение:**
  - Сбор 1 раз в сутки (ночью, 03:00)
  - Использование существующего прокси
  - Рандомные задержки между действиями
  - User-Agent от реального Chrome

### 4. Бесконечный скролл
- **Риск:** 600+ товаров не подгружаются за раз
- **Решение:** цикл скролла с проверкой `row_count` + таймаут безопасности

### 5. HTTPS для TMA
- **Риск:** Telegram требует HTTPS для Mini Apps
- **Решение:** Cloudflare Tunnel или nginx + certbot для production

---

## Зависимости (новые пакеты)

```
# requirements.txt — дополнение
playwright>=1.40.0
pytest-playwright>=0.4.0
```

```
# tma/package.json — отдельный проект
react, react-dom, typescript, vite
@telegram-apps/sdk-react
recharts
react-router-dom
```
