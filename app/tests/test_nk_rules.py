"""Движок правил РД: match_rule (приоритет, мультивыбор видов, casefold) и
apply_rules (файл > правило > дефолт; fields — дополнительные поля карточки)."""
from marko.nkmt.parse import apply_defaults
from marko.nkmt.resolve import apply_rules, match_rule, resolve_fields, resolve_pipeline, sources


def R(id, brand="", product_types=None, declaration_number="Д-X",
      declaration_date="2026-05-05", producer="", fields=None):
    return {"id": id, "brand": brand, "product_types": product_types or [],
            "declaration_number": declaration_number,
            "declaration_date": declaration_date, "producer": producer,
            "fields": fields or {}}


def test_match_rule_precedence():
    rules = [R(1, brand="ycpb"), R(2, brand="YCPB", product_types=["ФУТБОЛКА", "ШАПКА"]),
             R(3, brand="YCPB")]
    assert match_rule(rules, "YCPB", "ШАПКА")["id"] == 2      # 2 условия бьют 1
    assert match_rule(rules, "YCPB", "КЕПКА")["id"] == 3      # вид вне списка; ничья → больший id
    assert match_rule(rules, "ДРУГОЙ", "ФУТБОЛКА") is None    # бренд не совпал нигде
    # мультивыбор: правило бьёт по любому виду из списка
    assert match_rule(rules, "ycpb", "ФУТБОЛКА")["id"] == 2
    # вид товара — casefold, как бренд: ручной ввод мимо подсказок не разваливает матчинг
    assert match_rule([R(5, product_types=["ШАПКА"])], "YCPB", "шапка")["id"] == 5
    assert match_rule([R(5, product_types=["Шапка-Ушанка"])], "", "ШАПКА-УШАНКА")["id"] == 5


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


def test_apply_rules_fields_fill_and_file_wins():
    """fields правила подставляют поля карточки в пустые слоты: типовой кейс
    «one size» для шапок. Файловое значение сильнее (инвариант _fill)."""
    raw = [{"brand": "YCPB", "product_type": "ШАПКА", "size": ""},
           {"brand": "YCPB", "product_type": "ШАПКА", "size": "58-60", "color": "ЧЁРНЫЙ"}]
    rows, src = apply_defaults(raw, {}), sources(raw)
    out, src2, matched = apply_rules(
        [R(3, product_types=["ШАПКА"], declaration_number="Д-1",
           fields={"size": "ONE SIZE", "color": "ОЛИВА"})], rows, src)
    assert out[0]["size"] == "ONE SIZE" and src2[0]["size"] == "rule"
    assert out[0]["color"] == "ОЛИВА" and src2[0]["color"] == "rule"
    assert matched[0] == 3
    # файловые значения сильнее: и размер, и цвет не тронуты при подходящем правиле
    assert out[1]["size"] == "58-60" and src2[1]["size"] == "file"
    assert out[1]["color"] == "ЧЁРНЫЙ" and src2[1]["color"] == "file"


def test_apply_rules_fields_whitelist_enforced_in_engine():
    """Ключи вне RULE_FIELDS движок игнорирует оборонительно (в БД значения
    могли попасть в обход роута): идентификация строки не подменяется."""
    raw = [{"brand": "YCPB", "product_type": "ШАПКА", "article": "A-1", "gtin": ""}]
    out, _, _ = apply_rules(
        [R(1, product_types=["ШАПКА"], declaration_number="Д-1",
           fields={"article": "HACKED", "tnved": "0000000000", "size": "ONE SIZE"})],
        apply_defaults(raw, {}), sources(raw))
    assert out[0]["article"] == "A-1" and out[0].get("tnved", "") != "0000000000"
    assert out[0]["size"] == "ONE SIZE"    # валидный ключ из той же карты работает


def test_resolve_fields_includes_rule_fields():
    """«Проверка подстановок»: rule-поля в ответе; недефолтуемые поля без
    подстановки имеют src='' (источника нет), дефолтуемые — 'default'."""
    rules = [R(2, product_types=["ШАПКА"], declaration_number="Д-1",
               fields={"size": "ONE SIZE"})]
    keys = resolve_fields("YCPB", "шапка", {}, rules)
    assert keys["size"] == {"value": "ONE SIZE", "src": "rule"}
    assert keys["color"] == {"value": "", "src": ""}
    assert keys["declaration_number"] == {"value": "Д-1", "src": "rule"}
    # без правила: размер пуст (дефолт не задан), цвет — без источника
    keys2 = resolve_fields("YCPB", "кепка", {}, rules)
    assert keys2["size"] == {"value": "", "src": "default"}
