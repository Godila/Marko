"""Движок правил РД: match_rule (приоритет условий) и apply_rules (файл > правило > дефолт)."""
from marko.nkmt.parse import apply_defaults
from marko.nkmt.resolve import apply_rules, match_rule, sources


def R(id, brand="", product_type="", declaration_number="Д-X",
      declaration_date="2026-05-05", producer=""):
    return {"id": id, "brand": brand, "product_type": product_type,
            "declaration_number": declaration_number,
            "declaration_date": declaration_date, "producer": producer}


def test_match_rule_precedence():
    rules = [R(1, brand="ycpb"), R(2, brand="YCPB", product_type="ФУТБОЛКА"), R(3, brand="YCPB")]
    assert match_rule(rules, "YCPB", "ФУТБОЛКА")["id"] == 2      # 2 условия бьют 1
    assert match_rule(rules, "YCPB", "ШАПКА")["id"] == 3         # вид не совпал; ничья → больший id
    assert match_rule(rules, "ДРУГОЙ", "ФУТБОЛКА") is None       # бренд не совпал нигде
    assert match_rule([R(5, product_type="ШАПКА")], "YCPB", "шапка") is None  # вид — точно, не casefold


def test_apply_rules_priority_and_prov():
    rows = [{"article": "A", "brand": "", "declaration_number": "", "declaration_date": "", "producer": ""},
            {"article": "B", "brand": "ДРУГОЙ", "declaration_number": "Д-СВОЯ",
             "declaration_date": "2026-01-01", "producer": ""}]
    raw = [dict(r) for r in rows]
    filled = apply_defaults(rows, {"brand": "YCPB"})
    src = sources(raw)
    rules = [R(7, brand="YCPB", declaration_number="Д-ПРАВИЛО", producer="ИП X")]
    out, src2, matched = apply_rules(rules, filled, src)
    # A: бренд пришёл из дефолта → правило перебивает дефолт-подстановку
    assert out[0]["declaration_number"] == "Д-ПРАВИЛО"
    assert out[0]["declaration_date"] == "2026-05-05"   # дата из записи правила
    assert out[0]["producer"] == "ИП X" and matched[0] == 7
    assert src2[0]["declaration_number"] == "rule" and src2[0]["brand"] == "default"
    # B: бренд и декларация из файла → правило не матчит бренд, файл цел
    assert out[1]["declaration_number"] == "Д-СВОЯ" and src2[1]["declaration_number"] == "file"
    assert out[1]["producer"] == "" and matched[1] is None
    # входные rows/src не мутированы
    assert rows[0]["declaration_number"] == "" and src[0]["declaration_number"] == "default"


def test_apply_rules_without_producer_keeps_value():
    rows = [{"brand": "YCPB", "declaration_number": "", "declaration_date": "", "producer": "СТАРЫЙ"}]
    src = sources([dict(rows[0])])
    out, src2, matched = apply_rules(
        [R(1, brand="YCPB", declaration_number="Д-1")], rows, src)
    assert out[0]["declaration_number"] == "Д-1" and matched[0] == 1
    assert out[0]["producer"] == "СТАРЫЙ" and src2[0]["producer"] == "file"  # правило без producer не трогает
