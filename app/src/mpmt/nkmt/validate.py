"""Валидатор строк выгрузки: разобранная строка xlsx → карточные данные НК.

Каждая строка проходит конвейер из 9 шагов против живой атрибутной модели
(dicts.attrs_model по ТН ВЭД): обязательные поля и форматы, unicode-фильтр,
preset-справочники, бренд, реестр деклараций, категория, GTIN. Ошибки всех
шагов собираются в одну строку («; »); аварийная строка не роняет пакет.
"""
import re
from typing import TypedDict

from sqlalchemy import select

from mpmt.nkmt import dicts
from mpmt.nkmt.models import Declaration
from mpmt.nkmt.parse import REQUIRED_ROW_KEYS

# кириллица U+0400-04FF, латиница, цифры, пробел, .,:;-()/+«»"%№'&!?*—
_ALLOWED_CLASS = "0-9A-Za-z\u0400-\u04FF .,:;\\-()/+«»\"%№'&!?*—"
ALLOWED_UNICODE_RE = re.compile(f"^[{_ALLOWED_CLASS}]*$")
_FORBIDDEN_RE = re.compile(f"[^{_ALLOWED_CLASS}]")

TNVED_RE = re.compile(r"\d{10}")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
GTIN_RE = re.compile(r"\d{14}")

TEXT_KEYS = ["article", "name", "product_type", "color", "composition", "size", "model",
             "brand", "target_gender", "size_system", "techreg", "country", "producer",
             "declaration_number", "category_hint"]

ATTR_PRODUCT_TYPE = 12    # Вид товара (preset_only)
ATTR_COLOR = 36           # Цвет (есть presets, но не preset_only — не блок)
ATTR_SIZE = 35            # Размер: attr_value_type = размерные системы
ATTR_TECHREG = 13836      # Номер технического регламента (preset, список)
ATTR_GENDER = 14013       # Целевой пол (preset)


class ValidatedRow(TypedDict):
    article: str
    tnved: str
    name: str
    gtin: str
    cat_id: str
    attributes: dict
    ok: bool
    error: str


def _attr_map(model: dict) -> dict:
    """attr_id → атрибут по m+r спискам модели."""
    return {a["attr_id"]: a for a in model.get("m", []) + model.get("r", [])}


def _check_preset(amap: dict, attr_id: int, value: str, errors: list) -> None:
    attr = amap.get(attr_id)
    if attr and value not in (attr.get("attr_preset") or []):
        errors.append(f"{attr['attr_name']}: значение «{value}» отсутствует в справочнике")


def _normalize_color(amap: dict, color: str) -> str:
    """Цвет не preset_only: совпадение без учёта регистра → точное написание preset;
    отсутствующий в справочнике цвет оставляем как есть (предупреждение, не блок)."""
    attr = amap.get(ATTR_COLOR)
    low = color.casefold()
    for preset in (attr.get("attr_preset") or []) if attr else []:
        if preset.casefold() == low:
            return preset
    return color


def _validate_row(db, client, token: str, row: dict, errors: list, models: dict) -> ValidatedRow:
    # 1. обязательные ключи и форматы
    for key in REQUIRED_ROW_KEYS:
        if not str(row.get(key, "")).strip():
            errors.append(f"пустое обязательное поле: {key}")
    tnved = str(row.get("tnved", ""))
    if tnved and not TNVED_RE.fullmatch(tnved):
        errors.append(f"ТНВЭД: ожидаются 10 цифр («{tnved}»)")
    declaration_number = str(row.get("declaration_number", ""))
    declaration_date = str(row.get("declaration_date", ""))
    if declaration_date and not DATE_RE.fullmatch(declaration_date):
        errors.append(f"дата декларации: ожидается ГГГГ-ММ-ДД («{declaration_date}»)")

    # 2. unicode-фильтр текстовых полей
    for key in TEXT_KEYS:
        bad = "".join(dict.fromkeys(_FORBIDDEN_RE.findall(str(row.get(key, "")))))
        if bad:
            errors.append(f"{key}: запрещённые символы: {bad}")

    # 3. атрибутная модель по ТН ВЭД (кэш на вызов)
    amap: dict = {}
    if TNVED_RE.fullmatch(tnved):
        if tnved not in models:
            try:
                models[tnved] = _attr_map(dicts.attrs_model(db, client, token, tnved))
            except Exception as e:
                errors.append(f"модель атрибутов ТНВЭД {tnved}: {e}")
        amap = models.get(tnved, {})

    # 4. preset-справочники
    product_type = str(row.get("product_type", ""))
    _check_preset(amap, ATTR_PRODUCT_TYPE, product_type, errors)
    _check_preset(amap, ATTR_TECHREG, str(row.get("techreg", "")), errors)
    _check_preset(amap, ATTR_GENDER, str(row.get("target_gender", "")), errors)
    size_system = str(row.get("size_system", ""))
    attr_size = amap.get(ATTR_SIZE)
    if attr_size and size_system not in (attr_size.get("attr_value_type") or []):
        errors.append(f"{attr_size['attr_name']}: значение «{size_system}» отсутствует в справочнике")

    # 5. цвет — нормализация к написанию preset
    color = _normalize_color(amap, str(row.get("color", "")))

    # 6. бренд: имя должно существовать в НК (resolve_brand кэширует имя→id в brand_cache);
    #    в атрибут идёт имя ТМ строкой — 2504 «Торговая марка товара» (дамп /nk/feed)
    brand = str(row.get("brand", ""))
    try:
        dicts.resolve_brand(db, client, token, brand)
    except dicts.UnknownBrand as e:
        errors.append(f"бренд не найден: {e.args[0]}")
    except Exception as e:
        errors.append(f"бренд: {e}")

    # 7. декларация: реестр nkmt.declarations; дата реестра заполняет пустую дату строки
    if not declaration_number:
        errors.append("укажите декларацию")
    elif db is not None:
        decl = db.execute(select(Declaration)
                          .where(Declaration.doc_number == declaration_number)).scalars().first()
        if decl is None:
            errors.append(f"декларация не в реестре: {declaration_number}")
        elif not declaration_date:
            declaration_date = decl.doc_date

    # 8. категория по ТН ВЭД (hint выбирает при неоднозначности)
    cat_id = ""
    try:
        cat_id = str(dicts.resolve_category(client, token, tnved, str(row.get("category_hint", ""))))
    except dicts.AmbiguousCategory as e:
        options = "; ".join(f"{c.get('cat_id')}={c.get('cat_name')}" for c in e.args[0])
        errors.append(f"категория неоднозначна, уточните подсказкой: {options}")
    except Exception as e:
        errors.append(f"категория: {e}")

    # 9. GTIN
    gtin = str(row.get("gtin", ""))
    if gtin and not GTIN_RE.fullmatch(gtin):
        errors.append(f"GTIN: ожидаются 14 цифр («{gtin}»)")

    attributes = {
        "2478": str(row.get("name", "")),           # Полное наименование товара
        "12": product_type,                          # Вид товара
        "36": color,                                 # Цвет (нормализованный)
        "2483": str(row.get("composition", "")),     # Состав
        "14013": str(row.get("target_gender", "")),  # Целевой пол
        "35": {"type": size_system, "value": str(row.get("size", ""))},
        "13914": {"type": "Модель", "value": str(row.get("model") or row.get("article") or "")},
        "13836": [str(row.get("techreg", ""))],      # списочный атрибут
        "2504": brand,                              # Товарный знак — имя ТМ строкой (дамп /nk/feed)
        "2630": str(row.get("country", "")),         # Страна производства
        "2503": str(row.get("producer", "")),        # Производитель
        "23557": {"number": declaration_number, "date": declaration_date},
    }
    return ValidatedRow(article=str(row.get("article", "")), tnved=tnved,
                        name=str(row.get("name", "")), gtin=gtin, cat_id=cat_id,
                        attributes=attributes, ok=not errors, error="; ".join(errors))


def validate_rows(db, client, token: str, rows: list[dict]) -> list[ValidatedRow]:
    """Строки выгрузки → ValidatedRow по конвейеру; ошибка строки не роняет остальные."""
    models: dict = {}  # кэш attr-карт по tnved внутри одного вызова
    result = []
    for row in rows:
        errors: list = []
        try:
            result.append(_validate_row(db, client, token, row, errors, models))
        except Exception as e:  # страховка: одна строка не должна ронять пакет
            result.append(ValidatedRow(
                article=str(row.get("article", "")), tnved=str(row.get("tnved", "")),
                name=str(row.get("name", "")), gtin=str(row.get("gtin", "")),
                cat_id="", attributes={}, ok=False,
                error="; ".join([*errors, f"необработанная ошибка: {e}"])))
    return result
