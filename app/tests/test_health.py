from fastapi.testclient import TestClient
from mpmt.api.app import create_app

def test_healthz():
    client = TestClient(create_app())
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "last_poll" in body

def test_healthz_db_down(monkeypatch):
    import mpmt.db as dbmod

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(dbmod.engine, "connect", boom)
    client = TestClient(create_app())
    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "db": "down"}
