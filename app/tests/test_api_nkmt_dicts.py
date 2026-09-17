import pytest
from fastapi.testclient import TestClient
from marko.api.app import create_app
from marko.platform.models import PlatformPrincipal, PlatformToken, hash_token

AUTH = {"Authorization": "Bearer t1"}                 # read + nkmt:import + docs:submit
AUTH_RO = {"Authorization": "Bearer t-ro"}            # read-only


@pytest.fixture
def client(db):
    p = PlatformPrincipal(kind="user", name="owner"); db.add(p); db.flush()
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t1"),
                         scopes="read,nkmt:import,docs:submit"))
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t-ro"), scopes="read"))
    db.commit()
    return TestClient(create_app())


DECL = {"doc_number": "ЕАЭС №RU Д-RU.АБ12.В.12345", "doc_date": "2026-01-01"}


def test_declarations_crud(db, client):
    r = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL)
    assert r.status_code == 200 and r.json()["id"]
    assert r.json()["found"] is False   # ЧЗ офлайн в тестах — обогащение best-effort
    lst = client.get("/v1/nkmt/declarations", headers=AUTH_RO).json()
    row = lst[0]
    assert row["id"] == r.json()["id"] and row["status"] == "" and row["tnved_list"] == []
    dup = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL)
    assert dup.status_code == 409
    assert client.delete(f"/v1/nkmt/declarations/{r.json()['id']}", headers=AUTH).json() == {"ok": True}
    assert client.get("/v1/nkmt/declarations", headers=AUTH_RO).json() == []


def test_declarations_check_enriches(db, client, monkeypatch):
    """POST /declarations/{id}/check: rd/list → rich-поля; не найдена — found: 0."""
    from marko.nkmt.client import NkClient
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    monkeypatch.setattr(NkClient, "rd_list", lambda self, token, docs: {
        "documents": [{"type": "CONFORMITY_DECLARATION", "number": DECL["doc_number"],
                       "dateFrom": DECL["doc_date"], "dateTo": "2031-05-12",
                       "status": "Действует", "productName": "Головные уборы",
                       "productTnved": "6505003000, 6505009000",
                       "productTechRegulations": "ТР ТС 017/2011",
                       "applicantProductName": "ИП", "manufacturerProductName": "ИП"}],
        "errors": []})
    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()
    out = client.post(f"/v1/nkmt/declarations/{d['id']}/check", headers=AUTH).json()
    assert out["found"] == 1 and out["declaration"]["status"] == "Действует"
    assert out["declaration"]["tnved_list"] == ["6505003000", "6505009000"]
    assert out["declaration"]["date_to"] == "2031-05-12"
    assert out["declaration"]["checked_at"]
    # check-all по всему реестру — тот же enriched-ответ
    allout = client.post("/v1/nkmt/declarations/check-all", headers=AUTH).json()
    assert allout["checked"] == 1 and allout["found"] == 1


def test_declarations_check_all_empty_409(db, client):
    assert client.post("/v1/nkmt/declarations/check-all", headers=AUTH).status_code == 409


def test_producers_crud(db, client):
    r = client.post("/v1/nkmt/producers", headers=AUTH, json={
        "name": "ИП Байкулов Д. А.", "inn": "090201471350",
        "kind": "entrepreneur", "note": "осн. производитель"})
    assert r.status_code == 200 and r.json()["id"]
    lst = client.get("/v1/nkmt/producers", headers=AUTH_RO).json()
    assert lst == [{"id": r.json()["id"], "name": "ИП Байкулов Д. А.",
                    "inn": "090201471350", "kind": "entrepreneur",
                    "note": "осн. производитель"}]
    # дубль имени без учёта регистра — 409; битый ИНН — 400; пустое имя — 400
    assert client.post("/v1/nkmt/producers", headers=AUTH,
                       json={"name": "ип байкулов д. а."}).status_code == 409
    assert client.post("/v1/nkmt/producers", headers=AUTH,
                       json={"name": "X", "inn": "123"}).status_code == 400
    assert client.post("/v1/nkmt/producers", headers=AUTH,
                       json={"name": "  "}).status_code == 400
    assert client.delete(f"/v1/nkmt/producers/{r.json()['id']}", headers=AUTH).json() == {"ok": True}
    assert client.get("/v1/nkmt/producers", headers=AUTH_RO).json() == []
    assert client.delete("/v1/nkmt/producers/999", headers=AUTH).status_code == 404


def test_defaults_get_put(db, client):
    r = client.get("/v1/nkmt/defaults", headers=AUTH_RO).json()
    assert r["brand"] == "YCPB"
    assert client.put("/v1/nkmt/defaults", headers=AUTH,
                      json={**r, "brand": "ADEL"}).json() == {"ok": True}
    assert client.get("/v1/nkmt/defaults", headers=AUTH_RO).json()["brand"] == "ADEL"


def test_dicts_attributes_validates_tnved(db, client, monkeypatch):
    r = client.get("/v1/nkmt/dicts/attributes?tnved=6109", headers=AUTH_RO)
    assert r.status_code == 400


def test_dicts_hints(db, client):
    """Подсказки для правил: пресеты вида товара, размерные системы и пол из
    кэша атрибутных моделей + бренды и производители из справочников."""
    from marko.nkmt.models import BrandCache, Producer
    from marko.platform.models import PlatformKV
    db.add(PlatformKV(key="nk_attrs:6109100000", value={"m": [
        {"attr_id": 12, "attr_name": "Вид товара", "attr_preset": ["ФУТБОЛКА", "ШАПКА"]},
        {"attr_id": 35, "attr_name": "Размер", "attr_preset": [],
         "attr_value_type": ["МЕЖДУНАРОДНЫЙ", "ЕВРОПЕЙСКИЙ"]},
        {"attr_id": 14013, "attr_name": "Целевой пол",
         "attr_preset": ["ЖЕНСКИЙ", "МУЖСКОЙ"]}], "r": []}))
    db.add(BrandCache(name="ycpb", brand_id=2102811))
    db.add(Producer(name="ИП Байкулов Д. А.", inn="090201471350", kind="entrepreneur"))
    db.commit()
    r = client.get("/v1/nkmt/dicts/hints", headers=AUTH_RO)
    assert r.status_code == 200
    hints = r.json()
    assert hints["product_types"] == ["ФУТБОЛКА", "ШАПКА"]
    # casefold-дедуп: кэш «ycpb» + дефолт «YCPB» → один вариант с дефолтным написанием
    assert hints["brands"] == ["YCPB"]
    assert hints["size_systems"] == ["ЕВРОПЕЙСКИЙ", "МЕЖДУНАРОДНЫЙ"]
    assert hints["genders"] == ["ЖЕНСКИЙ", "МУЖСКОЙ"]
    assert hints["producers"] == ["ИП Байкулов Д. А."]


def test_rules_crud_and_declaration_guard(db, client):
    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()["id"]
    r = client.post("/v1/nkmt/rules", headers=AUTH,
                    json={"brand": "YCPB", "product_types": ["ФУТБОЛКА", "ШАПКА"],
                          "declaration_id": d, "producer": "ИП Байкулов"})
    assert r.status_code == 200 and r.json()["id"]
    lst = client.get("/v1/nkmt/rules", headers=AUTH_RO).json()
    assert lst == [{"id": r.json()["id"], "brand": "YCPB",
                    "product_types": ["ФУТБОЛКА", "ШАПКА"],
                    "declaration_id": d, "declaration_number": DECL["doc_number"],
                    "declaration_date": DECL["doc_date"], "declaration_title": "",
                    "producer": "ИП Байкулов", "fields": {}}]
    # правило без условия — 400; неизвестная декларация — 404; дубль условия — 409
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"declaration_id": d}).status_code == 400
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "X", "declaration_id": 99999}).status_code == 404
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "YCPB", "product_types": ["ФУТБОЛКА", "ШАПКА"],
                             "declaration_id": d}).status_code == 409
    # матчится casefold → дубль условия в другом регистре тоже 409
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "ycpb", "product_types": ["футболка", "Шапка"],
                             "declaration_id": d}).status_code == 409
    # дубль в другом порядке видов — то же множество (семантика match_rule);
    # кириллический бренд фолдится в Python, а не lower() локали БД
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "Адель", "product_types": ["ШАПКА", "КЕПКА"],
                             "declaration_id": d}).status_code == 200
    adel = client.get("/v1/nkmt/rules", headers=AUTH_RO).json()[-1]["id"]
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "АДЕЛЬ", "product_types": ["кепка", "шапка"],
                             "declaration_id": d}).status_code == 409
    assert client.delete(f"/v1/nkmt/rules/{adel}", headers=AUTH).json() == {"ok": True}
    # пустые виды в списке выбрасываются: ["  ", ""] → [] → без условия → 400
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "", "product_types": ["  ", ""],
                             "declaration_id": d}).status_code == 400
    # декларация под правилом не удаляется; после удаления правила — удаляется
    assert client.delete(f"/v1/nkmt/declarations/{d}", headers=AUTH).status_code == 409
    assert client.delete(f"/v1/nkmt/rules/{r.json()['id']}", headers=AUTH).json() == {"ok": True}
    assert client.delete(f"/v1/nkmt/declarations/{d}", headers=AUTH).json() == {"ok": True}
    assert client.delete("/v1/nkmt/rules/99999", headers=AUTH).status_code == 404


def test_rules_fields_crud(db, client):
    """fields правила: whitelist-ключи сохраняются и возвращаются, неизвестное
    поле — 400, пустые значения нормализуются в отсутствие ключа."""
    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()["id"]
    r = client.post("/v1/nkmt/rules", headers=AUTH,
                    json={"product_types": ["ШАПКА"], "declaration_id": d,
                          "fields": {"size": "ONE SIZE", "color": "  "}})
    assert r.status_code == 200
    lst = client.get("/v1/nkmt/rules", headers=AUTH_RO).json()
    assert lst[0]["fields"] == {"size": "ONE SIZE"}
    bad = client.post("/v1/nkmt/rules", headers=AUTH,
                      json={"product_types": ["КЕПКА"], "declaration_id": d,
                            "fields": {"article": "X"}})
    assert bad.status_code == 400 and "article" in bad.json()["detail"]


def test_rules_types_casefold_dedup(db, client):
    """Виды товара дедуплицируются по casefold при записи: «ШАПКА, шапка» —
    один вид с первым написанием (роут открыт API-агентам, UI так не шлёт)."""
    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()["id"]
    r = client.post("/v1/nkmt/rules", headers=AUTH,
                    json={"product_types": ["ШАПКА", "шапка", " КЕПКА "],
                          "declaration_id": d})
    assert r.status_code == 200
    lst = client.get("/v1/nkmt/rules", headers=AUTH_RO).json()
    assert lst[0]["product_types"] == ["ШАПКА", "КЕПКА"]


def test_resolve_endpoint(db, client):
    from marko.nkmt.models import Rule
    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()["id"]
    db.add(Rule(brand="YCPB", product_types=["ФУТБОЛКА", "ШАПКА"],
                declaration_id=d, producer=""))
    db.commit()
    # правило матчится по любому виду из списка; бренд «введён» (как файловый)
    r = client.post("/v1/nkmt/resolve", headers=AUTH_RO,
                    json={"brand": "YCPB", "product_type": "ШАПКА"})
    assert r.status_code == 200
    keys = r.json()
    assert keys["declaration_number"] == {"value": DECL["doc_number"], "src": "rule"}
    assert keys["brand"] == {"value": "YCPB", "src": "file"}
    assert keys["techreg"]["src"] == "default" and keys["techreg"]["value"]
    # вид вне списка → декларация остаётся дефолтной (пустой — kv не задан)
    r2 = client.post("/v1/nkmt/resolve", headers=AUTH_RO,
                     json={"brand": "YCPB", "product_type": "КЕПКА"}).json()
    assert r2["declaration_number"] == {"value": "", "src": "default"}
