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


def test_rules_crud_and_declaration_guard(db, client):
    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()["id"]
    r = client.post("/v1/nkmt/rules", headers=AUTH,
                    json={"brand": "YCPB", "product_type": "", "declaration_id": d,
                          "producer": "ИП Байкулов"})
    assert r.status_code == 200 and r.json()["id"]
    lst = client.get("/v1/nkmt/rules", headers=AUTH_RO).json()
    assert lst == [{"id": r.json()["id"], "brand": "YCPB", "product_type": "",
                    "declaration_id": d, "declaration_number": DECL["doc_number"],
                    "declaration_date": DECL["doc_date"], "producer": "ИП Байкулов"}]
    # правило без условия — 400; неизвестная декларация — 404; точный дубль условия — 409
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"declaration_id": d}).status_code == 400
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "X", "declaration_id": 99999}).status_code == 404
    assert client.post("/v1/nkmt/rules", headers=AUTH,
                       json={"brand": "YCPB", "declaration_id": d}).status_code == 409
    # декларация под правилом не удаляется; после удаления правила — удаляется
    assert client.delete(f"/v1/nkmt/declarations/{d}", headers=AUTH).status_code == 409
    assert client.delete(f"/v1/nkmt/rules/{r.json()['id']}", headers=AUTH).json() == {"ok": True}
    assert client.delete(f"/v1/nkmt/declarations/{d}", headers=AUTH).json() == {"ok": True}
    assert client.delete("/v1/nkmt/rules/99999", headers=AUTH).status_code == 404


def test_brands_crud_and_declaration_guard(db, client):
    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()["id"]
    r = client.post("/v1/nkmt/brands", headers=AUTH,
                    json={"name": "YCPB", "producer": "ИП Байкулов", "declaration_id": d})
    assert r.status_code == 200 and r.json()["id"]
    lst = client.get("/v1/nkmt/brands", headers=AUTH_RO).json()
    assert lst[0]["name"] == "YCPB" and lst[0]["producer"] == "ИП Байкулов"
    assert lst[0]["declaration_number"] == DECL["doc_number"]
    # пустое имя — 400; чужая декларация — 404; дубль casefold — 409
    assert client.post("/v1/nkmt/brands", headers=AUTH,
                       json={"name": "  "}).status_code == 400
    assert client.post("/v1/nkmt/brands", headers=AUTH,
                       json={"name": "X", "declaration_id": 99999}).status_code == 404
    assert client.post("/v1/nkmt/brands", headers=AUTH,
                       json={"name": "ycpb"}).status_code == 409
    # декларация под записью справочника не удаляется; после удаления бренда — да
    assert client.delete(f"/v1/nkmt/declarations/{d}", headers=AUTH).status_code == 409
    assert client.delete(f"/v1/nkmt/brands/{r.json()['id']}", headers=AUTH).json() == {"ok": True}
    assert client.delete(f"/v1/nkmt/declarations/{d}", headers=AUTH).json() == {"ok": True}
    assert client.delete("/v1/nkmt/brands/99999", headers=AUTH).status_code == 404


def test_resolve_endpoint(db, client):
    from marko.nkmt.models import Brand, Rule
    d = client.post("/v1/nkmt/declarations", headers=AUTH, json=DECL).json()["id"]
    db.add(Rule(brand="YCPB", product_type="ФУТБОЛКА", declaration_id=d, producer=""))
    db.add(Brand(name="КЛИЕНТ", producer="Фабрика клиента"))
    db.commit()
    # правило: декларация от правила, бренд «введён» (как файловый)
    r = client.post("/v1/nkmt/resolve", headers=AUTH_RO,
                    json={"brand": "YCPB", "product_type": "ФУТБОЛКА"})
    assert r.status_code == 200
    keys = r.json()
    assert keys["declaration_number"] == {"value": DECL["doc_number"], "src": "rule"}
    assert keys["brand"] == {"value": "YCPB", "src": "file"}
    assert keys["techreg"]["src"] == "default" and keys["techreg"]["value"]
    # справочник бренда: producer оттуда, декларация — дефолт
    r2 = client.post("/v1/nkmt/resolve", headers=AUTH_RO,
                     json={"brand": "клиент", "product_type": "ШАПКА"}).json()
    assert r2["producer"] == {"value": "Фабрика клиента", "src": "dict"}
    assert r2["declaration_number"]["src"] == "default"
