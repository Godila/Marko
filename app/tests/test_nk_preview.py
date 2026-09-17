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
    db.add(Rule(brand="YCPB", product_types=["ФУТБОЛКА"], declaration_id=d1.id,
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
    # src-карта покрывает правило-поля: размер из файла, цвет из файла
    assert p1["src"]["size"] == "file" and p1["src"]["color"] == "file"
    assert p1["size"] == "M" and p1["product_type"] == "ФУТБОЛКА"
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


def test_preview_rule_fields_and_casefold(db, client, monkeypatch, model):
    """Правило подставляет size («one size» шапкам) в пустые слоты; вид товара
    в файле в произвольном регистре матчится и канонизируется к написанию НК."""
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    d1 = Declaration(doc_number="Д-Ш", doc_date="2026-01-01")
    db.add(d1); db.flush()
    db.add(Rule(product_types=["ФУТБОЛКА"], declaration_id=d1.id,
                fields={"size": "ONE SIZE"}))
    db.commit()
    row = ["F-1", "6109100000", "Футболка превью", "футболка", "БЕЛЫЙ", "100% хлопок",
           "", "F-1", "", "", "", "", ""]
    files = {"file": ("f.xlsx", make_xlsx(HDR, [row]), XLSX_MIME)}
    out = client.post("/v1/nkmt/import/preview", headers=AUTH, files=files).json()
    r0 = out["rows"][0]
    assert out["stats"]["ok"] == 1
    assert r0["size"] == "ONE SIZE" and r0["src"]["size"] == "rule"
    assert r0["product_type"] == "ФУТБОЛКА"      # каноническое написание из справочника
    assert r0["declaration_number"] == "Д-Ш" and r0["rule_id"]
    # цвет/состав из файла видны в превью
    assert r0["color"] == "БЕЛЫЙ" and r0["composition"] == "100% хлопок"


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


def test_preview_bad_file_400(db, client, monkeypatch, model):
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    files = {"file": ("bad.xlsx", b"not an xlsx at all", XLSX_MIME)}
    r = client.post("/v1/nkmt/import/preview", headers=AUTH, files=files)
    assert r.status_code == 400 and "xlsx" in r.json()["detail"]


def test_preview_tnved_warning(db, client, monkeypatch, model):
    """ТНВЭД-контроль: строка с кодом вне tnved_list подставленной декларации
    получает предупреждение (импорт не блокируется); совпадение и декларация
    без списка (не проверена в ЧЗ) — молчим."""
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    d = Declaration(doc_number="Д-1", doc_date="2026-01-01",
                    tnved_list=["6109100000", "6109909900"], status="Действует")
    d2 = Declaration(doc_number="Д-2", doc_date="2026-02-02")   # без tnved_list
    db.add_all([d, d2]); db.flush()
    db.add(Rule(product_types=["ФУТБОЛКА"], declaration_id=d.id))
    db.add(Rule(product_types=["БАЛАКЛАВА"], declaration_id=d2.id))
    db.commit()
    rows = [
        ["A-1", "6203499009", "Футболка вне декларации", "ФУТБОЛКА", "БЕЛЫЙ",
         "100% хлопок", "M", "A-1", "", "", "", "", ""],
        ["A-2", "6109100000", "Футболка ок", "ФУТБОЛКА", "БЕЛЫЙ",
         "100% хлопок", "M", "A-2", "", "", "", "", ""],
        ["A-3", "6109100000", "Балаклава без проверки", "БАЛАКЛАВА", "БЕЛЫЙ",
         "100% акрил", "ONE SIZE", "A-3", "", "", "", "", ""],
    ]
    files = {"file": ("w.xlsx", make_xlsx(HDR, rows), XLSX_MIME)}
    out = client.post("/v1/nkmt/import/preview", headers=AUTH, files=files).json()
    by_article = {x["article"]: x for x in out["rows"]}
    warn = by_article["A-1"]["tnved_warning"]
    assert "6203499009" not in warn and "6109100000" in warn and "Д-1" in warn
    assert by_article["A-1"]["ok"] is True          # предупреждение, не блок
    assert by_article["A-2"]["tnved_warning"] == ""
    assert by_article["A-3"]["tnved_warning"] == ""  # у Д-2 нет списка — не проверяем


def test_template_endpoint(db, client):
    r = client.get("/v1/nkmt/import/template", headers=AUTH_RO)
    assert r.status_code == 200 and r.headers["content-type"] == XLSX_MIME
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Выгрузка", "Инструкция"]
