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

def test_mutation_audit_rows_written(db, client):
    from mpmt.platform.models import PlatformAudit
    apply_event(db, source="wb_excise", source_event_id="a:1", kind="sale",
                km="0104630520676025215AUDIT001", srid="a", payload={"price": 100})
    r = client.post("/v1/batches/withdraw", headers={"Authorization": "Bearer t1"},
                    json={"inn": "090201471350"})
    assert r.status_code == 200
    actions = [a.action for a in db.query(PlatformAudit).all()]
    assert "batch.withdraw" in actions


def test_emitter_defaults_roundtrip(db, client):
    r = client.get("/v1/emitter/defaults", headers=AUTH_RO)   # read хватает на GET
    assert r.status_code == 200 and r.json() == {"fias_id": "", "primary_custom_name": ""}
    r = client.put("/v1/emitter/defaults", headers=AUTH_RO,   # PUT требует docs:submit
                   json={"fias_id": "b944722c-3080-4a72-b9a5-57e11533083c"})
    assert r.status_code == 403
    r = client.put("/v1/emitter/defaults", headers=AUTH,
                   json={"fias_id": "b944722c-3080-4a72-b9a5-57e11533083c",
                         "primary_custom_name": "Чек дистанционной продажи Wildberries"})
    assert r.status_code == 200
    from mpmt.platform.models import PlatformKV
    kv = db.get(PlatformKV, "emitter_defaults")
    assert kv.value["fias_id"] == "b944722c-3080-4a72-b9a5-57e11533083c"


def test_wb_returns_list_and_scopes(db, client):
    from mpmt.connector_wb.returns import ingest_returns
    ingest_returns(db, [{"srid": "r1", "orderId": 7, "status": "Готов к выдаче",
                         "expiredDt": "2026-09-10T10:00:00", "reason": "Размер",
                         "subjectName": "Шапка", "isStatusActive": 1, "completedDt": None}])
    r = client.get("/v1/wb/returns", headers=AUTH_RO)
    assert r.status_code == 200
    row = r.json()[0]
    assert row["srid"] == "r1" and row["order_id"] == 7 and row["is_active"] is True
    assert row["reason"] == "Размер" and row["completed_dt"] is None
    r = client.get("/v1/wb/returns?active=false", headers=AUTH_RO)
    assert r.json() == []                                  # активная строка отфильтрована
    r = client.post("/v1/wb/returns/poll", headers=AUTH_RO)  # poll требует docs:submit
    assert r.status_code == 403


def test_wb_returns_poll_502_on_wb_error(db, client, monkeypatch):
    from mpmt.api import routes_journal
    from mpmt.connector_wb.client import WbHttpError

    class Boom:
        def goods_return(self, a, b):
            raise WbHttpError(500, "wb down")

    monkeypatch.setattr(routes_journal, "load_wb_token", lambda p: "t")
    monkeypatch.setattr(routes_journal, "WBClient", lambda **kw: Boom())
    r = client.post("/v1/wb/returns/poll", headers=AUTH)
    assert r.status_code == 502
