import pytest
from fastapi.testclient import TestClient
from mpmt.api.app import create_app
from mpmt.platform.models import PlatformPrincipal, PlatformToken, hash_token
from mpmt.journal import apply_event

KM = "0104630520676025215TEST123"
INN = "090201471350"
AUTH = {"Authorization": "Bearer t1"}                 # read + docs:submit
AUTH_RO = {"Authorization": "Bearer t-ro"}            # read-only


@pytest.fixture
def client(db):
    p = PlatformPrincipal(kind="user", name="owner"); db.add(p); db.flush()
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t1"), scopes="read,docs:submit"))
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t-ro"), scopes="read"))
    db.commit()
    return TestClient(create_app())


def _sale(db, km=KM, ev="e1"):
    apply_event(db, source="wb_excise", source_event_id=ev, kind="sale",
                km=km, srid="s1", payload={"price": 1793})


def test_journal_and_batch_flow(db, client):
    _sale(db)
    r = client.get("/v1/journal?state=PENDING_WITHDRAW", headers=AUTH)
    assert r.status_code == 200 and r.json()[0]["km"] == KM
    assert r.json()[0]["state"] == "PENDING_WITHDRAW"
    assert r.json()[0]["last_event"]["price"] == 1793
    r = client.post("/v1/batches/withdraw", headers=AUTH, json={"inn": INN})
    assert r.status_code == 200 and "doc_id" in r.json()
    doc_id = r.json()["doc_id"]
    r = client.get(f"/v1/docs/{doc_id}/csv", headers=AUTH)
    assert KM in r.text


def test_stats_counts_by_state(db, client):
    _sale(db, KM, "e1")            # NEW -> PENDING_WITHDRAW
    _sale(db, "0104630520676025215TEST456", "e2")
    r = client.get("/v1/journal/stats", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"PENDING_WITHDRAW": 2}


def test_docs_list_excludes_payload(db, client):
    _sale(db)
    doc_id = client.post("/v1/batches/withdraw", headers=AUTH,
                         json={"inn": INN}).json()["doc_id"]
    r = client.get("/v1/docs", headers=AUTH)
    assert r.status_code == 200
    row = r.json()[0]
    assert row["id"] == doc_id and row["type"] == "LK_RECEIPT" and row["status"] == "draft"
    assert "payload" not in row        # payload тяжёлый — в списке его нет


def test_docs_detail_includes_payload_and_404(db, client):
    _sale(db)
    doc_id = client.post("/v1/batches/withdraw", headers=AUTH,
                         json={"inn": INN}).json()["doc_id"]
    r = client.get(f"/v1/docs/{doc_id}", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["payload"]["products"][0]["cis"] == KM
    assert client.get("/v1/docs/99999", headers=AUTH).status_code == 404
    assert client.get("/v1/docs/99999/csv", headers=AUTH).status_code == 404


def test_withdraw_nothing_pending(db, client):
    r = client.post("/v1/batches/withdraw", headers=AUTH, json={"inn": INN})
    assert r.status_code == 200 and r.json() == {"doc_id": 0}


def test_readonly_token_403_on_batches(db, client):
    assert client.post("/v1/batches/withdraw", headers=AUTH_RO,
                       json={"inn": INN}).status_code == 403
    assert client.post("/v1/batches/return", headers=AUTH_RO,
                       json={"inn": INN}).status_code == 403


def test_return_route(db, client):
    _sale(db, KM, "r1")
    client.post("/v1/batches/withdraw", headers=AUTH, json={"inn": INN})
    apply_event(db, source="wb_excise", source_event_id="r2", kind="return",
                km=KM, srid="s1", payload={"price": 1793})
    r = client.post("/v1/batches/return", headers=AUTH, json={"inn": INN})
    assert r.status_code == 200 and r.json() == {"docs": 1, "blocked": 0}
    assert client.get("/v1/journal?state=RETURNED", headers=AUTH).json()[0]["km"] == KM
