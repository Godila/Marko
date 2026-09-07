"""Парсер выгрузки xlsx для деклараций НК: первый лист, строка 1 — заголовки.

Нераспознанные колонки игнорируются, пустые строки пропускаются, все значения
нормализуются в str().strip() (дата-ячейки — date/datetime от openpyxl —
в ISO-дату 'YYYY-MM-DD', иначе str(datetime) «2026-01-01 00:00:00» валит
DATE_RE валидатора). apply_defaults подставляет платформенные дефолты в
пустые ключи (techreg — всегда) и возвращает новые dict'ы, не мутируя вход.
"""
import io
from dataclasses import dataclass
from datetime import date, datetime

import openpyxl


@dataclass(frozen=True)
class ColumnSpec:
    """Колонка выгрузки: единый источник для parse_xlsx, шаблона и «Инструкции».

    title "" — колонки в файле нет (значение приходит только из дефолтов/правил).
    Порядок SPEC = порядок колонок в шаблоне (как в тестовой HDR-фикстуре).
    """
    title: str
    key: str
    required: bool = False
    defaultable: bool = False   # участвует в подстановках (дефолты/правила)
    hint: str = ""


SPEC: list[ColumnSpec] = [
    ColumnSpec("Артикул", "article", required=True,
               hint="уникальный ключ; повторный импорт обновляет карточку"),
    ColumnSpec("ТНВЭД", "tnved", required=True, hint="ровно 10 цифр"),
    ColumnSpec("Наименование", "name", required=True),
    ColumnSpec("Вид товара", "product_type", required=True,
               hint="точное значение из справочника НК (участвует в правилах РД)"),
    ColumnSpec("Цвет", "color", required=True),
    ColumnSpec("Состав", "composition", required=True),
    ColumnSpec("Размер", "size", required=True, hint="например «M», «one size»"),
    ColumnSpec("Модель/артикул", "model", hint="пусто — подставится артикул"),
    ColumnSpec("Бренд", "brand", defaultable=True,
               hint="точное имя ТМ из НК; иначе правило РД, затем дефолт"),
    ColumnSpec("Пол", "target_gender", defaultable=True),
    ColumnSpec("Размерная система", "size_system", defaultable=True),
    ColumnSpec("Декларация", "declaration_number", defaultable=True,
               hint="номер из реестра; иначе правило РД, затем дефолт"),
    ColumnSpec("Дата декларации", "declaration_date", defaultable=True,
               hint="ГГГГ-ММ-ДД; пустая — дата из реестра"),
    ColumnSpec("Категория", "category_hint",
               hint="подсказка при неоднозначной категории НК"),
    ColumnSpec("GTIN", "gtin", hint="14 цифр; пустой — сгенерируется при подаче фида"),
    ColumnSpec("", "producer", defaultable=True,
               hint="производитель: файл → правило РД → дефолт; колонки в файле нет"),
    ColumnSpec("", "country", defaultable=True,
               hint="код страны («RU»); колонки в файле нет"),
]

COLUMNS = {s.title.casefold(): s.key for s in SPEC if s.title}
REQUIRED_ROW_KEYS = [s.key for s in SPEC if s.required]
DEFAULTED_KEYS = [s.key for s in SPEC if s.defaultable]


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
