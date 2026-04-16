# ROADMAP: Автоматизация работы с "прилепалами"

> Поэтапный план доработки KaspiBot.
> Каждая фаза — самодостаточный инкремент, который можно протестировать отдельно.

---

## Текущее состояние (baseline)

**Что уже работает:**
- Сканирование товаров каждые 12 часов через Kaspi API
- Обнаружение новых / вернувшихся продавцов
- Сбор данных: merchant_id, merchant_name, phone, price
- Уведомления в Telegram (индивидуальные и групповые)
- Команды: /add, /list, /remove, /search, /sellers, /recent, /stats, /scan
- Пагинация, inline-кнопки, просмотр карточек продавцов
- Прокси с ротацией IP каждые 50 запросов

**Текущие таблицы БД:**
- `products`, `sellers`, `product_sellers`, `scan_logs`, `recent_sellers`

**Текущая структура:**
```
kaspi_monitor/
├── bot/handlers.py, notifications.py, utils.py
├── parser/kaspi_parser.py, proxy_manager.py, scanner.py
├── database/schema.py, products.py, sellers.py, product_sellers.py,
│            scan_logs.py, recent_sellers.py
├── config.py, main.py
```

---

## Архитектурные решения (принять до начала разработки)

### 1. Новые модули — не трогаем старые без необходимости

```
kaspi_monitor/
├── bot/
│   ├── handlers.py          # существующие команды (не трогаем)
│   ├── admin_handlers.py    # НОВЫЙ: команды управления воронкой
│   ├── notifications.py     # расширяем: +whatsapp события
│   └── utils.py             # расширяем: +форматирование юрзаявок
├── parser/                  # НЕ ТРОГАЕМ (только точка интеграции в scanner)
├── database/
│   ├── schema.py            # расширяем: +новые таблицы
│   ├── seller_workflow.py   # НОВЫЙ: статусы, переходы воронки
│   ├── message_log.py       # НОВЫЙ: лог переписки
│   ├── legal_requests.py    # НОВЫЙ: юрзаявки
│   └── ...                  # остальные без изменений
├── whatsapp/
│   ├── __init__.py
│   ├── client.py            # НОВЫЙ: WhatsApp Business API клиент
│   ├── templates.py         # НОВЫЙ: шаблоны сообщений
│   ├── classifier.py        # НОВЫЙ: классификация входящих
│   └── webhook.py           # НОВЫЙ: приём входящих сообщений
├── workflow/
│   ├── __init__.py
│   ├── engine.py            # НОВЫЙ: движок воронки (state machine)
│   ├── escalation.py        # НОВЫЙ: эскалация WARN1→WARN2→LEGAL
│   └── export.py            # НОВЫЙ: экспорт доказательств
├── config.py                # расширяем: +WhatsApp настройки
└── main.py                  # расширяем: +webhook, +scheduled tasks
```

### 2. State Machine для статусов продавца

```
NEW_SELLER_ATTACH
    ↓
WARN1_SENT ──→ [продавец ответил] ──→ DIALOG_ACTIVE
    ↓ (24ч)                                ↓
WARN2_SENT ──→ [продавец ответил] ──→ DIALOG_ACTIVE
    ↓ (24ч)                                ↓
LEGAL_REQUEST_CREATED                      │
    ↓                                      │
CONTROL_PURCHASE_REQUIRED                  │
    ↓                                      │
READY_FOR_LAWSUIT                          │
                                           ↓
                              DETACHED (отсоединился) ──→ CLOSED
                              PARTIALLY_DETACHED ──→ уточняющее сообщение
```

**Особые переходы:**
- Из ЛЮБОГО статуса → `DETACHED` → `CLOSED` (если повторная проверка подтвердила отсоединение)
- Из `CLOSED` → `RECIDIVE` (если прилепился снова) → сразу `WARN2_SENT`

### 3. WhatsApp — Green API (неофициальный)

Используем **Green API** (green-api.com) вместо официального Meta WhatsApp Business API:
- Нет модерации шаблонов — можно отправлять любой текст (WARN1, WARN2 с юридическими формулировками)
- Нет процедуры верификации бизнеса через Meta
- Фиксированная подписка (~$15/мес) вместо оплаты за каждый диалог
- Работает как WhatsApp Web — привязка номера через QR-код
- Есть webhook для входящих + REST API для исходящих

Клиент реализуем через абстрактный базовый класс, чтобы:
- Легко подменить провайдера (Green API → whapi.cloud → другой) при необходимости
- Тестировать без реального API (mock)

**Риски Green API:** возможная блокировка номера за спам. Митигация:
- Вежливый тон, ограничение количества сообщений
- Задержки между отправками (5-10 сек)
- При блокировке — привязка нового номера за 5 минут

### 4. Webhook для входящих сообщений

Для приёма ответов от продавцов нужен HTTP-сервер.
Используем `aiohttp` (лёгкий, async, совместим с aiogram).
Webhook запускается параллельно с Telegram-ботом в том же event loop.
Green API отправляет webhook на наш endpoint при каждом входящем сообщении.

---

## ФАЗА 1: Расширение БД и модель данных

- [x] **Фаза завершена**

**Цель:** Подготовить хранилище для всей новой логики.

### 1.1 Новые таблицы в `database/schema.py` <!-- [x] -->

```sql
-- Воронка продавца (один продавец может быть в воронке по нескольким товарам)
seller_workflows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id TEXT NOT NULL REFERENCES sellers(merchant_id),
    status TEXT NOT NULL DEFAULT 'NEW_SELLER_ATTACH',
    -- статусы: NEW_SELLER_ATTACH, WARN1_SENT, WARN2_SENT,
    --          DIALOG_ACTIVE, LEGAL_REQUEST_CREATED,
    --          CONTROL_PURCHASE_REQUIRED, READY_FOR_LAWSUIT,
    --          DETACHED, CLOSED, RECIDIVE
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    warn1_sent_at TIMESTAMP,
    warn2_sent_at TIMESTAMP,
    detached_at TIMESTAMP,
    closed_at TIMESTAMP,
    notes TEXT
);

-- Привязка товаров к воронке (какие именно товары фигурируют)
workflow_products (
    workflow_id INTEGER REFERENCES seller_workflows(id),
    product_id TEXT REFERENCES products(master_sku),
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    still_attached INTEGER DEFAULT 1,
    PRIMARY KEY (workflow_id, product_id)
);

-- Лог всех сообщений (WhatsApp)
message_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id INTEGER REFERENCES seller_workflows(id),
    seller_id TEXT NOT NULL REFERENCES sellers(merchant_id),
    direction TEXT NOT NULL, -- 'IN' или 'OUT'
    channel TEXT NOT NULL DEFAULT 'whatsapp',
    message_text TEXT NOT NULL,
    template_code TEXT, -- код шаблона, если исходящее
    wa_message_id TEXT, -- ID сообщения в WhatsApp (для статусов доставки)
    classification TEXT, -- тип входящего: DIDNT_KNOW, PROVE_IT, WONT_REMOVE, и т.д.
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Юридические заявки
legal_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id INTEGER NOT NULL REFERENCES seller_workflows(id),
    seller_id TEXT NOT NULL REFERENCES sellers(merchant_id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    shop_name TEXT,
    phone TEXT,
    product_links TEXT, -- JSON массив ссылок
    detection_dates TEXT, -- JSON таймлайн
    warn_timeline TEXT, -- JSON: {warn1_at, warn2_at, ...}
    dialog_log TEXT, -- полный текстовый лог
    bin_iin TEXT, -- заполняется после контрольной закупки
    purchase_order_number TEXT,
    purchase_notes TEXT,
    purchase_documents TEXT, -- JSON массив путей к файлам
    control_purchase_status TEXT DEFAULT 'PENDING',
    -- PENDING, ASSIGNED, COMPLETED
    ready_for_lawsuit INTEGER DEFAULT 0,
    assigned_to TEXT, -- кому назначена закупка
    completed_at TIMESTAMP
);
```

### 1.2 Новые DAO-классы <!-- [x] -->

- `database/seller_workflow.py` — `SellerWorkflowDB`
  - `create_workflow(seller_id) → int` (workflow_id)
  - `get_workflow(workflow_id) → Dict`
  - `get_active_workflow_for_seller(seller_id) → Dict | None`
  - `update_status(workflow_id, new_status) → None`
  - `get_workflows_by_status(status) → List[Dict]`
  - `get_workflows_for_escalation(status, older_than_hours) → List[Dict]`
  - `add_product_to_workflow(workflow_id, product_id) → None`
  - `get_workflow_products(workflow_id) → List[Dict]`
  - `update_product_attached(workflow_id, product_id, still_attached) → None`
  - `get_all_active_workflows(limit, offset) → List[Dict]`

- `database/message_log.py` — `MessageLogDB`
  - `log_message(workflow_id, seller_id, direction, text, template_code, wa_message_id, classification) → int`
  - `get_messages_for_workflow(workflow_id) → List[Dict]`
  - `get_messages_for_seller(seller_id) → List[Dict]`
  - `get_last_outgoing(workflow_id) → Dict | None`
  - `count_messages_today(seller_id, direction) → int` (антиспам)

- `database/legal_requests.py` — `LegalRequestsDB`
  - `create_request(workflow_id, seller_id, ...) → int`
  - `get_request(request_id) → Dict`
  - `get_request_by_workflow(workflow_id) → Dict | None`
  - `update_purchase_info(request_id, bin_iin, order_number, notes, documents) → None`
  - `mark_ready_for_lawsuit(request_id) → None`
  - `get_pending_purchases() → List[Dict]`
  - `get_all_requests(limit, offset) → List[Dict]`

### 1.3 Расширение таблицы `sellers` <!-- [x] -->

Добавить колонку (через ALTER TABLE в миграции):
```sql
ALTER TABLE sellers ADD COLUMN whatsapp_available INTEGER DEFAULT NULL;
-- NULL = не проверяли, 1 = есть WhatsApp, 0 = нет
```

### 1.4 Миграции <!-- [x] -->

Создать `database/migrations.py`:
- Проверяет текущую версию схемы (таблица `schema_version`)
- Применяет ALTER TABLE и CREATE TABLE по порядку
- Безопасно: IF NOT EXISTS, проверка колонок

**Критерий завершения:** Все новые таблицы создаются, тесты CRUD проходят, старая функциональность не сломана.

---

## ФАЗА 2: WhatsApp клиент (Green API)

- [x] **Фаза завершена**

**Цель:** Модуль отправки/приёма сообщений через WhatsApp (Green API).

### 2.1 Конфигурация (`config.py`) <!-- [x] -->

Добавить:
```python
GREEN_API_URL = os.getenv("GREEN_API_URL", "https://api.green-api.com")
GREEN_API_INSTANCE_ID = os.getenv("GREEN_API_INSTANCE_ID")  # ID инстанса
GREEN_API_TOKEN = os.getenv("GREEN_API_TOKEN")              # API токен инстанса
WHATSAPP_WEBHOOK_PORT = int(os.getenv("WHATSAPP_WEBHOOK_PORT", "8443"))
```

### 2.2 Клиент (`whatsapp/client.py`) <!-- [x] -->

Абстрактный базовый класс `WhatsAppClientBase` + реализация `GreenAPIClient`:

```python
class WhatsAppClientBase(ABC):
    @abstractmethod
    async def send_text(self, to_phone: str, text: str) -> Dict: ...
    @abstractmethod
    async def check_phone_exists(self, phone: str) -> bool: ...
```

Класс `GreenAPIClient(WhatsAppClientBase)`:
- `__init__(api_url, instance_id, token)`
- `send_text(to_phone, text) → Dict` — отправка сообщения (любой текст, без ограничений)
- `check_phone_exists(phone) → bool` — проверка наличия WhatsApp на номере
- `mark_as_read(chat_id) → None`
- `_request(method, endpoint, payload) → Dict` — базовый HTTP-запрос с retry

Green API endpoints:
- `POST /waInstance{id}/sendMessage/{token}` — отправка текста
- `POST /waInstance{id}/checkWhatsapp/{token}` — проверка номера
- `POST /waInstance{id}/readChat/{token}` — пометить прочитанным

Формат номера: `+7 (701) 754-51-09` → `77017545109@c.us`.

### 2.3 Webhook-сервер (`whatsapp/webhook.py`) <!-- [x] -->

Используем `aiohttp`:
- `POST /webhook` — приём входящих сообщений от Green API
- Валидация: проверка IP-адреса отправителя (whitelist Green API серверов)
- Парсинг payload Green API → вызов обработчика

Формат входящего webhook от Green API:
```json
{
  "typeWebhook": "incomingMessageReceived",
  "senderData": { "chatId": "77017545109@c.us", "senderName": "..." },
  "messageData": { "textMessageData": { "textMessage": "текст" } }
}
```

Обработчик входящего сообщения:
1. Извлечь номер отправителя из `chatId`, текст из `messageData`
2. Найти продавца по номеру телефона в БД
3. Найти активный workflow
4. Классифицировать сообщение (через LLM, см. Фазу 3)
5. Записать в message_log (direction='IN')
6. Сформировать ответ → отправить → записать (direction='OUT')
7. Уведомить админов в Telegram о входящем

### 2.4 Нормализация телефонов <!-- [x] -->

Утилита `whatsapp/phone_utils.py`:
- `normalize_phone(phone) → str` — приведение к E.164 (`+7 (701) 754-51-09` → `77017545109`)
- `is_valid_kz_phone(phone) → bool`

**Критерий завершения:** Можно отправить текстовое сообщение через Green API, получить входящее через webhook, записать в лог.

---

## ФАЗА 3: Шаблоны сообщений и LLM-классификация ответов

- [x] **Фаза завершена**

**Цель:** Система шаблонных сообщений и распознавание входящих через LLM.

### 3.1 Шаблоны (`whatsapp/templates.py`) <!-- [x] -->

Структура шаблона:
```python
@dataclass
class MessageTemplate:
    code: str           # "WARN1_SOFT_01"
    category: str       # "WARN1" | "WARN2" | "AUTO_REPLY" | "CLARIFICATION"
    text: str           # Текст с плейсхолдерами: {shop_name}, {product_links}, {deadline}
    tone: str           # "soft" | "firm" | "legal"
```

Функции:
- `get_warn1_template(product_links) → MessageTemplate` — рандомный выбор из 5-10 вариантов
- `get_warn2_template(product_links) → MessageTemplate` — рандомный из 5-10 строгих
- `get_auto_reply(classification, context) → MessageTemplate` — ответ на основе типа входящего
- `render_template(template, context: Dict) → str` — подстановка переменных

Модерация шаблонов не требуется — Green API отправляет любой текст как обычное сообщение.

### 3.2 LLM-классификация входящих (`whatsapp/classifier.py`) <!-- [x] -->

Используем **gpt-4o-mini** (OpenAI API) для классификации ответов продавцов.

Почему LLM, а не regex:
- Продавцы пишут на русском, казахском, смеси языков, с ошибками и сленгом
- Regex-классификатор на 8 типов — бесконечная отладка и ложные срабатывания
- gpt-4o-mini: ~$0.15/1M input токенов → на 100 сообщений/мес = центы

Класс `MessageClassifier`:
- `__init__(openai_api_key)`
- `classify(text) → str` — отправляет сообщение в LLM, получает JSON с типом
- **Жёсткий таймаут 5 сек** на вызов OpenAI API — webhook не должен висеть, иначе Green API начнёт дропать вебхуки

Типы ответов:
- `DIDNT_KNOW` — «Я не знал»
- `PROVE_IT` — «Докажите»
- `WONT_REMOVE` — «Не сниму»
- `ALREADY_REMOVED` — «Уже снял»
- `NEED_TIME` — «Дайте время»
- `AGGRESSIVE` — Агрессия
- `NEGOTIATE` — Попытка договориться
- `UNKNOWN` — Не удалось классифицировать

Системный промпт (суть):
```
Ты классифицируешь сообщения продавцов на Kaspi.kz.
Верни JSON: {"type": "<один из 8 типов>", "confidence": 0.0-1.0}
Сообщения могут быть на русском, казахском или смеси языков.
```

Fallback: если OpenAI API недоступен или таймаут (>5 сек) → возвращать `UNKNOWN`, админ разберёт вручную. Webhook отвечает Green API мгновенно, классификация не должна его блокировать.

Конфигурация (`config.py`):
```python
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
```

### 3.3 Матрица ответов <!-- [x] -->

Для каждого типа входящего — пул из 3-5 вариантов ответа.
Каждый ответ содержит:
- Срок (24 часа)
- Просьбу подтвердить отсоединение
- Ссылки на товары
- Юридическое предупреждение (только после WARN2)

**Критерий завершения:** Шаблоны рендерятся корректно, LLM-классификатор покрывает все 8 типов, fallback работает, матрица протестирована.

---

## ФАЗА 4: Движок воронки (Workflow Engine)

- [x] **Фаза завершена**

**Цель:** Основная бизнес-логика переходов между статусами.

### 4.1 Движок (`workflow/engine.py`) <!-- [x] -->

Класс `WorkflowEngine`:
- `__init__(workflow_db, message_log_db, legal_db, whatsapp_client, notification_service)`

Методы:
- `on_new_seller_detected(seller_id, product_ids) → int` — Создать workflow, привязать товары
- `send_warn1(workflow_id) → bool` — Отправить WARN1, обновить статус, записать лог
- `send_warn2(workflow_id) → bool` — Отправить WARN2, обновить статус
- `handle_incoming_message(seller_id, text) → None` — Классифицировать, ответить, обновить статус
- `check_detachment(workflow_id) → bool` — Повторная проверка через scanner, обновить attached-флаги
- `escalate_to_legal(workflow_id) → int` — Создать юрзаявку, собрать все данные
- `close_workflow(workflow_id, reason) → None` — Закрыть при отсоединении
- `handle_recidive(seller_id, product_ids) → int` — Рецидив: сразу WARN2

### 4.2 Интеграция с существующим сканером <!-- [x] -->

В `parser/scanner.py` → метод `scan_product()`:
- После обнаружения нового/вернувшегося продавца (is_new or was_inactive):
  - Вызвать `workflow_engine.on_new_seller_detected(seller_id, [product_id])`
  - Если уже есть активный workflow → добавить product к нему
  - Если продавец CLOSED и вернулся → `handle_recidive()`

**Изменения в scanner.py минимальны:** добавить один вызов workflow_engine после детекции. Вся логика — в workflow/engine.py.

### 4.3 Триггерный микро-скан при "Я уже снял" <!-- [x] -->

Когда LLM-классификатор возвращает `ALREADY_REMOVED`:
1. Немедленно вызвать `scanner.scan_product()` для каждого товара из workflow
2. Если продавец действительно исчез из offers → `DETACHED` → `CLOSED` + ответ: "Спасибо, подтверждаем отсоединение"
3. Если продавец всё ещё на карточке → ответ: "Мы проверили — товар(ы) всё ещё на карточке. Просим завершить отсоединение"
4. Обновить `still_attached` в `workflow_products`

Точечный запрос к Kaspi API — 2-3 секунды, не влияет на rate limits.
Это избавляет от ситуации, когда продавец снял товар и написал боту, а бот молчит 12 часов.

### 4.4 Антиспам <!-- [x] -->

- Максимум 3 исходящих сообщения в день на одного продавца
- Не отправлять WARN1 если workflow уже существует
- Пауза между сообщениями — минимум 1 час
- Задержка между WhatsApp-отправками: 5-10 сек (защита от бана номера)

**Критерий завершения:** Воронка проходит полный цикл NEW → WARN1 → WARN2 → LEGAL. Отсоединение на любом этапе → CLOSED. Микро-скан при ALREADY_REMOVED работает.

---

## ФАЗА 5: Планировщик эскалации

- [x] **Фаза завершена**

**Цель:** Автоматические переходы по таймеру.

### 5.1 Scheduled Tasks в APScheduler (`workflow/escalation.py`) <!-- [x] -->

Класс `EscalationScheduler`:
- `__init__(workflow_engine)`

Задачи (каждые 30 минут):
1. **process_new_sellers** — Найти workflow со статусом NEW_SELLER_ATTACH → отправить WARN1
2. **process_warn1_expiry** — Найти WARN1_SENT старше 24 часов:
   - Повторная проверка (отсоединился?) → если да → close
   - Если нет → send_warn2
3. **process_warn2_expiry** — Найти WARN2_SENT старше 24 часов:
   - Повторная проверка → если да → close
   - Если нет → escalate_to_legal
4. **process_dialog_timeout** — Найти DIALOG_ACTIVE без ответа 24 часа → вернуть к предыдущему warn-этапу

### 5.2 Защита от race conditions <!-- [x] -->

**Проблема:** Если задача эскалации выполняется дольше 30 минут (при большой БД), APScheduler запустит её повторно параллельно → дублирование WARN2, юрзаявок.

**Решение (два уровня):**

1. **APScheduler: `max_instances=1` + `coalesce=True`**
```python
scheduler.add_job(
    escalation.process_warn1_expiry,
    IntervalTrigger(minutes=30),
    max_instances=1,   # не запускать если предыдущий экземпляр ещё работает
    coalesce=True      # пропущенные запуски объединяются в один
)
```

2. **Оптимистичная блокировка в БД при смене статуса:**
```sql
UPDATE seller_workflows
SET status = 'WARN2_SENT', updated_at = CURRENT_TIMESTAMP
WHERE id = ? AND status = 'WARN1_SENT'
```
Обновит только если статус не изменился другим воркером. Проверять `rows_affected > 0`.

### 5.3 Регистрация в `main.py` <!-- [x] -->

Добавить задачи в AsyncIOScheduler:
```python
scheduler.add_job(escalation.process_new_sellers, IntervalTrigger(minutes=30),
                  max_instances=1, coalesce=True)
scheduler.add_job(escalation.process_warn1_expiry, IntervalTrigger(minutes=30),
                  max_instances=1, coalesce=True)
scheduler.add_job(escalation.process_warn2_expiry, IntervalTrigger(minutes=30),
                  max_instances=1, coalesce=True)
scheduler.add_job(escalation.process_dialog_timeout, IntervalTrigger(hours=1),
                  max_instances=1, coalesce=True)
```

### 5.4 Повторная проверка перед эскалацией <!-- [x] -->

Перед каждой эскалацией (WARN2, LEGAL) — обязательный вызов `check_detachment()`:
- Запросить текущие offers для всех товаров в workflow
- Если продавец отсутствует → DETACHED → CLOSED
- Если частично отсоединился → PARTIALLY_DETACHED → уточняющее сообщение
- Если всё ещё прилеплен → продолжить эскалацию

**Критерий завершения:** Воронка автоматически прогрессирует без ручного вмешательства. Тайминги соблюдаются. Дублирование событий невозможно.

---

## ФАЗА 6: Юридические заявки и экспорт

- [x] **Фаза завершена**

**Цель:** Формирование и экспорт доказательной базы.

### 6.1 Генерация юрзаявки (`workflow/engine.py` → `escalate_to_legal`) <!-- [x] -->

При эскалации собрать:
- Название магазина, телефон (из sellers)
- Список товаров со ссылками (из workflow_products + products)
- Даты обнаружения каждого товара
- Таймлайн: WARN1_at, WARN2_at
- Полный лог переписки (из message_log)

Сохранить в `legal_requests`.

### 6.2 Экспорт (`workflow/export.py`) <!-- [x] -->

Класс `EvidenceExporter`:
- `export_legal_request(request_id, format="json") → bytes` — JSON/CSV
- `export_dialog_log(workflow_id) → str` — текстовый лог переписки
- `export_timeline(workflow_id) → Dict` — хронология событий
- `generate_legal_package(request_id) → bytes` — ZIP-архив со всеми данными

**Защита от лимита Telegram (50 МБ):**
- Перед отправкой проверять размер ZIP-архива
- Если >45 МБ → сжать изображения (Pillow, quality=60) и разбить на части
- Каждая часть — отдельный ZIP ≤45 МБ, отправляемый как документ

Формат JSON-пакета:
```json
{
  "seller": { "name": "...", "phone": "...", "merchant_id": "..." },
  "products": [{ "url": "...", "title": "...", "detected_at": "..." }],
  "timeline": [
    { "event": "DETECTED", "at": "..." },
    { "event": "WARN1_SENT", "at": "...", "message": "..." },
    { "event": "WARN2_SENT", "at": "..." },
    { "event": "LEGAL_REQUEST", "at": "..." }
  ],
  "dialog": [
    { "direction": "OUT", "text": "...", "at": "..." },
    { "direction": "IN", "text": "...", "at": "...", "classification": "..." }
  ]
}
```

### 6.3 Уведомление команды <!-- [x] -->

При создании юрзаявки → уведомление в Telegram:
```
⚖️ Юридическая заявка #12

Магазин: NAVIEN ЦЕНТР
Телефон: +7 (701) 754-51-09
Товаров: 3
Обнаружен: 15.03.2026
WARN1: 15.03.2026 18:00
WARN2: 16.03.2026 18:00

Статус: Требуется контрольная закупка
👉 Назначить: /assign_purchase 12
```

**Критерий завершения:** Юрзаявка формируется автоматически, экспорт в JSON/ZIP работает, admin получает уведомление.

---

## ФАЗА 7: Контрольная закупка

- [x] **Фаза завершена**

**Цель:** Процесс ввода данных после закупки.

### 7.1 Команды в Telegram (`bot/admin_handlers.py`) <!-- [x] -->

- `/assign_purchase <request_id> <@username>` — назначить закупку (статус → ASSIGNED)
- `/purchase_done <request_id>` — начать ввод данных (бот запрашивает поля)
- Inline-диалог:
  1. «Введите БИН/ИИН:» → сохранить
  2. «Введите номер заказа:» → сохранить
  3. «Отправьте скриншоты/документы:» → сохранить файлы
  4. «Комментарий (опционально):» → сохранить
  5. Подтверждение → статус → READY_FOR_LAWSUIT

### 7.2 FSM (Finite State Machine) для ввода <!-- [x] -->

Использовать `aiogram.fsm`:
- States: `waiting_bin`, `waiting_order`, `waiting_docs`, `waiting_notes`, `confirm`
- Данные сохраняются в FSMContext → после подтверждения записываются в БД

### 7.3 Хранение файлов <!-- [x] -->

Документы сохранять в `data/legal/{request_id}/`:
- Скриншоты, чеки — через Telegram file API → скачать → сохранить
- Пути записать в legal_requests.purchase_documents (JSON)

**Критерий завершения:** Полный цикл: назначение → ввод данных → READY_FOR_LAWSUIT.

---

## ФАЗА 8: Админ-панель (расширение Telegram бота)

- [x] **Фаза завершена**

**Цель:** Управление воронкой через Telegram.

### 8.1 Новые команды (`bot/admin_handlers.py`) <!-- [x] -->

| Команда | Действие |
|---------|----------|
| `/workflows` | Список активных воронок с пагинацией |
| `/workflow <id>` | Детали воронки: статус, товары, таймлайн, переписка |
| `/warn <seller_id>` | Ручная отправка предупреждения |
| `/legal_requests` | Список юрзаявок |
| `/legal <id>` | Детали юрзаявки |
| `/export <request_id>` | Отправить ZIP-архив в чат |
| `/assign_purchase <id>` | Назначить контрольную закупку |
| `/purchase_done <id>` | Начать ввод данных закупки |
| `/close_workflow <id>` | Ручное закрытие воронки |

### 8.2 Inline-кнопки в карточках <!-- [x] -->

В уведомлениях о новых продавцах добавить кнопки:
- `[📋 Воронка]` — открыть карточку workflow
- `[⚠️ Отправить WARN1]` — ручная отправка (если автоматика не сработала)

В карточке воронки:
- `[📤 WARN2]` — ручной WARN2
- `[⚖️ Юрзаявка]` — ручная эскалация
- `[✅ Закрыть]` — продавец отсоединился

### 8.3 Уведомления команде <!-- [x] -->

Расширить `NotificationService`:
- `notify_warn1_sent(workflow)` — «⚠️ WARN1 отправлен: {магазин}»
- `notify_warn2_sent(workflow)` — «⚠️⚠️ WARN2 отправлен: {магазин}»
- `notify_incoming_message(seller, text)` — «💬 Ответ от {магазин}: {текст}»
- `notify_legal_request(request)` — «⚖️ Юрзаявка создана: {магазин}»
- `notify_purchase_required(request)` — «🛒 Требуется закупка: {магазин}»
- `notify_detached(workflow)` — «✅ Отсоединился: {магазин}»

**Критерий завершения:** Все действия доступны из Telegram, уведомления приходят на каждое событие.

---

## ФАЗА 9: Тестирование и безопасность

- [x] **Фаза завершена**

### 9.1 Тесты <!-- [x] -->

- Unit-тесты для каждого нового DAO-класса
- Unit-тесты для LLM-classifier (все 8 типов + edge cases, mock OpenAI)
- Unit-тесты для templates (рендеринг, подстановка)
- Unit-тесты для Green API client (mock HTTP)
- Integration-тест: полный цикл воронки (mock WhatsApp)
- Integration-тест: webhook приём и обработка
- Integration-тест: микро-скан при ALREADY_REMOVED

### 9.2 Безопасность <!-- [x] -->

- WhatsApp webhook: валидация IP-адреса отправителя (whitelist Green API)
- Все ключи/токены в .env, НЕ в коде
- Валидация входящих данных (phone, text length)
- Rate limiting на webhook endpoint
- Логи хранить ≥ 12 месяцев (настройка ротации)
- OpenAI API key — только из env, не логировать

### 9.3 Обработка ошибок <!-- [x] -->

- Ошибка отправки WhatsApp → retry 3 раза → уведомление админу
- OpenAI API недоступен → fallback на UNKNOWN, админ классифицирует вручную
- Webhook недоступен → сообщения буферизуются у Green API
- БД-ошибка → откат транзакции, лог, уведомление

---

## ФАЗА 10: Запуск и документация

- [x] **Фаза завершена**

### 10.1 Деплой <!-- [x] -->

- Обновить `requirements.txt`: + aiohttp, openai
- Обновить `.env.example` с новыми переменными (Green API, OpenAI)
- Инструкция по настройке Green API (регистрация, привязка номера через QR)
- Инструкция по настройке webhook (проброс порта / reverse proxy)

### 10.2 Документация <!-- [x] -->

- README: обновить описание функциональности
- Инструкция для Улшат: как вводить данные закупки
- Инструкция для юристов: как получать заявки

---

## Порядок реализации и зависимости

```
ФАЗА 1 (БД)
    ↓
ФАЗА 2 (WhatsApp клиент)  ←→  ФАЗА 3 (Шаблоны + классификатор)
    ↓                              ↓
    └──────────→ ФАЗА 4 (Движок воронки) ←──┘
                      ↓
                 ФАЗА 5 (Планировщик)
                      ↓
              ФАЗА 6 (Юрзаявки + экспорт)
                      ↓
              ФАЗА 7 (Контрольная закупка)
                      ↓
              ФАЗА 8 (Админ-панель)
                      ↓
              ФАЗА 9 (Тестирование)
                      ↓
              ФАЗА 10 (Запуск)
```

**Фазы 2 и 3 можно делать параллельно** — они не зависят друг от друга.

---

## Что НЕ входит в план

- Web-интерфейс (админ-панель только через Telegram)
- Интеграция с CRM/Bitrix24 (пока юрзаявки через Telegram + экспорт)
- LLM для генерации ответов (LLM используется ТОЛЬКО для классификации; ответы — шаблонные)
- Снапшоты страниц (слишком сложно, лог переписки достаточен для суда)
- Роли доступа (пока только admin/non-admin, как сейчас)
- Anti-ban Kaspi (TLS-fingerprinting, мобильная эмуляция) — не нужно при текущих объёмах

Эти вещи можно добавить позже как отдельные фазы, если клиент запросит.
