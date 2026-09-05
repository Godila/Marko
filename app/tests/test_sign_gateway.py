import pytest
from fastapi.testclient import TestClient
from marko.api.app import create_app
from marko.platform.models import PlatformPrincipal, PlatformToken, hash_token
from marko.sign.models import SignTask


@pytest.fixture
def client(db):
    p = PlatformPrincipal(kind="machine", name="signer-agent"); db.add(p); db.flush()
    p2 = PlatformPrincipal(kind="user", name="owner"); db.add(p2); db.flush()
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t-signer"), scopes="signer"))
    db.add(PlatformToken(principal_id=p2.id, token_hash=hash_token("t-admin"), scopes="admin,read,docs:submit"))
    db.add(PlatformToken(principal_id=p2.id, token_hash=hash_token("t-read"), scopes="read"))
    db.commit()
    return TestClient(create_app())


def _mk_task(client, **body):
    r = client.post("/v1/sign/test-task", headers={"Authorization": "Bearer t-admin"}, json=body)
    assert r.status_code == 200
    return r.json()["task_id"]


def test_ping(client):
    assert client.get("/v1/sign/ping", headers={"Authorization": "Bearer t-signer"}).json() == {"ok": True}
    assert client.get("/v1/sign/ping").status_code == 401
    assert client.get("/v1/sign/ping", headers={"Authorization": "Bearer t-read"}).status_code == 403


def test_lease_empty_204(client):
    r = client.post("/v1/sign/lease?wait=0", headers={"Authorization": "Bearer t-signer"})
    assert r.status_code == 204


def test_full_flow(client):
    tid = _mk_task(client, type="auth_sign", data="GNUFBAZB")
    r = client.post("/v1/sign/lease?wait=1", headers={"Authorization": "Bearer t-signer"})
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == tid and body["type"] == "auth_sign"
    assert body["payload"] == {"data": "GNUFBAZB"}
    r = client.post("/v1/sign/results", headers={"Authorization": "Bearer t-signer"},
                    json={"task_id": tid, "signature_b64": "Zm9vYmFy"})
    assert r.status_code == 200
    r = client.post("/v1/sign/results", headers={"Authorization": "Bearer t-signer"},
                    json={"task_id": tid, "signature_b64": "again"})
    assert r.status_code == 409


def test_result_error_path(client, db):
    tid = _mk_task(client, type="doc_sign", data_b64="QUJD")
    client.post("/v1/sign/lease?wait=1", headers={"Authorization": "Bearer t-signer"})
    r = client.post("/v1/sign/results", headers={"Authorization": "Bearer t-signer"},
                    json={"task_id": tid, "error": "no cert"})
    assert r.status_code == 200
    assert db.get(SignTask, tid).status == "error"


def test_expired_lease_requeued_and_404(client, db):
    from datetime import datetime, timedelta
    tid = _mk_task(client, type="auth_sign", data="X")
    client.post("/v1/sign/lease?wait=1", headers={"Authorization": "Bearer t-signer"})
    t = db.get(SignTask, tid)
    t.lease_until = datetime.utcnow() - timedelta(seconds=1)
    db.commit()
    # lease снова выдаёт ту же задачу (attempt растёт)
    r = client.post("/v1/sign/lease?wait=1", headers={"Authorization": "Bearer t-signer"})
    db.expire_all()   # lease шёл через другую сессию — сбросить identity map
    assert r.json()["task_id"] == tid and db.get(SignTask, tid).attempt == 2
    # результат по переигранной задаче (pending) → 404
    t2 = db.get(SignTask, tid); t2.status = "pending"; db.commit()
    r = client.post("/v1/sign/results", headers={"Authorization": "Bearer t-signer"},
                    json={"task_id": tid, "signature_b64": "zzz"})
    assert r.status_code == 404


def test_test_task_scopes(client):
    r = client.post("/v1/sign/test-task", headers={"Authorization": "Bearer t-signer"},
                    json={"type": "auth_sign", "data": "x"})
    assert r.status_code == 403
    r = client.post("/v1/sign/test-task", headers={"Authorization": "Bearer t-admin"},
                    json={"type": "bad", "data": "x"})
    assert r.status_code == 422
