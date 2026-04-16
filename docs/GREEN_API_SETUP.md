# Настройка Green API (WhatsApp)

Бот использует Green API для отправки сообщений продавцам через WhatsApp.
Green API работает как WhatsApp Web — привязывается к реальному номеру телефона.

---

## 1. Регистрация

1. Перейти на [green-api.com](https://green-api.com/)
2. Создать аккаунт
3. Создать **инстанс** (Instance) в личном кабинете
4. Записать:
   - **Instance ID** (например, `1101234567`)
   - **API Token** (например, `abc123def456...`)

## 2. Привязка номера

1. В личном кабинете Green API нажать **«Привязать»** рядом с инстансом
2. На экране появится **QR-код**
3. Открыть WhatsApp на телефоне:
   - **Android**: Настройки → Связанные устройства → Привязать устройство
   - **iPhone**: Настройки → Связанные устройства → Привязать устройство
4. Отсканировать QR-код камерой телефона
5. Дождаться статуса **«Авторизован»** в панели Green API

> **Важно**: Используйте отдельный номер телефона для бота, не свой личный.
> Номер должен быть казахстанский (+7...) для доверия продавцов.

## 3. Настройка .env

```env
GREEN_API_URL=https://api.green-api.com
GREEN_API_INSTANCE_ID=1101234567
GREEN_API_TOKEN=abc123def456ghi789
```

## 4. Проверка работы

После запуска бота в логах должно появиться:

```
INFO - GreenAPIClient создан: instance=1101234567
```

Если credentials не заданы:

```
WARNING - GREEN_API_INSTANCE_ID/GREEN_API_TOKEN не установлены - WhatsApp отключен
```

## 5. Настройка webhook (приём ответов)

Green API будет отправлять входящие сообщения на ваш сервер.

### Вариант A: Прямой IP (если сервер с белым IP)

1. В панели Green API → Настройки инстанса → Webhook URL:
   ```
   http://ВАШ_IP:8443/webhook
   ```
2. В `.env`:
   ```env
   WHATSAPP_WEBHOOK_HOST=0.0.0.0
   WHATSAPP_WEBHOOK_PORT=8443
   ```
3. Открыть порт 8443 в firewall

### Вариант B: Reverse proxy (nginx)

1. Настроить nginx:
   ```nginx
   server {
       listen 443 ssl;
       server_name whatsapp.yourdomain.com;

       ssl_certificate /path/to/cert.pem;
       ssl_certificate_key /path/to/key.pem;

       location /webhook {
           proxy_pass http://127.0.0.1:8443;
           proxy_set_header X-Real-IP $remote_addr;
       }
   }
   ```

2. В панели Green API → Webhook URL:
   ```
   https://whatsapp.yourdomain.com/webhook
   ```

3. В `.env`:
   ```env
   WHATSAPP_WEBHOOK_HOST=127.0.0.1
   WHATSAPP_WEBHOOK_PORT=8443
   ```

### Вариант C: ngrok (для тестирования)

```bash
ngrok http 8443
```

В панели Green API → Webhook URL:
```
https://xxxx-xx-xx-xx.ngrok-free.app/webhook
```

## 6. Безопасность

### IP-фильтрация webhook

Чтобы принимать запросы только от серверов Green API, укажите в `.env`:

```env
WHATSAPP_WEBHOOK_IP_WHITELIST=5.182.37.0/24
```

> Актуальные IP Green API можно узнать в их документации или поддержке.
> Если оставить пустым — принимаются запросы с любого IP.

## 7. Возможные проблемы

| Проблема                         | Решение                                        |
| -------------------------------- | ---------------------------------------------- |
| QR-код не сканируется            | Обновить WhatsApp до последней версии          |
| «Инстанс не авторизован»        | Повторить привязку через QR                    |
| Сообщения не отправляются        | Проверить баланс Green API                     |
| Webhook не получает сообщения    | Проверить доступность порта, URL в панели       |
| Ошибка 403 на webhook            | Добавить IP Green API в whitelist              |
| Номер заблокирован WhatsApp      | Использовать другой номер, снизить частоту     |
