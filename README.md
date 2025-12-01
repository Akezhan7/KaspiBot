# Kaspi Sellers Monitor Bot

Telegram-бот для отслеживания новых продавцов на товарах Kaspi.kz

## Возможности

- ✅ Мониторинг до 1000 товаров Kaspi.kz
- ✅ Автоматическая проверка каждые 6 часов
- ✅ Уведомления о новых продавцах с контактами
- ✅ Извлечение телефонов из страниц магазинов
- ✅ Умная ротация IP через MobileProxy.Space
- ✅ SQLite база данных
- ✅ Команды управления через Telegram

## Требования

- Python 3.11+
- Telegram Bot Token
- MobileProxy.Space прокси (Казахстан)

## Установка

1. **Клонировать репозиторий**
```bash
git clone <repo_url>
cd KaspiBot
```

2. **Создать виртуальное окружение**
```bash
python -m venv venv
venv\Scripts\activate  # Windows
# source venv/bin/activate  # Linux/Mac
```

3. **Установить зависимости**
```bash
pip install -r requirements.txt
```

4. **Настроить переменные окружения**
```bash
cp .env.example .env
```

Отредактировать `.env`:
```env
TELEGRAM_BOT_TOKEN=your_bot_token
ADMIN_USER_IDS=123456789
PROXY_URL=http://user:pass@host:port
PROXY_CHANGE_API=https://mobileproxy.space/api/change_ip
```

5. **Запустить бота**
```bash
python main.py
```

## Команды бота

- `/start` - Приветствие и справка
- `/add <url>` - Добавить товар для мониторинга
- `/list` - Список отслеживаемых товаров
- `/remove <sku>` - Удалить товар
- `/stats` - Статистика
- `/scan` - Принудительная проверка (admin)

## Структура проекта

```
kaspi_monitor/
├── bot/                # Telegram handlers
│   ├── handlers.py     # Команды бота
│   ├── notifications.py # Уведомления
│   └── utils.py        # Утилиты
├── parser/             # Парсинг Kaspi
│   ├── kaspi_parser.py # API парсер
│   ├── proxy_manager.py # Прокси менеджер
│   └── scanner.py      # Логика сканирования
├── database/           # SQLite
│   ├── schema.py       # Схема БД
│   ├── products.py     # CRUD товары
│   ├── sellers.py      # CRUD продавцы
│   └── product_sellers.py # Связи
├── config.py           # Конфигурация
├── main.py             # Точка входа
└── requirements.txt
```

## База данных

SQLite с 4 таблицами:
- `products` - Товары
- `sellers` - Продавцы (с телефонами)
- `product_sellers` - Связь многие-ко-многим
- `scan_logs` - История сканирований

## Логирование

Логи сохраняются в `logs/bot.log` и выводятся в консоль.

## Разработка

### Добавление новых команд
Редактировать `bot/handlers.py`

### Изменение интервала сканирования
В `.env` изменить `SCAN_INTERVAL_HOURS`

### Настройка rate limits
В `.env` изменить задержки: `PRODUCT_DELAY_MIN/MAX`, `MERCHANT_DELAY_MIN/MAX`

## Лицензия

MIT
