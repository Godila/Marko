"""Справочник брендов: match/apply (4-й источник) и «примерка» (resolve_fields)."""
from marko.nkmt.parse import apply_defaults
from marko.nkmt.resolve import (apply_brand_dict, match_brand, resolve_fields,
                                resolve_pipeline, sources)


def B(id, name, producer="", declaration_number="", declaration_date=""):
    return {"id": id, "name": name, "producer": producer,
            "declaration_id": 1 if declaration_number else None,
            "declaration_number": declaration_number,
            "declaration_date": declaration_date}


RULE = {"id": 7, "brand": "YCPB", "product_type": "ФУТБОЛКА",
        "declaration_number": "Д-ПРАВИЛО", "declaration_date": "2026-05-05",
        "producer": "ИП из правила"}
DEFAULTS = {"brand": "YCPB", "target_gender": "ЖЕНСКИЙ", "size_system": "МЕЖДУНАРОДНЫЙ",
            "country": "RU", "producer": "ИП дефолт", "declaration_number": "Д-ДЕФОЛТ",
            "declaration_date": "2026-01-01", "techreg": "ТР ТС 017/2011"}


def test_match_brand_casefold():
    brands = [B(1, "YCPB", producer="ИП")]
    assert match_brand(brands, "ycpb")["id"] == 1
    assert match_brand(brands, "YCPB")["id"] == 1
    assert match_brand(brands, "ДРУГОЙ") is None
    assert match_brand(brands, "") is None


def test_apply_brand_dict_fills_only_default_slots():
    """Пустые слоты ← справочник (src='dict'); файл ('file') и правило ('rule') целы;
    правило без producer + справочник с producer → producer из справочника."""
    brands = [B(1, "YCPB", producer="ИП из справочника",
                declaration_number="Д-СПРАВ", declaration_date="2026-03-03")]
    raw = [{"brand": "YCPB", "product_type": "ФУТБОЛКА",
            "declaration_number": "", "declaration_date": "", "producer": ""},
           {"brand": "ycpb", "product_type": "ФУТБОЛКА",
            "declaration_number": "Д-ФАЙЛ", "declaration_date": "2026-02-02",
            "producer": "ИП из файла"}]
    rules = [dict(RULE, producer="")]   # правило ставит декларацию, producer пуст
    rows, src, matched = resolve_pipeline(raw, DEFAULTS, rules, brands)
    # строка 1: декларация от правила ('rule'), producer — из справочника ('dict')
    assert rows[0]["declaration_number"] == "Д-ПРАВИЛО" and src[0]["declaration_number"] == "rule"
    assert rows[0]["producer"] == "ИП из справочника" and src[0]["producer"] == "dict"
    # строка 2: файловые значения целы (регистр бренда не важен для справочника тоже)
    assert rows[1]["declaration_number"] == "Д-ФАЙЛ" and src[1]["declaration_number"] == "file"
    assert rows[1]["producer"] == "ИП из файла" and src[1]["producer"] == "file"
    assert matched == [7, 7]
    # входные rows/src не мутированы и выход — новые объекты
    assert raw == [{"brand": "YCPB", "product_type": "ФУТБОЛКА",
                    "declaration_number": "", "declaration_date": "", "producer": ""},
                   {"brand": "ycpb", "product_type": "ФУТБОЛКА",
                    "declaration_number": "Д-ФАЙЛ", "declaration_date": "2026-02-02",
                    "producer": "ИП из файла"}]
    assert rows[0] is not raw[0]


def test_dict_without_declaration_fills_producer_only():
    brands = [B(1, "YCPB", producer="ИП из справочника")]
    rows, src, _ = resolve_pipeline([{"brand": "YCPB"}], DEFAULTS, [], brands)
    assert rows[0]["producer"] == "ИП из справочника" and src[0]["producer"] == "dict"
    assert rows[0]["declaration_number"] == "Д-ДЕФОЛТ" and src[0]["declaration_number"] == "default"


def test_resolve_fields_matrix():
    brands = [B(1, "КЛИЕНТ", producer="Фабрика клиента",
                declaration_number="Д-КЛИЕНТ", declaration_date="2026-04-04")]
    # 1) только дефолты: пустой бренд ← дефолт YCPB, вид «ФУТБОЛКА» матчит RULE
    rz = resolve_fields("", "ФУТБОЛКА", DEFAULTS, [RULE], brands)
    assert rz["brand"] == {"value": "YCPB", "src": "default"}
    assert rz["declaration_number"] == {"value": "Д-ПРАВИЛО", "src": "rule"}
    assert rz["techreg"]["src"] == "default" and rz["techreg"]["value"].startswith("ТР ТС")
    # 2) бренд из справочника, мимо правил: producer/декларация от справочника
    rz = resolve_fields("клиент", "ШАПКА", DEFAULTS, [RULE], brands)
    assert rz["producer"] == {"value": "Фабрика клиента", "src": "dict"}
    assert rz["declaration_number"] == {"value": "Д-КЛИЕНТ", "src": "dict"}
    assert rz["brand"] == {"value": "клиент", "src": "file"}   # введённое = как файловое
    # 3) файловый приоритет «введено» сильнее справочника не проверить здесь —
    #    в примерке бренд всегда введён; см. apply-тест выше (file > dict)


def test_pipeline_sources_and_defaults_untouched():
    raw = [{"brand": "X", "producer": "П"}]
    src = sources(raw)
    assert src[0]["brand"] == "file" and src[0]["declaration_number"] == "default"
    filled = apply_defaults(raw, DEFAULTS)
    assert filled[0]["brand"] == "X" and filled[0]["producer"] == "П"
    assert filled[0]["country"] == "RU"
