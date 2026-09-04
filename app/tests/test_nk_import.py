"""Импорт выгрузки: POST /v1/nkmt/import + GET /batches (смешанные пакеты, upsert по артикулу)."""
from mpmt.nkmt.models import Declaration
from tests.test_api_nkmt_dicts import AUTH, AUTH_RO, client  # noqa: F401  (фикстура client)
from tests.test_nk_parse import HDR, make_xlsx

# валидная строка по семантике Task 6 BASE: пресеты из фикстуры модели, декларация в реестре
ROW_OK = ["T-1", "6109100000", "Футболка тест", "ФУТБОЛКА", "БЕЛЫЙ", "100% хлопок",
          "M", "Tee", "", "", "", "Д-1", ""]
ROW_BAD_TYPE = ["T-2", "6109100000", "Футболка тест", "НЕТ ТАКОГО", "БЕЛЫЙ", "100% хлопок",
                "M", "Tee", "", "", "", "Д-1", ""]
ROW_DUP = list(ROW_OK)  # дубль артикула внутри файла

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_import_mixed_and_reimport(db, client, monkeypatch, model):
    monkeypatch.setattr("mpmt.connector_mt.manager.get_token", lambda _db: "T")
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
