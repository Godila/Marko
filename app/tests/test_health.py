from fastapi.testclient import TestClient
from marko.api.app import create_app

def test_healthz():
    client = TestClient(create_app())
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "wb_last_poll" in body and "signer_last_seen" in body

def test_healthz_db_down(monkeypatch):
    import marko.db as dbmod

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(dbmod.engine, "connect", boom)
    client = TestClient(create_app())
    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "db": "down"}
