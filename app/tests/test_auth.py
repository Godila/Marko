import pytest
from fastapi.testclient import TestClient
from mpmt.api.app import create_app
from mpmt.platform.models import PlatformPrincipal, PlatformToken, hash_token

@pytest.fixture
def client(db):
    p = PlatformPrincipal(kind="machine", name="agent"); db.add(p); db.flush()
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("tok-read"), scopes="read"))
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("tok-admin"), scopes="read,admin"))
    db.commit()
    return TestClient(create_app())

def test_me_ok(client):
    r = client.get("/v1/me", headers={"Authorization": "Bearer tok-admin"})
    assert r.status_code == 200 and r.json() == {"name": "agent", "scopes": ["read", "admin"]}

def test_missing_token_401(client):
    assert client.get("/v1/me").status_code == 401

def test_wrong_scope_403(client):
    r = client.get("/v1/me", headers={"Authorization": "Bearer tok-read"})
    assert r.status_code == 403
