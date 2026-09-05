"""Импорт выгрузки: POST /v1/nkmt/import + GET /batches (смешанные пакеты, upsert по артикулу)."""
from marko.nkmt.models import Batch, Card, Declaration
from tests.test_api_nkmt_dicts import AUTH, AUTH_RO, client  # noqa: F401  (фикстура client)
from tests.test_nk_parse import HDR, make_xlsx

# валидная строка по семантике Task 6 BASE: пресеты из фикстуры модели, декларация в реестре
ROW_OK = ["T-1", "6109100000", "Футболка тест", "ФУТБОЛКА", "БЕЛЫЙ", "100% хлопок",
          "M", "Tee", "", "", "", "Д-1", ""]
ROW_BAD_TYPE = ["T-2", "6109100000", "Футболка тест", "НЕТ ТАКОГО", "БЕЛЫЙ", "100% хлопок",
                "M", "Tee", "", "", "", "Д-1", ""]
ROW_DUP = list(ROW_OK)  # дубль артикула внутри файла

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

HDR_GTIN = [*HDR, "gtin"]


def _row_gtin(article: str, gtin: str) -> list:
    """ROW_OK-форма строки + колонка gtin под HDR_GTIN."""
    return [article, *ROW_OK[1:], gtin]


def test_import_mixed_and_reimport(db, client, monkeypatch, model):
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    db.add(Declaration(doc_number="Д-1", doc_date="2025-12-01")); db.commit()
    files = {"file": ("import.xlsx", make_xlsx(HDR, [ROW_OK, ROW_BAD_TYPE, ROW_DUP]), XLSX_MIME)}
    r = client.post("/v1/nkmt/import", headers=AUTH, files=files)
    assert r.status_code == 200
    b = r.json(); assert b["stats"] == {"ok": 1, "error": 2}
    cards = client.get(f"/v1/nkmt/batches/{b['batch_id']}", headers=AUTH_RO).json()["cards"]
    assert {c["status"] for c in cards} == {"ok", "error"}
    assert all("attributes" not in c for c in cards)  # без attributes в списке
    # повторный импорт той же строки → карточка обновилась, не задублилась
    files2 = {"file": ("import.xlsx", make_xlsx(HDR, [ROW_OK]), XLSX_MIME)}
    b2 = client.post("/v1/nkmt/import", headers=AUTH, files=files2).json()
    assert b2["stats"] == {"ok": 1, "error": 0}
    cards2 = client.get(f"/v1/nkmt/batches/{b2['batch_id']}", headers=AUTH_RO).json()["cards"]
    assert len(cards2) == 1 and cards2[0]["article"] == cards[0]["article"]
    # список батчей (новые сверху), фильтр по status, фильтр карточек, 404
    lst = client.get("/v1/nkmt/batches", headers=AUTH_RO).json()
    assert [x["id"] for x in lst] == [b2["batch_id"], b["batch_id"]]
    assert lst[0]["status"] == "new" and lst[1]["status"] == "partial"
    assert client.get("/v1/nkmt/batches?status=partial", headers=AUTH_RO).json()[0]["id"] == b["batch_id"]
    # фильтр карточек: T-1 переехал в новый батч, в старом осталась только ошибка
    ok_only = client.get(f"/v1/nkmt/batches/{b2['batch_id']}?card_status=ok",
                         headers=AUTH_RO).json()["cards"]
    assert [c["article"] for c in ok_only] == ["T-1"]
    err_only = client.get(f"/v1/nkmt/batches/{b['batch_id']}?card_status=error",
                          headers=AUTH_RO).json()["cards"]
    assert [c["article"] for c in err_only] == ["T-2"]
    assert client.get("/v1/nkmt/batches/99999", headers=AUTH_RO).status_code == 404


def test_import_gtin_conflict_not_persisted(db, client, monkeypatch, model):
    GTIN = "46305206999701"
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    db.add(Declaration(doc_number="Д-1", doc_date="2025-12-01"))
    b0 = Batch(source_filename="seed.xlsx"); db.add(b0); db.flush()
    db.add(Card(article="G-1", gtin=GTIN, batch_id=b0.id, tnved="6109100000",
                name="Футболка тест", status="ok")); db.commit()
    # G-2 с gtin карточки G-1 → «gtin занят»; отклонённый gtin не сохраняется
    files = {"file": ("import.xlsx", make_xlsx(HDR_GTIN, [_row_gtin("G-2", GTIN)]), XLSX_MIME)}
    b = client.post("/v1/nkmt/import", headers=AUTH, files=files).json()
    assert b["stats"] == {"ok": 0, "error": 1}
    g2 = client.get(f"/v1/nkmt/batches/{b['batch_id']}", headers=AUTH_RO).json()["cards"][0]
    assert g2["article"] == "G-2" and g2["status"] == "error"
    assert "gtin" in g2["error_text"] and g2["gtin"] == ""  # спорный gtin не записан
    g1 = client.get(f"/v1/nkmt/batches/{b0.id}", headers=AUTH_RO).json()["cards"][0]
    assert g1["article"] == "G-1" and g1["status"] == "ok" and g1["gtin"] == GTIN
    # тот же артикул со своим gtin — самоконфликта нет, gtin сохранён
    files2 = {"file": ("import.xlsx", make_xlsx(HDR_GTIN, [_row_gtin("G-1", GTIN)]), XLSX_MIME)}
    b2 = client.post("/v1/nkmt/import", headers=AUTH, files=files2).json()
    assert b2["stats"] == {"ok": 1, "error": 0}
    g1 = client.get(f"/v1/nkmt/batches/{b2['batch_id']}", headers=AUTH_RO).json()["cards"][0]
    assert g1["article"] == "G-1" and g1["status"] == "ok" and g1["gtin"] == GTIN


def test_import_gtin_malformed_not_persisted_then_filled(db, client, monkeypatch, model):
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    db.add(Declaration(doc_number="Д-1", doc_date="2025-12-01"))
    b0 = Batch(source_filename="seed.xlsx"); db.add(b0); db.flush()
    db.add(Card(article="F-1", gtin="", batch_id=b0.id, tnved="6109100000",
                name="Футболка тест", status="ok")); db.commit()
    # битый gtin (не 14 цифр) → ошибка строки, значение НЕ попадает в карточку
    files = {"file": ("import.xlsx", make_xlsx(HDR_GTIN, [_row_gtin("F-1", "12345")]), XLSX_MIME)}
    b = client.post("/v1/nkmt/import", headers=AUTH, files=files).json()
    assert b["stats"] == {"ok": 0, "error": 1}
    f1 = client.get(f"/v1/nkmt/batches/{b['batch_id']}", headers=AUTH_RO).json()["cards"][0]
    assert f1["article"] == "F-1" and f1["status"] == "error"
    assert "GTIN" in f1["error_text"] and f1["gtin"] == ""  # битое значение не сохранено
    # исправленный файл: валидный свободный gtin → дозаполнение пустого gtin карточки
    files2 = {"file": ("import.xlsx", make_xlsx(HDR_GTIN, [_row_gtin("F-1", "46305206999702")]), XLSX_MIME)}
    b2 = client.post("/v1/nkmt/import", headers=AUTH, files=files2).json()
    assert b2["stats"] == {"ok": 1, "error": 0}
    f1 = client.get(f"/v1/nkmt/batches/{b2['batch_id']}", headers=AUTH_RO).json()["cards"][0]
    assert f1["article"] == "F-1" and f1["status"] == "ok" and f1["gtin"] == "46305206999702"
