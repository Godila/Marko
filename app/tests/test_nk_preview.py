"""Превью импорта: dry-run без записи; приоритет файл > правило > дефолт; статусы gtin."""
import io

import openpyxl

from marko.nkmt.models import Batch, Card, Declaration, Rule
from tests.test_api_nkmt_dicts import AUTH, AUTH_RO, client  # noqa: F401  (фикстура client)
from tests.test_nk_import import XLSX_MIME
from tests.test_nk_parse import HDR, make_xlsx

ROW_NO_DECL = ["P-1", "6109100000", "Футболка превью", "ФУТБОЛКА", "БЕЛЫЙ", "100% хлопок",
               "M", "Tee", "", "", "", "", ""]
ROW_FILE_DECL = ["P-2", "6109100000", "Футболка своя", "ФУТБОЛКА", "БЕЛЫЙ", "100% хлопок",
                 "M", "Tee", "", "", "", "Д-2", ""]


def test_preview_dry_run_and_rule(db, client, monkeypatch, model):
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    d1 = Declaration(doc_number="Д-1", doc_date="2025-11-01")
    d2 = Declaration(doc_number="Д-2", doc_date="2025-12-01")
    db.add_all([d1, d2]); db.flush()
    db.add(Rule(brand="YCPB", product_type="ФУТБОЛКА", declaration_id=d1.id,
                producer="ИП Байкулов"))
    db.commit()
    files = {"file": ("p.xlsx", make_xlsx(HDR, [ROW_NO_DECL, ROW_FILE_DECL]), XLSX_MIME)}
    r = client.post("/v1/nkmt/import/preview", headers=AUTH, files=files)
    assert r.status_code == 200
    out = r.json()
    assert out["stats"]["ok"] == 2 and out["stats"]["error"] == 0
    rows = {x["article"]: x for x in out["rows"]}
    # правило: пустая декларация ← правило, дата дозаполнена из реестра
    p1 = rows["P-1"]
    assert p1["declaration_number"] == "Д-1" and p1["declaration_date"] == "2025-11-01"
    assert p1["producer"] == "ИП Байкулов" and p1["rule_id"]
    assert p1["src"]["declaration_number"] == "rule" and p1["src"]["producer"] == "rule"
    # файловая декларация сильнее правила (бренд обеих строк — из дефолта YCPB)
    p2 = rows["P-2"]
    assert p2["declaration_number"] == "Д-2" and p2["src"]["declaration_number"] == "file"
    assert p2["producer"] == "ИП Байкулов"      # producer у файла пуст → из правила
    # dry-run: ни батчей, ни карточек
    assert db.query(Batch).count() == 0 and db.query(Card).count() == 0
    # импорт того же файла повторяет превью: декларация правила в атрибутах
    imp = client.post("/v1/nkmt/import", headers=AUTH, files=files).json()
    assert imp["stats"] == {"ok": 2, "error": 0}
    card = db.query(Card).filter_by(article="P-1").one()
    assert card.attributes["23557"] == {"number": "Д-1", "date": "2025-11-01"}
    assert card.attributes["2503"] == "ИП Байкулов"


def test_preview_gtin_statuses(db, client, monkeypatch, model):
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    db.add(Declaration(doc_number="Д-1", doc_date="2025-12-01"))
    b0 = Batch(source_filename="seed.xlsx"); db.add(b0); db.flush()
    db.add(Card(article="G-1", gtin="46305206999701", batch_id=b0.id,
                tnved="6109100000", name="Футболка тест", status="ok"))
    db.commit()
    rows = [
        ["N-1", *ROW_NO_DECL[1:11], "Д-1", "", "46305206999702"],   # новый gtin
        ["G-1", *ROW_NO_DECL[1:11], "Д-1", "", "46305206999703"],   # свой артикул: хранит свой gtin
        ["C-1", *ROW_NO_DECL[1:11], "Д-1", "", "46305206999701"],   # gtin чужой карточки
    ]
    files = {"file": ("g.xlsx", make_xlsx([*HDR, "gtin"], rows), XLSX_MIME)}
    out = client.post("/v1/nkmt/import/preview", headers=AUTH, files=files).json()
    st = {x["article"]: x for x in out["rows"]}
    assert st["N-1"]["gtin_status"] == "new" and st["N-1"]["gtin"] == "46305206999702"
    assert st["G-1"]["gtin_status"] == "update" and st["G-1"]["gtin"] == "46305206999701"
    assert st["C-1"]["gtin_status"] == "conflict" and st["C-1"]["gtin"] == ""
    assert st["C-1"]["ok"] is False and "gtin" in st["C-1"]["error"]
    assert out["stats"] == {"ok": 2, "error": 1, "new": 1, "update": 1, "conflict": 1}
    assert db.query(Batch).count() == 1   # только seed — превью не пишет


def test_preview_dict_provenance(db, client, monkeypatch, model):
    """Справочник брендов как 4-й источник: превью показывает src='dict'."""
    from marko.nkmt.models import Brand
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    db.add(Declaration(doc_number="Д-РЕЕСТР", doc_date="2025-10-01"))
    db.flush()
    decl = db.query(Declaration).filter_by(doc_number="Д-РЕЕСТР").one()
    db.add(Brand(name="КЛИЕНТ", producer="Фабрика клиента", declaration_id=decl.id))
    db.commit()
    files = {"file": ("p.xlsx", make_xlsx(HDR, [
        ["D-1", "6109100000", "Футболка клиент", "ФУТБОЛКА", "БЕЛЫЙ", "100% хлопок",
         "M", "Tee", "КЛИЕНТ", "", "", "", ""]]), XLSX_MIME)}
    out = client.post("/v1/nkmt/import/preview", headers=AUTH, files=files).json()
    row = out["rows"][0]
    assert row["producer"] == "Фабрика клиента" and row["src"]["producer"] == "dict"
    assert row["declaration_number"] == "Д-РЕЕСТР" and row["src"]["declaration_number"] == "dict"
    assert row["declaration_date"] == "2025-10-01"
    assert row["ok"] is True and row["rule_id"] is None


def test_preview_bad_file_400(db, client, monkeypatch, model):
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    files = {"file": ("bad.xlsx", b"not an xlsx at all", XLSX_MIME)}
    r = client.post("/v1/nkmt/import/preview", headers=AUTH, files=files)
    assert r.status_code == 400 and "xlsx" in r.json()["detail"]


def test_template_endpoint(db, client):
    r = client.get("/v1/nkmt/import/template", headers=AUTH_RO)
    assert r.status_code == 200 and r.headers["content-type"] == XLSX_MIME
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Выгрузка", "Инструкция"]
