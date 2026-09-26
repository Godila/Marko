"""Парсер выгрузки xlsx для деклараций НК: первый лист, строка 1 — заголовки.

Нераспознанные колонки игнорируются (порядок колонок в файле не важен —
заголовки словарные), пустые строки пропускаются, все значения
нормализуются в str().strip() (дата-ячейки — date/datetime от openpyxl —
в ISO-дату 'YYYY-MM-DD', иначе str(datetime) «2026-01-01 00:00:00» валит
DATE_RE валидатора). apply_defaults подставляет платформенные дефолты в
пустые ключи (techreg — всегда) и возвращает новые dict'ы, не мутируя вход.
"""
import io
import re
from dataclasses import dataclass
from datetime import date, datetime

import openpyxl


@dataclass(frozen=True)
class ColumnSpec:
    """Колонка выгрузки: единый источник для parse_xlsx, шаблона и «Инструкции».

    Порядок SPEC = порядок колонок в шаблоне (группы: идентификация →
    классификация → атрибуты → документы → производство).
    """
    title: str
    key: str
    required: bool = False
    defaultable: bool = False   # участвует в подстановках (дефолты/правила)
    hint: str = ""


SPEC: list[ColumnSpec] = [
    ColumnSpec("Артикул", "article", required=True,
               hint="уникальный ключ; повторный импорт обновляет карточку"),
    ColumnSpec("Наименование", "name", required=True),
    ColumnSpec("Бренд", "brand", defaultable=True,
               hint="точное имя ТМ из НК; иначе правило РД, затем дефолт"),
    ColumnSpec("Модель/артикул", "model", hint="пусто — подставится артикул"),
    ColumnSpec("ТНВЭД", "tnved", required=True,
               hint="ровно 10 цифр; пусто — можно подставить правилом РД по виду товара"),
    ColumnSpec("Вид товара", "product_type", required=True, defaultable=True,
               hint="точное значение из справочника НК; пусто — подставится дефолт; участвует в правилах РД"),
    ColumnSpec("Категория", "category_hint",
               hint="подсказка при неоднозначной категории НК"),
    ColumnSpec("Цвет", "color", required=True),
    ColumnSpec("Состав", "composition", required=True),
    ColumnSpec("Размер", "size", required=True, defaultable=True,
               hint="например «M», «one size»; пусто — правило РД; у части ТНВЭД есть "
                    "справочник значений (шапки: 46–62) — предпросмотр предупредит, если вне"),
    ColumnSpec("Размерная система", "size_system", defaultable=True,
               hint="справочник: МЕЖДУНАРОДНЫЙ, ЕВРОПА, РОССИЯ…; головные уборы — ОБХВАТ ГОЛОВЫ"),
    ColumnSpec("Пол", "target_gender", defaultable=True,
               hint="справочник ЧЗ: ЖЕНСКИЙ, МУЖСКОЙ, БЕЗ УКАЗАНИЯ ПОЛА, "
                    "УНИВЕРСАЛЬНЫЙ (УНИСЕКС); синонимы «унисекс»/«универсальный» понимаются"),
    ColumnSpec("Декларация", "declaration_number", defaultable=True,
               hint="номер из реестра; иначе правило РД, затем дефолт"),
    ColumnSpec("Дата декларации", "declaration_date", defaultable=True,
               hint="ГГГГ-ММ-ДД; пустая — дата из реестра"),
    ColumnSpec("GTIN", "gtin", hint="14 цифр; пустой — сгенерируется при подаче фида"),
    ColumnSpec("Производитель", "producer", defaultable=True,
               hint="можно пусто — подставится правило РД или дефолт"),
    ColumnSpec("Страна производства", "country", defaultable=True,
               hint="код страны ISO («RU»); можно пусто — подставится дефолт"),
]

COLUMNS = {s.title.casefold(): s.key for s in SPEC if s.title}
REQUIRED_ROW_KEYS = [s.key for s in SPEC if s.required]
DEFAULTED_KEYS = [s.key for s in SPEC if s.defaultable]

# Поля, которые правило РД может подставить сверх декларации/производителя
# (единственный источник whitelist: роут правил и apply_rules). ТН ВЭД —
# маппинг «изделие → код» для выгрузок 1С без ТН ВЭД. Идентификация
# (article/name/gtin/category_hint), условия матчинга (brand,
# product_type), пара декларации (реестр), techreg (системный) и producer
# (отдельная колонка правила) сюда не входят.
RULE_FIELDS = ["tnved", "size", "color", "composition", "model",
               "target_gender", "size_system", "country"]
# Ключи provenance-карты: дефолтуемые + правила-поля, без дублей, порядок стабилен
PROV_KEYS = list(dict.fromkeys(DEFAULTED_KEYS + RULE_FIELDS))


def _cell(value) -> str:
    if isinstance(value, datetime):  # datetime — подкласс date, проверяем первым
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return "" if value is None else str(value).strip()


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


# --- шаблон наборов: отдельная выгрузка, лёгкая (у карточки набора нет вида/
# размера/цвета/декларации — состав набора лёгпрома = 6 атрибутов, live 22.09) ---
SPEC_SETS: list[ColumnSpec] = [
    ColumnSpec("Артикул", "article", required=True,
               hint="ключ идемпотентности: повторный импорт обновит набор, не дублируя"),
    ColumnSpec("Наименование", "name",
               hint="пусто — соберём из имён компонентов «Набор: X + Y»"),
    ColumnSpec("Бренд", "brand", hint="пусто — дефолтный бренд консоли"),
    ColumnSpec("ТН ВЭД набора", "tnved",
               hint="10 цифр; пусто — возьмём ТН ВЭД первого компонента"),
    ColumnSpec("GTIN набора", "gtin",
               hint="пустой — сгенерируется при подаче фида"),
    ColumnSpec("Компоненты", "components",
               hint="через «;»: артикул или GTIN с количеством после ×/x "
                    "(умолчание 1), например «AB-1001; 04630562322355x2». "
                    "Либо эта колонка, либо «Кол-во предметов»"),
    ColumnSpec("Кол-во предметов", "count",
               hint="для набора без привязки GTIN (только количество; live: "
                    "лёгпром допускает набор-«количество» без set_gtins)"),
    ColumnSpec("Состав (немаркируемые)", "composition",
               hint="текстом, что ещё входит в набор без кодов — уйдёт в атрибут «Состав набора»"),
]
SETS_COLUMNS = {s.title.casefold(): s.key for s in SPEC_SETS if s.title}


def parse_xlsx(data: bytes, columns: dict[str, str] | None = None) -> list[dict]:
    """bytes xlsx → список строк-словарей по COLUMNS (или SETS_COLUMNS);
    пустые строки пропущены."""
    cols = columns if columns is not None else COLUMNS
    ws = openpyxl.load_workbook(io.BytesIO(data)).worksheets[0]
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter, None) or ()
    keys = [cols.get(_cell(h).casefold()) for h in header]
    rows = []
    for values in rows_iter:
        row = {k: _cell(v) for k, v in zip(keys, values) if k}
        if any(row.values()):
            rows.append(row)
    return rows


_QTY_RE = re.compile(r"^(.+?)[×xX*]([0-9]+)$")


def parse_components(cell: str, known_article=None) -> list[dict]:
    """Ячейка «Компоненты» → [{ref, quantity}]: «AB-1; 0463...x2».

    Разделитель «;», количество — хвост «×/x/X/*N» у элемента (умолчание 1).
    known_article(ref) → bool (артикул есть в нашем каталоге): известный
    артикул целиком не режется количеством — «HX2» остаётся «HX2», а не
    «H×2». Резолв ссылок (наша карточка или внешний GTIN) — в sets.build_set_row.
    """
    items: list[dict] = []
    for chunk in str(cell or "").split(";"):
        ref = chunk.strip()
        if not ref:
            continue
        quantity = 1
        if not (known_article and known_article(ref)):
            m = _QTY_RE.fullmatch(ref)
            if m:
                ref, quantity = m.group(1).strip(), int(m.group(2))
        items.append({"ref": ref, "quantity": quantity})
    return items
