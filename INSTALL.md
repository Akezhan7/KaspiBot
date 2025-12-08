# Установка бота на новый компьютер

## Что установить:

### 1. Python 3.11+
- Скачать: https://www.python.org/downloads/
- При установке отметить "Add Python to PATH"

### 2. Git (опционально, для клонирования)
- Скачать: https://git-scm.com/downloads

## Быстрая установка:

```powershell
# 1. Скопировать папку проекта на новый комп

# 2. Открыть PowerShell в папке проекта

# 3. Установить зависимости
pip install -r requirements.txt

# 4. Создать .env файл (скопировать с текущего компа)
# Нужны: TELEGRAM_BOT_TOKEN, ADMIN_USER_IDS, PROXY_URL, PROXY_CHANGE_API

# 5. Запустить
python main.py
```

## Содержимое .env:
```
TELEGRAM_BOT_TOKEN=ваш_токен_бота
ADMIN_USER_IDS=ваш_telegram_id
PROXY_URL=http://user:pass@host:port
PROXY_CHANGE_API=https://...
SCAN_INTERVAL_HOURS=12
```

## Проверка:
- База данных создастся автоматически в `data/`
- Логи будут в `logs/`
- Бот отправит уведомление о запуске

## Автозапуск (опционально):
Создать bat-файл `start_bot.bat`:
```bat
@echo off
cd /d "D:\путь\к\KaspiBot"
python main.py
pause
```
