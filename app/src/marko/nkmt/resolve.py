"""Резолвер строк выгрузки: дефолты → правила РД, с provenance подстановок.

Конвейер импорта и превью: parse_xlsx → apply_defaults → apply_rules →
validate_rows; preview и import зовут один resolve_rows — расхождение
семантики невозможно. Приоритет «файл > правило > глоб. дефолт» держится
дважды: apply_defaults не трогает непустые (файловые) значения, apply_rules
перебивает только дефолт-подстановку (src != 'file'). Условия правил
сравниваются со значениями строки ПОСЛЕ дефолтов (файловое значение или
дефолт — «эффективное значение строки»). Дата при rule-подстановке берётся
из записи реестра, на которую указывает правило (а не по номеру), и файловая
дата затирается — пара «номер-дата» всегда консистентна.
"""
from marko.nkmt.dicts import get_defaults, get_rules
from marko.nkmt.parse import DEFAULTED_KEYS, apply_defaults, parse_xlsx
from marko.nkmt.validate import validate_rows


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

    Правило перебивает только дефолт-подстановку (src == 'default'):
    declaration_number ← номер декларации правила (дата — из записи правила,
    файловая затирается — пара «номер-дата» всегда консистентна),
    producer — если задан правилом.
    Возвращает (новые rows, новый src, id сработавшего правила на строку).
    """
    out_rows, out_src, matched = [], [], []
    for row, s in zip(rows, src):
        r, s2 = dict(row), dict(s)
        hit = match_rule(rules, str(row.get("brand", "")),
                         str(row.get("product_type", "")))
        if hit:
            if s2["declaration_number"] != "file":
                r["declaration_number"] = hit["declaration_number"]
                r["declaration_date"] = hit.get("declaration_date", "")
                s2["declaration_number"] = "rule"
                s2["declaration_date"] = "rule"
            if hit["producer"] and s2["producer"] != "file":
                r["producer"] = hit["producer"]
                s2["producer"] = "rule"
        out_rows.append(r)
        out_src.append(s2)
        matched.append(hit["id"] if hit else None)
    return out_rows, out_src, matched


def resolve_rows(db, client, token: str, data: bytes) -> dict:
    """bytes xlsx → {"validated": [ValidatedRow], "src", "matched"}.

    Списки параллельны (запись на строку файла). Из валидатора ошибки не
    выбрасываются; побочные записи — только кэши справочников (kv, brand_cache).
    """
    raw = parse_xlsx(data)
    rows = apply_defaults(raw, get_defaults(db))
    src = sources(raw)
    rules = get_rules(db)
    rows, src, matched = apply_rules(rules, rows, src)
    return {"validated": validate_rows(db, client, token, rows),
            "src": src, "matched": matched}
