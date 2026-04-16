# KaspiBot — Мониторинг продавцов Kaspi.kz

Telegram-бот для отслеживания продавцов-нарушителей на Kaspi.kz с полным циклом:
мониторинг → WhatsApp-уведомления → диалог → эскалация → юридическая заявка.

## Возможности

- **Мониторинг** — автоматическая проверка до 1000 товаров каждые 6 часов
- **Уведомления** — оповещение администраторов о новых продавцах (телефон, магазин, цена)
- **WhatsApp-воронка** — автоматические предупреждения (WARN1 → WARN2) через Green API
- **Умный диалог** — классификация ответов продавцов с помощью LLM (OpenAI)
- **Эскалация** — автоматическое продвижение по воронке по таймаутам
- **Юридические заявки** — сбор доказательной базы, контрольная закупка, экспорт в ZIP
- **Админ-панель** — управление воронками, заявками и экспортом через Telegram
- **Безопасность** — IP-фильтрация webhook, rate limiting, валидация входных данных

## Требования

- Python 3.11+
- Telegram Bot Token
- MobileProxy.Space прокси (Казахстан)
- Green API аккаунт (для WhatsApp)
- OpenAI API Key (для классификации сообщений)

## Быстрый старт

```bash
git clone <repo_url>
cd KaspiBot
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env           # заполнить переменные
python main.py
```

Подробная установка: [INSTALL.md](INSTALL.md)

## Настройка

### Минимальная конфигурация (.env)

```env
TELEGRAM_BOT_TOKEN=your_bot_token
ADMIN_USER_IDS=123456789
PROXY_URL=socks5://user:pass@host:port
```

### WhatsApp (Green API)

```env
GREEN_API_URL=https://api.green-api.com
GREEN_API_INSTANCE_ID=1101234567
GREEN_API_TOKEN=abc123...
WHATSAPP_WEBHOOK_HOST=0.0.0.0
WHATSAPP_WEBHOOK_PORT=8443
```

Подробная инструкция: [docs/GREEN_API_SETUP.md](docs/GREEN_API_SETUP.md)

### OpenAI (классификация)

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

### Эскалация (таймауты)

```env
ESCALATION_INTERVAL_MINUTES=30
WARN1_TIMEOUT_HOURS=48
WARN2_TIMEOUT_HOURS=24
DIALOG_TIMEOUT_HOURS=72
```

Все переменные описаны в [.env.example](.env.example).

## Команды бота

### Базовые команды

| Команда             | Описание                               |
| ------------------- | -------------------------------------- |
| `/start`            | Приветствие и справка                  |
| `/add <url>`        | Добавить товар для мониторинга (admin) |
| `/list`             | Список отслеживаемых товаров           |
| `/remove <sku>`     | Удалить товар (admin)                  |
| `/recent`           | Последние обнаруженные продавцы        |
| `/search <запрос>`  | Поиск товаров по названию              |
| `/sellers`          | Список продавцов по товарам            |
| `/stats`            | Статистика                             |
| `/scan`             | Принудительное сканирование (admin)    |
| `/export_urls`      | Выгрузить все URL в файл (admin)       |

### Управление воронками (admin)

| Команда                      | Описание                           |
| ---------------------------- | ---------------------------------- |
| `/workflows [page]`          | Список активных воронок            |
| `/workflow <id>`             | Карточка воронки (статус, товары)  |
| `/close_workflow <id>`       | Закрыть воронку вручную            |

### Юридические заявки (admin)

| Команда                              | Описание                       |
| ------------------------------------ | ------------------------------ |
| `/legal_requests [page]`             | Список юридических заявок      |
| `/legal <id>`                        | Карточка заявки                |
| `/export <id> [json\|csv]`           | Экспорт доказательной базы     |
| `/assign_purchase <id> <@username>`  | Назначить контрольную закупку  |
| `/purchase_done <id>`                | Ввести данные закупки          |

### Inline-кнопки

В карточках воронок доступны кнопки для быстрых действий:
- ⚠️ Отправить WARN1
- 📤 Отправить WARN2
- ⚖️ Создать юрзаявку
- ✅ Закрыть воронку

## Архитектура

```
Telegram Bot ←→ WhatsApp Webhook
     ↓                ↓
   Workflow Engine (бизнес-логика)
     ↓
  Database Layer (DAO)
```

### Воронка продавца (State Machine)

```
NEW_SELLER_ATTACH → WARN1_SENT → WARN2_SENT → LEGAL_REQUEST_CREATED
                         ↓            ↓               ↓
                   DIALOG_ACTIVE ─────┘    CONTROL_PURCHASE_REQUIRED
                         ↓                        ↓
                      DETACHED            READY_FOR_LAWSUIT
                         ↓
                       CLOSED ← ─ ─ ─ ─ RECIDIVE (при возврате)
```

## Структура проекта

```
KaspiBot/
├── bot/                    # Telegram
│   ├── handlers.py         # Команды пользователей
│   ├── admin_handlers.py   # Команды администратора
│   ├── notifications.py    # Уведомления
│   └── utils.py            # Утилиты
├── parser/                 # Парсинг Kaspi.kz
│   ├── kaspi_parser.py     # API-клиент
│   ├── proxy_manager.py    # Прокси + ротация IP
│   └── scanner.py          # Сканирование товаров
├── database/               # SQLite DAO
│   ├── schema.py           # Создание таблиц
│   ├── migrations.py       # Миграции
│   ├── products.py         # Товары
│   ├── sellers.py          # Продавцы
│   ├── product_sellers.py  # Связи товар-продавец
│   ├── seller_workflow.py  # Воронки продавцов
│   ├── message_log.py      # Лог WhatsApp-сообщений
│   ├── legal_requests.py   # Юридические заявки
│   ├── scan_logs.py        # История сканирований
│   └── recent_sellers.py   # Недавние продавцы
├── whatsapp/               # WhatsApp через Green API
│   ├── client.py           # HTTP-клиент Green API
│   ├── webhook.py          # Приём входящих сообщений
│   ├── classifier.py       # LLM-классификация ответов
│   ├── templates.py        # Шаблоны сообщений WARN1/WARN2
│   └── phone_utils.py      # Нормализация телефонов
├── workflow/               # Бизнес-логика
│   ├── engine.py           # WorkflowEngine
│   ├── escalation.py       # Автоматическая эскалация
│   └── export.py           # Экспорт доказательной базы
├── data/legal/             # Документы закупок
├── logs/                   # Логи
├── config.py               # Конфигурация из .env
├── main.py                 # Точка входа
└── requirements.txt
```

## База данных

SQLite с 8 таблицами:

| Таблица             | Назначение                           |
| ------------------- | ------------------------------------ |
| `products`          | Отслеживаемые товары                 |
| `sellers`           | Продавцы (merchant_id, телефон)      |
| `product_sellers`   | Связь товар ↔ продавец              |
| `scan_logs`         | История сканирований                 |
| `seller_workflows`  | Воронки продавцов (state machine)    |
| `workflow_products` | Товары, привязанные к воронке        |
| `message_log`       | Лог WhatsApp-переписки               |
| `legal_requests`    | Юридические заявки                   |

## Документация

- [INSTALL.md](INSTALL.md) — Установка и запуск
- [docs/GREEN_API_SETUP.md](docs/GREEN_API_SETUP.md) — Настройка Green API и webhook
- [docs/INSTRUCTION_PURCHASE.md](docs/INSTRUCTION_PURCHASE.md) — Инструкция по контрольной закупке
- [docs/INSTRUCTION_LEGAL.md](docs/INSTRUCTION_LEGAL.md) — Инструкция для юристов
- [ROADMAP.md](ROADMAP.md) — План разработки

## Логирование

Логи сохраняются в `logs/bot.log` и выводятся в консоль.
Уровни: DEBUG (dev) → INFO (бизнес-события) → WARNING → ERROR → CRITICAL.

## Тесты

```bash
pytest tests/ -v
```

119 тестов покрывают: DAO-операции, шаблоны, классификатор, workflow engine,
эскалацию, экспорт, контрольную закупку, безопасность webhook.

## Лицензия

MIT