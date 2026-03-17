"""
Утилиты нормализации телефонных номеров для WhatsApp
Формат Казахстана: +7 (XXX) XXX-XX-XX → 7XXXXXXXXXX
"""
import re
import logging

logger = logging.getLogger(__name__)

# Паттерн для казахстанских номеров: 7 + 10 цифр
_KZ_PHONE_PATTERN = re.compile(r"^7[0-9]{10}$")

# Допустимые префиксы мобильных операторов Казахстана
_KZ_MOBILE_PREFIXES = {
    "700", "701", "702", "705", "706", "707", "708", "747", "750",
    "751", "760", "761", "762", "763", "764", "771", "775", "776",
    "777", "778",
}


def normalize_phone(phone: str) -> str:
    """
    Нормализация телефонного номера в формат 7XXXXXXXXXX (11 цифр).

    Примеры:
        +7 (701) 754-51-09 → 77017545109
        8 701 754 51 09    → 77017545109
        77017545109        → 77017545109
        +77017545109       → 77017545109

    Возвращает пустую строку если нормализация невозможна.
    """
    if not phone:
        return ""

    # Убираем все нецифровые символы
    digits = re.sub(r"\D", "", phone)

    # Обработка различных форматов
    if len(digits) == 11:
        if digits.startswith("8"):
            # 87017545109 → 77017545109
            digits = "7" + digits[1:]
        elif digits.startswith("7"):
            pass  # уже в нужном формате
        else:
            return ""
    elif len(digits) == 10:
        # 7017545109 → 77017545109
        digits = "7" + digits
    elif len(digits) == 12 and digits.startswith("87"):
        # Иногда встречается +87... — нормализуем
        digits = "7" + digits[2:]
    else:
        return ""

    # Финальная валидация
    if _KZ_PHONE_PATTERN.match(digits):
        return digits

    return ""


def is_valid_kz_phone(phone: str) -> bool:
    """
    Проверка что номер — валидный казахстанский мобильный.
    Принимает как сырой, так и нормализованный номер.
    """
    normalized = normalize_phone(phone)
    if not normalized:
        return False

    # Проверяем префикс оператора (первые 3 цифры после 7)
    prefix = normalized[1:4]
    return prefix in _KZ_MOBILE_PREFIXES


def phone_to_chat_id(phone: str) -> str:
    """
    Конвертация телефона в формат Green API chat ID.

    Примеры:
        +7 (701) 754-51-09 → 77017545109@c.us
        77017545109        → 77017545109@c.us

    Raises ValueError при невалидном номере.
    """
    normalized = normalize_phone(phone)
    if not normalized:
        raise ValueError(f"Невалидный номер телефона: {phone}")

    return f"{normalized}@c.us"


def chat_id_to_phone(chat_id: str) -> str:
    """
    Извлечение нормализованного номера из Green API chat ID.

    Примеры:
        77017545109@c.us → 77017545109

    Возвращает пустую строку если формат невалидный.
    """
    if not chat_id or "@" not in chat_id:
        return ""

    phone_part = chat_id.split("@")[0]
    return normalize_phone(phone_part)
