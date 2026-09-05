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
