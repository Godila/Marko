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
    assert r.json()["id"] == client.get("/v1/nkmt/declarations", headers=AUTH_RO).json()[0]["id"]
    dup = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL)
    assert dup.status_code == 409
    assert client.delete(f"/v1/nkmt/declarations/{r.json()['id']}", headers=AUTH).json() == {"ok": True}
    assert client.get("/v1/nkmt/declarations", headers=AUTH_RO).json() == []


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
    """Подсказки для правил: пресеты вида товара из кэша атрибутных моделей
    + бренды из brand_cache и дефолта."""
    from marko.nkmt.models import BrandCache
    from marko.platform.models import PlatformKV
    db.add(PlatformKV(key="nk_attrs:6109100000", value={"m": [
        {"attr_id": 12, "attr_name": "Вид товара", "attr_preset": ["ФУТБОЛКА", "ШАПКА"]}], "r": []}))
    db.add(BrandCache(name="ycpb", brand_id=2102811))
    db.commit()
    r = client.get("/v1/nkmt/dicts/hints", headers=AUTH_RO)
    assert r.status_code == 200
    hints = r.json()
    assert hints["product_types"] == ["ФУТБОЛКА", "ШАПКА"]
    # casefold-дедуп: кэш «ycpb» + дефолт «YCPB» → один вариант с дефолтным написанием
    assert hints["brands"] == ["YCPB"]


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
                    "declaration_date": DECL["doc_date"], "producer": "ИП Байкулов"}]
    # правило без условия — 400; неизвестная декларация — 404; точный дубль условия — 409
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"declaration_id": d}).status_code == 400
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "X", "declaration_id": 99999}).status_code == 404
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "YCPB", "product_types": ["ФУТБОЛКА", "ШАПКА"],
                             "declaration_id": d}).status_code == 409
    # пустые виды в списке выбрасываются: ["  ", ""] → [] → без условия → 400
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "", "product_types": ["  ", ""],
                             "declaration_id": d}).status_code == 400
    # декларация под правилом не удаляется; после удаления правила — удаляется
    assert client.delete(f"/v1/nkmt/declarations/{d}", headers=AUTH).status_code == 409
    assert client.delete(f"/v1/nkmt/rules/{r.json()['id']}", headers=AUTH).json() == {"ok": True}
    assert client.delete(f"/v1/nkmt/declarations/{d}", headers=AUTH).json() == {"ok": True}
    assert client.delete("/v1/nkmt/rules/99999", headers=AUTH).status_code == 404


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
