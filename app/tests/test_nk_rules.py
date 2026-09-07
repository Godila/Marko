"""Движок правил РД: match_rule (приоритет, мультивыбор видов) и apply_rules (файл > правило > дефолт)."""
from marko.nkmt.parse import apply_defaults
from marko.nkmt.resolve import apply_rules, match_rule, resolve_pipeline, sources


def R(id, brand="", product_types=None, declaration_number="Д-X",
      declaration_date="2026-05-05", producer=""):
    return {"id": id, "brand": brand, "product_types": product_types or [],
            "declaration_number": declaration_number,
            "declaration_date": declaration_date, "producer": producer}


def test_match_rule_precedence():
    rules = [R(1, brand="ycpb"), R(2, brand="YCPB", product_types=["ФУТБОЛКА", "ШАПКА"]),
             R(3, brand="YCPB")]
    assert match_rule(rules, "YCPB", "ШАПКА")["id"] == 2      # 2 условия бьют 1
    assert match_rule(rules, "YCPB", "КЕПКА")["id"] == 3      # вид вне списка; ничья → больший id
    assert match_rule(rules, "ДРУГОЙ", "ФУТБОЛКА") is None    # бренд не совпал нигде
    # мультивыбор: правило бьёт по любому виду из списка
    assert match_rule(rules, "ycpb", "ФУТБОЛКА")["id"] == 2
    # вид товара — точно, не casefold
    assert match_rule([R(5, product_types=["ШАПКА"])], "YCPB", "шапка") is None


def test_match_rule_empty_types_is_wildcard():
    """Пустой список видов = «любой вид» (роль бывшего справочника брендов)."""
    rules = [R(1, brand="Adel")]
    assert match_rule(rules, "ADEL", "ШАПКА")["id"] == 1
    assert match_rule(rules, "adel", "КЕПКА")["id"] == 1


def test_apply_rules_priority_and_prov():
    rows = [{"article": "A", "brand": "", "product_type": "ФУТБОЛКА",
             "declaration_number": "", "declaration_date": "", "producer": ""},
            {"article": "B", "brand": "ДРУГОЙ", "product_type": "ФУТБОЛКА",
             "declaration_number": "Д-СВОЯ",
             "declaration_date": "2026-01-01", "producer": ""}]
    raw = [dict(r) for r in rows]
    filled = apply_defaults(rows, {"brand": "YCPB"})
    src = sources(raw)
    rules = [R(7, brand="YCPB", product_types=["ФУТБОЛКА"],
               declaration_number="Д-ПРАВИЛО", producer="ИП X")]
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


def test_pipeline_multitype_rule():
    """Одно правило на несколько видов: Шапки и Шапки-ушанки → одна декларация."""
    rules = [R(9, brand="Adel", product_types=["ШАПКА", "ШАПКА-УШАНКА", "КЕПКА"],
               declaration_number="Д-ADEL", producer="Фабрика Adel")]
    raw = [{"brand": "adel", "product_type": "ШАПКА-УШАНКА"},
           {"brand": "adel", "product_type": "ПАНАМА"}]
    rows, src, matched = resolve_pipeline(raw, {"brand": "YCPB"}, rules)
    assert rows[0]["declaration_number"] == "Д-ADEL" and matched[0] == 9
    assert src[0]["declaration_number"] == "rule"
    # вид вне списка → правило не сработало, декларация осталась дефолтной
    assert rows[1]["declaration_number"] == "" and matched[1] is None
