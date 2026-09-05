import json
from pathlib import Path

from marko.nkmt import validate

# model-фикстура (патчи dicts на фикстуру модели 6109100000) — общая, в conftest.py
BASE = {"article": "T-1", "tnved": "6109100000", "name": "Футболка тест", "product_type": "ФУТБОЛКА",
        "color": "БЕЛЫЙ", "composition": "100% хлопок", "size": "M", "model": "Tee",
        "brand": "YCPB", "target_gender": "ЖЕНСКИЙ", "size_system": "РОССИЯ",
        "techreg": 'ТР ТС 017/2011 "О безопасности продукции легкой промышленности"',
        "country": "RU", "producer": "ИП", "declaration_number": "Д-1", "declaration_date": "2026-01-01",
        "gtin": "", "category_hint": ""}


def _row(**kw): return {**BASE, **kw}


def test_ok_row(model):
    out = validate.validate_rows(db := None, None, None, [_row()])[0]   # db/client не нужны при патчах
    assert out["ok"] and out["cat_id"] == "214943"
    a = out["attributes"]
    assert a["2504"] == "YCPB" and a["35"] == {"type": "РОССИЯ", "value": "M"}
    assert a["13836"] == [BASE["techreg"]] and a["13914"]["type"] == "Модель"


def test_bad_preset_and_short_tnved(model):
    out = validate.validate_rows(None, None, None, [_row(product_type="НЕТ ТАКОГО"),
                                                    _row(article="T-2", tnved="6109")])
    assert not out[0]["ok"] and "Вид товара" in out[0]["error"]
    assert not out[1]["ok"] and "ТНВЭД" in out[1]["error"]


def test_unicode_filter(model):
    out = validate.validate_rows(None, None, None, [_row(name="Футболка ☺")])[0]
    assert not out["ok"] and "запрещённые символы" in out["error"]


def test_declaration_must_be_in_registry(model, db):
    from marko.nkmt.models import Declaration
    db.add(Declaration(doc_number="Д-1", doc_date="2025-12-01")); db.commit()
    out = validate.validate_rows(db, None, None, [_row(declaration_date="")])[0]
    assert out["ok"] and out["attributes"]["23557"]["date"] == "2025-12-01"
    out2 = validate.validate_rows(db, None, None, [_row(declaration_number="ХХ-9")])[0]
    assert not out2["ok"] and "реестре" in out2["error"]


def test_categories_fetched_once_per_tnved(db, monkeypatch):
    """/nk/categories тянется РАЗ на ТН ВЭД на вызов validate_rows (массовый
    импорт), не на строку; resolve_category — настоящий, клиент считает вызовы."""
    fix = json.loads((Path(__file__).parent / "fixtures" / "nk_attrs_6109100000.json")
                     .read_text(encoding="utf-8"))
    monkeypatch.setattr(validate.dicts, "attrs_model", lambda *a, **k: fix)
    monkeypatch.setattr(validate.dicts, "resolve_brand", lambda *a, **k: 2102811)
    from marko.nkmt.models import Declaration
    db.add(Declaration(doc_number="Д-1", doc_date="2025-12-01")); db.commit()
    calls = []

    class CountingNk:
        def categories(self, token, tnved):
            calls.append(tnved)
            return [{"cat_id": 214943, "cat_name": "Футболки"}]

    out = validate.validate_rows(db, CountingNk(), "T", [_row(), _row(article="T-2")])
    assert [o["ok"] for o in out] == [True, True]
    assert out[0]["cat_id"] == out[1]["cat_id"] == "214943"
    assert calls == ["6109100000"]  # две строки, один ТН ВЭД → один запрос
