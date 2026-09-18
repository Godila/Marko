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


def test_preset_values_canonicalized_casefold(model):
    """Регистр значения не валит preset-проверку: «футболка»/«женский» →
    канонические написания справочника НК в атрибутах карточки."""
    out = validate.validate_rows(None, None, None,
                                 [_row(product_type="футболка",
                                       target_gender="женский")])[0]
    assert out["ok"]
    assert out["attributes"]["12"] == "ФУТБОЛКА"
    assert out["attributes"]["14013"] == "ЖЕНСКИЙ"


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


# --- синонимы и подсказки живых справочников ЧЗ (инцидент «Унисекс» 17.09:
# литерал справочника — «УНИВЕРСАЛЬНЫЙ (УНИСЕКС)», оператор пишет синоним) ---
def test_gender_synonyms_map_to_dictionary_literal(model):
    out = validate.validate_rows(None, None, None,
                                 [_row(target_gender="Унисекс"),
                                  _row(article="T-2", target_gender="Универсальный"),
                                  _row(article="T-3", target_gender="универсальное"),
                                  _row(article="T-4", target_gender="без пола")])
    assert [o["ok"] for o in out] == [True] * 4
    assert {o["attributes"]["14013"] for o in out[:3]} == {"УНИВЕРСАЛЬНЫЙ (УНИСЕКС)"}
    assert out[3]["attributes"]["14013"] == "БЕЗ УКАЗАНИЯ ПОЛА"


def test_punctuation_insensitive_preset_match(model):
    """«футболка поло» → «ФУТБОЛКА-ПОЛО», «универсальный(унисекс)» → литерал:
    раскладка пунктуации/пробелов не должна валить preset-проверку."""
    out = validate.validate_rows(None, None, None,
                                 [_row(product_type="футболка поло",
                                       target_gender="универсальный(унисекс)")])[0]
    assert out["ok"]
    assert out["attributes"]["12"] == "ФУТБОЛКА-ПОЛО"
    assert out["attributes"]["14013"] == "УНИВЕРСАЛЬНЫЙ (УНИСЕКС)"


def test_preset_error_lists_available_values(model):
    """Короткий справочник (≤6) — целиком в тексте ошибки: оператор видит
    валидные значения, не гадает."""
    out = validate.validate_rows(None, None, None, [_row(target_gender="унис")])[0]
    assert not out["ok"]
    assert ("доступно: ЖЕНСКИЙ, МУЖСКОЙ, БЕЗ УКАЗАНИЯ ПОЛА, "
            "УНИВЕРСАЛЬНЫЙ (УНИСЕКС)") in out["error"]


def test_preset_error_suggests_by_containment(model):
    """Длинный справочник (вид товара, 75 значений) — ближайшие по вхождению:
    «топ-банд» → ТОП-БАНДО, а не весь список."""
    out = validate.validate_rows(None, None, None, [_row(product_type="топ-банд")])[0]
    assert not out["ok"]
    assert "возможно: ТОП-БАНДО" in out["error"]
    assert "доступно:" not in out["error"]


def test_size_outside_preset_warns_not_blocks(db, monkeypatch):
    """Шапки 6505: у атрибута 35 есть справочник размеров (46–62), но
    preset_only=false — «ONE SIZE» не блокирует строку, помечается
    предупреждением для оператора (в превью)."""
    fix = json.loads((Path(__file__).parent / "fixtures" / "nk_attrs_6109100000.json")
                     .read_text(encoding="utf-8"))
    for a in fix["m"]:
        if a["attr_id"] == 35:
            a["attr_preset"] = [str(n) for n in range(46, 63)]
    monkeypatch.setattr(validate.dicts, "attrs_model", lambda *a, **k: fix)
    monkeypatch.setattr(validate.dicts, "resolve_brand", lambda *a, **k: 2102811)
    monkeypatch.setattr(validate.dicts, "resolve_category", lambda *a, **k: "214943")
    from marko.nkmt.models import Declaration
    db.add(Declaration(doc_number="Д-1", doc_date="2025-12-01")); db.commit()
    out = validate.validate_rows(db, None, None,
                                 [_row(size="ONE SIZE"), _row(article="T-2", size="54")])
    assert out[0]["ok"] is True and "вне справочника" in out[0]["size_warning"]
    assert out[1]["ok"] is True and out[1]["size_warning"] == ""


def test_normform_folds_yo_and_unicode_dashes(model):
    """«термобелье» (без ё) и «футболка–поло» (en-тире от автозамены) —
    канонизируются к литералам справочника «ТЕРМОБЕЛЬЁ» / «ФУТБОЛКА-ПОЛО»."""
    out = validate.validate_rows(None, None, None,
                                 [_row(product_type="термобелье"),
                                  _row(article="T-2", product_type="футболка–поло")])
    assert out[0]["attributes"]["12"] == "ТЕРМОБЕЛЬЁ"
    assert out[1]["attributes"]["12"] == "ФУТБОЛКА-ПОЛО"


def test_normform_guard_rejects_ambiguous(model, monkeypatch):
    """Два литерала с одной нормформой (напр. «ФУТБОЛКА-ПОЛО»/«ФУТБОЛКА ПОЛО»)
    — ввод «футболка:поло» не должен молча отображаться в один из них."""
    fix = json.loads((Path(__file__).parent / "fixtures" / "nk_attrs_6109100000.json")
                     .read_text(encoding="utf-8"))
    for a in fix["m"]:
        if a["attr_id"] == 12:
            a["attr_preset"] = ["ФУТБОЛКА-ПОЛО", "ФУТБОЛКА ПОЛО"]
    monkeypatch.setattr(validate.dicts, "attrs_model", lambda *a, **k: fix)
    out = validate.validate_rows(None, None, None, [_row(product_type="футболка:поло")])[0]
    assert out["ok"] is False and "отсутствует в справочнике" in out["error"]


def test_empty_size_gives_no_size_warning(db, monkeypatch):
    """Пустой размер — уже ошибка обязательного поля; вторая диагностика
    «вне справочника» под пустым значением вводила бы в заблуждение."""
    fix = json.loads((Path(__file__).parent / "fixtures" / "nk_attrs_6109100000.json")
                     .read_text(encoding="utf-8"))
    for a in fix["m"]:
        if a["attr_id"] == 35:
            a["attr_preset"] = [str(n) for n in range(46, 63)]
    monkeypatch.setattr(validate.dicts, "attrs_model", lambda *a, **k: fix)
    monkeypatch.setattr(validate.dicts, "resolve_brand", lambda *a, **k: 2102811)
    monkeypatch.setattr(validate.dicts, "resolve_category", lambda *a, **k: "214943")
    out = validate.validate_rows(None, None, None, [_row(size="")])[0]
    assert out["ok"] is False and "пустое обязательное поле" in out["error"]
    assert out["size_warning"] == ""
