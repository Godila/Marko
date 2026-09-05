"""Парсер выгрузки xlsx для деклараций НК: первый лист, строка 1 — заголовки.

Нераспознанные колонки игнорируются, пустые строки пропускаются, все значения
нормализуются в str().strip() (дата-ячейки — date/datetime от openpyxl —
в ISO-дату 'YYYY-MM-DD', иначе str(datetime) «2026-01-01 00:00:00» валит
DATE_RE валидатора). apply_defaults подставляет платформенные дефолты в
пустые ключи (techreg — всегда) и возвращает новые dict'ы, не мутируя вход.
"""
import io
from datetime import date, datetime

import openpyxl

COLUMNS = {  # RU-заголовок (casefold, strip) → ключ строки
    "тнвэд": "tnved", "наименование": "name", "вид товара": "product_type",
    "цвет": "color", "состав": "composition", "размер": "size",
    "модель/артикул": "model", "артикул": "article", "бренд": "brand",
    "пол": "target_gender", "размерная система": "size_system",
    "декларация": "declaration_number", "категория": "category_hint", "gtin": "gtin",
    "дата декларации": "declaration_date",
}
REQUIRED_ROW_KEYS = ["article", "tnved", "name", "product_type", "color", "composition", "size"]

DEFAULTED_KEYS = ["brand", "target_gender", "size_system", "declaration_number",
                  "declaration_date", "producer", "country"]


def _cell(value) -> str:
    if isinstance(value, datetime):  # datetime — подкласс date, проверяем первым
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return "" if value is None else str(value).strip()


def parse_xlsx(data: bytes) -> list[dict]:
    """bytes xlsx → список строк-словарей по COLUMNS; пустые строки пропущены."""
    ws = openpyxl.load_workbook(io.BytesIO(data)).worksheets[0]
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter, None) or ()
    keys = [COLUMNS.get(_cell(h).casefold()) for h in header]
    rows = []
    for values in rows_iter:
        row = {k: _cell(v) for k, v in zip(keys, values) if k}
        if any(row.values()):
            rows.append(row)
    return rows


def apply_defaults(rows: list[dict], defaults: dict) -> list[dict]:
    """Пустые/отсутствующие DEFAULTED_KEYS — из defaults; techreg — всегда.

    Возвращает новые словари, входные rows не мутируются.
    """
    out = []
    for row in rows:
        r = dict(row)
        for key in DEFAULTED_KEYS:
            if not r.get(key):
                r[key] = defaults.get(key, "")
        r["techreg"] = defaults.get("techreg", "")  # колонки в шаблоне нет
        out.append(r)
    return out
