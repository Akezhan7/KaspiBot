"""Минимальный XLSX-writer без внешних зависимостей.

Достаточен для экспорта аналитики /api/products/export.xlsx:
поддерживает один лист с заголовком и строками значений (str/int/float).
Не реализует стилей, формул, мерджей — просто плоская таблица.

Файл XLSX — это zip-архив с XML внутри. Структура:
    [Content_Types].xml
    _rels/.rels
    xl/workbook.xml
    xl/_rels/workbook.xml.rels
    xl/worksheets/sheet1.xml
    xl/sharedStrings.xml

Все строки кладём в sharedStrings и ссылаемся на них по индексу — это сильно
сжимает файл и совпадает с форматом Kaspi.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime
from typing import Iterable, Sequence
from xml.sax.saxutils import escape


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>"""


def _workbook_xml(sheet_name: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets>'
        f'<sheet name="{escape(sheet_name)[:31]}" sheetId="1" r:id="rId1"/>'
        '</sheets>'
        '</workbook>'
    )


def _column_letter(idx_zero_based: int) -> str:
    """Преобразовать 0-based индекс в буквенное имя колонки (0->A, 25->Z, 26->AA)."""
    n = idx_zero_based + 1
    out = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def _is_number(value: object) -> bool:
    """True если значение можно записать как число XLSX (int/float, но не bool)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        # Исключаем NaN/inf — они некорректны в XLSX
        try:
            return value == value and float("-inf") < float(value) < float("inf")
        except (TypeError, ValueError):
            return False
    return False


def write_xlsx(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    sheet_name: str = "Sheet1",
) -> bytes:
    """Сериализовать таблицу в XLSX-байты.

    Args:
        headers: список заголовков (первая строка XLSX).
        rows: итератор по строкам (списки значений).
        sheet_name: название листа (макс. 31 символ — ограничение Excel).

    Числа (int/float, кроме bool/NaN) пишутся как numeric, всё остальное —
    как inline-shared-string. Date/datetime сериализуются в ISO-формат.
    """
    shared: list[str] = []
    shared_index: dict[str, int] = {}

    def _share(text: str) -> int:
        idx = shared_index.get(text)
        if idx is not None:
            return idx
        idx = len(shared)
        shared.append(text)
        shared_index[text] = idx
        return idx

    sheet_rows: list[str] = []
    all_rows: list[Sequence[object]] = [list(headers)] + [list(r) for r in rows]

    for row_idx, row in enumerate(all_rows, start=1):
        cells: list[str] = []
        for col_idx, value in enumerate(row):
            ref = f"{_column_letter(col_idx)}{row_idx}"
            if value is None or value == "":
                continue
            if _is_number(value):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                if isinstance(value, (date, datetime)):
                    text = value.isoformat()
                else:
                    text = str(value)
                idx = _share(text)
                cells.append(f'<c r="{ref}" t="s"><v>{idx}</v></c>')
        sheet_rows.append(f'<row r="{row_idx}">{"".join(cells)}</row>')

    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData>'
        '</worksheet>'
    )

    shared_items = "".join(
        f'<si><t xml:space="preserve">{escape(s)}</t></si>' for s in shared
    )
    shared_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        f' count="{len(shared)}" uniqueCount="{len(shared)}">'
        f'{shared_items}</sst>'
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _ROOT_RELS)
        zf.writestr("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS)
        zf.writestr("xl/workbook.xml", _workbook_xml(sheet_name))
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        zf.writestr("xl/sharedStrings.xml", shared_xml)
    return buffer.getvalue()
