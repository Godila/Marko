"""Резолвер строк выгрузки: дефолты → правила РД, с provenance подстановок.

Конвейер импорта, превью и «проверки подстановок» — один resolve_pipeline;
расхождение семантики невозможно. Приоритет «файл > правило > дефолт» держится
инвариантом _fill: слот перебивается только в состоянии 'default' (файловые
значения apply_defaults не трогает, правило штампует 'rule'). Условия правил
сравниваются со значениями строки ПОСЛЕ дефолтов («эффективное значение
строки»); вид товара матчится, если входит в список правила. Дата при
подстановке берётся из записи реестра, на которую указывает правило, — пара
«номер-дата» всегда консистентна. Пишет только кэши справочников
(kv, brand_cache) — карточки/батчи не трогает.
"""
from marko.nkmt.dicts import get_defaults, get_rules
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
    """Подошедшее правило: бренд (casefold, как brand_cache) и вид товара
    (точно, как preset-проверка; должен входить в список правила); пустое
    условие = «любой». Из подошедших — больше непустых условий, ничья →
    больший id. Никого — None."""
    best, best_key = None, (-1, -1)
    for r in rules:
        score = 0
        if r["brand"]:
            if r["brand"].casefold() != brand.casefold():
                continue
            score += 1
        if r["product_types"]:
            if product_type not in r["product_types"]:
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


def resolve_pipeline(raw: list[dict], defaults: dict,
                     rules: list[dict]) -> tuple[list[dict], list[dict], list[int | None]]:
    """Дефолты → правила: единая последовательность стадий для импорта,
    превью и «проверки подстановок»."""
    rows = apply_defaults(raw, defaults)
    src = sources(raw)
    return apply_rules(rules, rows, src)


def resolve_rows(db, client, token: str, data: bytes) -> dict:
    """bytes xlsx → {"validated": [ValidatedRow], "src", "matched"}.

    Списки параллельны (запись на строку файла). Из валидатора ошибки не
    выбрасываются; побочные записи — только кэши справочников (kv, brand_cache).
    """
    raw = parse_xlsx(data)
    rows, src, matched = resolve_pipeline(raw, get_defaults(db), get_rules(db))
    return {"validated": validate_rows(db, client, token, rows),
            "src": src, "matched": matched}


def resolve_fields(brand: str, product_type: str, defaults: dict,
                   rules: list[dict]) -> dict:
    """«Проверка подстановок» без файла: как если бы в файле была строка,
    заполненная только брендом и видом товара. → {поле: {value, src}} по
    подставляемым полям + techreg (он по построению только из дефолтов)."""
    raw = [{"brand": brand.strip(), "product_type": product_type.strip()}]
    rows, src, _ = resolve_pipeline(raw, defaults, rules)
    keys = {k: {"value": rows[0].get(k, ""), "src": src[0][k]}
            for k in DEFAULTED_KEYS}
    keys["techreg"] = {"value": rows[0].get("techreg", ""), "src": "default"}
    return keys
