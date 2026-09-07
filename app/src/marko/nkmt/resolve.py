"""Резолвер строк выгрузки: дефолты → правила РД → справочник брендов.

Конвейер импорта, превью и «примерки» — один resolve_pipeline; расхождение
семантики невозможно. Приоритет «файл > правило > справочник бренда > дефолт»
держится инвариантом _fill: слот перебивается только в состоянии 'default'
(файловые значения apply_defaults не трогает, правило/справочник штампуют
'rule'/'dict'). Условия правил сравниваются со значениями строки ПОСЛЕ
дефолтов («эффективное значение строки»). Дата при подстановке декларации
берётся из записи реестра, на которую указывает правило/справочник, — пара
«номер-дата» всегда консистентна. Пишет только кэши справочников
(kv, brand_cache) — карточки/батчи не трогает.
"""
from marko.nkmt.dicts import get_brands, get_defaults, get_rules
from marko.nkmt.parse import DEFAULTED_KEYS, apply_defaults, parse_xlsx
from marko.nkmt.validate import validate_rows


def _fill(row: dict, s: dict, key: str, value: str, origin: str) -> bool:
    """Единственная точка инварианта приоритета: слот перебивается только в
    состоянии 'default' и только непустым значением. True — значение записано."""
    if value and s[key] == "default":
        row[key], s[key] = value, origin
        return True
    return False


def _stamp_decl(row: dict, s: dict, num: str, date: str, origin: str) -> bool:
    """Декларация подставляется только парой номер+дата из одной записи
    реестра — инвариант консистентности пары в одной точке."""
    if _fill(row, s, "declaration_number", num, origin):
        row["declaration_date"], s["declaration_date"] = date, origin
        return True
    return False


def sources(raw: list[dict]) -> list[dict]:
    """Provenance значений строки: key (DEFAULTED_KEYS) → 'file' | 'default'."""
    return [{k: ("file" if str(r.get(k, "")).strip() else "default")
             for k in DEFAULTED_KEYS} for r in raw]


def match_rule(rules: list[dict], brand: str, product_type: str) -> dict | None:
    """Подошедшее правило: все непустые условия совпали; из подошедших — больше
    непустых условий, ничья → больший id. Бренд — casefold (как brand_cache),
    вид товара — точно (как preset-проверка). Никого — None."""
    best, best_key = None, (-1, -1)
    for r in rules:
        score = 0
        if r["brand"]:
            if r["brand"].casefold() != brand.casefold():
                continue
            score += 1
        if r["product_type"]:
            if r["product_type"] != product_type:
                continue
            score += 1
        if (score, r["id"]) > best_key:
            best, best_key = r, (score, r["id"])
    return best


def apply_rules(rules: list[dict], rows: list[dict],
                src: list[dict]) -> tuple[list[dict], list[dict], list[int | None]]:
    """Правила поверх дефолтов; входные rows/src не мутируются.

    Правило перебивает только дефолт-подстановку: producer — если задан
    правилом; декларация — парой номер + дата из записи правила.
    Возвращает (новые rows, новый src, id сработавшего правила на строку).
    """
    out_rows, out_src, matched = [], [], []
    for row, s in zip(rows, src):
        r, s2 = dict(row), dict(s)
        hit = match_rule(rules, str(row.get("brand", "")),
                         str(row.get("product_type", "")))
        if hit:
            _stamp_decl(r, s2, hit["declaration_number"],
                        hit.get("declaration_date", ""), "rule")
            _fill(r, s2, "producer", hit["producer"], "rule")
        out_rows.append(r)
        out_src.append(s2)
        matched.append(hit["id"] if hit else None)
    return out_rows, out_src, matched


def match_brand(brands: list[dict], brand: str) -> dict | None:
    """Запись справочника по имени casefold (уникальность гарантирует API);
    пустой бренд не матчится никогда."""
    low = brand.casefold()
    return next((b for b in brands if b["name"].casefold() == low), None)


def apply_brand_dict(brands: list[dict], rows: list[dict],
                     src: list[dict]) -> tuple[list[dict], list[dict]]:
    """Справочник брендов ПОСЛЕ правил: только слоты src == 'default' (правило
    уже штампует 'rule' — этим кодируется «правило бьёт справочник», без чисел
    приоритета). Декларация — парой номер+дата из записи реестра, producer —
    если задан. Входные rows/src не мутируются."""
    out_rows, out_src = [], []
    for row, s in zip(rows, src):
        r, s2 = dict(row), dict(s)
        hit = match_brand(brands, str(row.get("brand", "")))
        if hit:
            _stamp_decl(r, s2, hit["declaration_number"],
                        hit["declaration_date"], "dict")
            _fill(r, s2, "producer", hit["producer"], "dict")
        out_rows.append(r)
        out_src.append(s2)
    return out_rows, out_src


def resolve_pipeline(raw: list[dict], defaults: dict, rules: list[dict],
                     brands: list[dict]) -> tuple[list[dict], list[dict], list[int | None]]:
    """Дефолты → правила → справочник брендов: единая последовательность стадий
    для импорта, превью и «примерки»."""
    rows = apply_defaults(raw, defaults)
    src = sources(raw)
    rows, src, matched = apply_rules(rules, rows, src)
    rows, src = apply_brand_dict(brands, rows, src)
    return rows, src, matched


def resolve_rows(db, client, token: str, data: bytes) -> dict:
    """bytes xlsx → {"validated": [ValidatedRow], "src", "matched"}.

    Списки параллельны (запись на строку файла). Из валидатора ошибки не
    выбрасываются; побочные записи — только кэши справочников (kv, brand_cache).
    """
    raw = parse_xlsx(data)
    rows, src, matched = resolve_pipeline(raw, get_defaults(db),
                                          get_rules(db), get_brands(db))
    return {"validated": validate_rows(db, client, token, rows),
            "src": src, "matched": matched}


def resolve_fields(brand: str, product_type: str, defaults: dict,
                   rules: list[dict], brands: list[dict]) -> dict:
    """«Примерка» без файла: как если бы в файле была строка, заполненная
    только брендом и видом товара. → {поле: {value, src}} по подставляемым
    полям + techreg (он по построению только из дефолтов)."""
    raw = [{"brand": brand.strip(), "product_type": product_type.strip()}]
    rows, src, _ = resolve_pipeline(raw, defaults, rules, brands)
    keys = {k: {"value": rows[0].get(k, ""), "src": src[0][k]}
            for k in DEFAULTED_KEYS}
    keys["techreg"] = {"value": rows[0].get("techreg", ""), "src": "default"}
    return keys
