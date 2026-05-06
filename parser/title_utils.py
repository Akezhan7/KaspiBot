"""
Утилиты для нормализации и валидации названий товаров.
"""
import re
from typing import Any, Optional

# Шаблоны, которые явно не считаются валидным названием товара.
_INVALID_TITLE_PATTERNS = [
    re.compile(r'^товар\s*\d+$', re.IGNORECASE),
    re.compile(r'^(?:sku|арт\.?|артикул)[:\s-]*\d{5,20}$', re.IGNORECASE),
    re.compile(r'^\d{5,20}$'),
]


def clean_product_title(raw_title: Any) -> Optional[str]:
    """Вернуть нормализованное название товара или None, если оно невалидно."""
    if raw_title is None:
        return None

    title = str(raw_title).strip()
    if not title:
        return None

    if title.lower() == 'без названия':
        return None

    normalized = title.lower()
    for pattern in _INVALID_TITLE_PATTERNS:
        if pattern.match(normalized):
            return None

    if len(title) < 3:
        return None

    return title
