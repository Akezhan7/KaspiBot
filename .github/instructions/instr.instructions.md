---
applyTo: '**'
---
# KASPI SELLERS MONITOR BOT

## ЗАДАЧА
Telegram-бот отслеживает новых продавцов на товарах Kaspi.kz
- Старт: 300 товаров → через год: 1000 товаров
- Проверка каждые 6 часов
- Уведомление: новый продавец + имя магазина + цена + телефон

## СТЕК
Python 3.11+ | aiogram 3.x | httpx | SQLite | APScheduler | Redis (опционально)

## БАЗА ДАННЫХ - SQLite

### Таблицы

**products**
- master_sku TEXT PRIMARY KEY
- url TEXT
- title TEXT
- added_at TIMESTAMP
- last_checked TIMESTAMP

**sellers**
- merchant_id TEXT PRIMARY KEY
- merchant_name TEXT
- phone TEXT
- created_at TIMESTAMP

**product_sellers**
- product_id TEXT (FK products)
- seller_id TEXT (FK sellers)
- price REAL
- first_seen TIMESTAMP
- last_seen TIMESTAMP
- is_active INTEGER (0/1)
- PRIMARY KEY (product_id, seller_id)

**scan_logs**
- id INTEGER PRIMARY KEY
- started_at TIMESTAMP
- finished_at TIMESTAMP
- products_checked INTEGER
- new_sellers INTEGER
- errors TEXT

## ПАРСИНГ KASPI API

### Endpoint
```
GET https://kaspi.kz/yml/offer-view/offers/{master_sku}
```

### Headers
- User-Agent: Mozilla/5.0...
- Accept: application/json
- Referer: https://kaspi.kz/shop/...

### Response JSON
```json
{
  "offers": [
    {"merchantId": "xxx", "merchantName": "YYY", "price": 123.0}
  ]
}
```

### Получение телефона
Парсить HTML страницу магазина:
```
GET https://kaspi.kz/shop/info/merchant/{merchant_id}
```

В HTML есть JavaScript объект:
```javascript
BACKEND.components.merchant = {
    "uid": "1094131",
    "name": "NAVIEN ЦЕНТР KAZAKHSTAN",
    "phone": "+7 (701) 754-51-09",  // ← Целевое значение
    "rating": 5,
    "numberOfReviews": 7117,
    ...
}
```

**Парсинг:**
- Regex: `r'"phone":\s*"([^"]+)"'` или `r'BACKEND\.components\.merchant.*?"phone":\s*"([^"]+)"'`
- Кэшировать в БД - запрашивать только для НОВЫХ merchant_id
- Если не найден → сохранить NULL, не повторять запрос
- Задержка: 3-6 сек (больше чем для товаров)

## ПРОКСИ

**MobileProxy.Space** - 1 прокси Казахстан (4190₽/мес)
- Смена IP через API после каждых 50 товаров
- Задержка после смены: 30-60 сек
- Между запросами товаров: 2-5 сек (random)

## ЛОГИКА ПАРСЕРА

### Основной цикл (каждые 6 часов)
1. Взять все товары из БД
2. Батчами по 50:
   - Запросить offers для каждого товара
   - Смена IP через API
   - Задержка 30-60 сек
3. Обработка результатов

### Обработка товара
1. GET `/offers/{master_sku}`
2. Парсинг offers[]
3. Для каждого offer:
   - Если merchant_id новый:
     - GET `/shop/info/merchant/{merchant_id}` → парсить телефон из HTML
     - Задержка 3-6 сек
     - Сохранить в sellers (merchant_id, merchant_name, phone)
   - Если связка (product, seller) новая → INSERT в product_sellers → **уведомление в Telegram**
   - Если существует → UPDATE last_seen, price
4. Если merchant_id отсутствует в новых offers → is_active=0

### Rate Limits
- Задержка между товарами: random(2, 5) сек
- Задержка для страницы магазина: random(3, 6) сек
- После смены IP: random(30, 60) сек
- Смена IP: каждые 50 запросов (товары + магазины суммарно)
- Timeout запроса: 30 сек
- Retry при ошибке: 3 попытки с паузами 5s, 15s, 45s

## TELEGRAM BOT

### Команды
- `/add <url>` - добавить товар (извлечь master_sku)
- `/list` - список товаров (пагинация)
- `/remove <sku>` - удалить товар
- `/stats` - статистика
- `/scan` - принудительная проверка (admin)

### Уведомление о новом продавце
```
🆕 Новый продавец
Товар: [название]
Магазин: [имя]
Цена: [цена] ₸
Телефон: [номер]
Всего продавцов: N
```

Если 10+ новых за цикл → группировать в одно сообщение

## КОНФИГ (.env)
```
TELEGRAM_BOT_TOKEN=
ADMIN_USER_IDS=123,456
PROXY_URL=http://user:pass@host:port
PROXY_CHANGE_API=https://...
SCAN_INTERVAL_HOURS=6
```

## СТРУКТУРА
```
kaspi_monitor/
├── bot/              # Telegram handlers
├── parser/           # Парсинг Kaspi + прокси
├── database/         # SQLite + queries
├── config.py
├── main.py
└── requirements.txt
```

## КРИТИЧНО

1. **Извлечение master_sku из URL:**
   - `https://kaspi.kz/shop/p/название-107664472/`
   - Regex: `/(\d{8,})/`

2. **Телефоны:**
   - Запрашивать ТОЛЬКО для новых продавцов
   - Сохранять в БД навсегда

3. **"Новый" продавец:**
   - Новая запись (product_id + seller_id) = уведомление
   - Вернулся после is_active=0 = тоже уведомление

4. **Смена IP:**
   - Счетчик запросов в памяти/Redis
   - После 50 → POST к API → wait 30-60s

## ПОЧЕМУ SQLite
Для 1000 товаров × 20 продавцов = 20,000 записей - SQLite работает отлично
PostgreSQL избыточен на старте. Миграция на Postgres только при >5000 товаров