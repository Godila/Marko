from fastapi.testclient import TestClient
from mpmt.api.app import create_app

def test_healthz():
    client = TestClient(create_app())
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
