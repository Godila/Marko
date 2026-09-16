import pytest
from fastapi.testclient import TestClient
from marko.api.app import create_app
from marko.platform.models import PlatformPrincipal, PlatformToken, hash_token
from marko.journal import apply_event, log_action
from marko.journal.models import Event, Item

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
    # без кандидатов пре-флят завершается нулём (сеть не трогается) — не skipped
    assert r.status_code == 200
    assert r.json() == {"doc_id": 0,
                        "preflight": {"checked": 0, "translated": 0, "errors": 0}}


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
    from marko.platform.models import PlatformAudit
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
    from marko.platform.models import PlatformKV
    kv = db.get(PlatformKV, "emitter_defaults")
    assert kv.value["fias_id"] == "b944722c-3080-4a72-b9a5-57e11533083c"


def test_wb_returns_list_and_scopes(db, client):
    from marko.connector_wb.returns import ingest_returns
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
    from marko.api import routes_journal
    from marko.connector_wb.client import WbHttpError

    class Boom:
        def goods_return(self, a, b):
            raise WbHttpError(500, "wb down")

    monkeypatch.setattr(routes_journal, "load_wb_token", lambda p: "t")
    monkeypatch.setattr(routes_journal, "WBClient", lambda **kw: Boom())
    r = client.post("/v1/wb/returns/poll", headers=AUTH)
    assert r.status_code == 502


def test_pulse_aggregate(db, client):
    import time as _t
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from marko.connector_wb.models import WbReturn
    from marko.platform.models import PlatformKV

    _sale(db)
    client.post("/v1/batches/withdraw", headers=AUTH, json={"inn": INN})
    db.add(WbReturn(srid="r1", order_id=7, status="Готов к выдаче",
                    expired_dt="2099-01-01T10:00:00", payload={"isStatusActive": True}))
    db.execute(pg_insert(PlatformKV).values(
        key="wb_goodsreturn_usage", value={"stamps": [_t.time() - 60]}))
    db.commit()
    r = client.get("/v1/pulse", headers=AUTH_RO)
    assert r.status_code == 200
    body = r.json()
    assert body["stats"] == {"WITHDRAWN": 1}          # вывод сразу переводит КМ
    assert body["docs"] == {"LK_RECEIPT:draft": 1}
    assert body["returns"]["active"] == 1
    assert body["returns"]["nearest_deadline"].startswith("2099-01-01")
    assert body["returns"]["pending_return"] == 0
    assert body["quota"] == {"goods_return_used": 1, "goods_return_limit": 2}
    assert set(body["markers"]) == {"wb_last_poll", "signer_last_seen",
                                    "nkmt_loop_last", "returns_loop_last"}


def _anomaly(db, km=KM):
    apply_event(db, source="wb_excise", source_event_id="ret-early", kind="return",
                km=km, srid="s1",
                payload={"price": 1793, "fiscal_dt": "2026-09-01", "fiscal_doc_number": 77})


def test_resolve_anomaly(db, client):
    from marko.journal.models import Event
    from marko.platform.models import PlatformAudit
    _anomaly(db)
    assert client.get("/v1/journal?state=ANOMALY_NO_RECEIPT", headers=AUTH).json()[0]["km"] == KM
    r = client.post(f"/v1/journal/{KM}/resolve", headers=AUTH,
                    json={"target": "PENDING_RETURN", "note": "продажа до запуска контура"})
    assert r.status_code == 200
    assert r.json() == {"km": KM, "from": "ANOMALY_NO_RECEIPT", "to": "PENDING_RETURN"}
    # позиция перешла; last_event (WB-первичка для LP_RETURN) не затёрт
    row = client.get("/v1/journal?state=PENDING_RETURN", headers=AUTH).json()[0]
    assert row["km"] == KM and row["last_event"]["fiscal_doc_number"] == 77
    # аудит: событие manual/resolve + строка audit_log
    ev = db.query(Event).filter_by(kind="resolve").one()
    assert ev.source == "manual" and ev.km == KM
    assert ev.payload["from"] == "ANOMALY_NO_RECEIPT" and "контура" in ev.payload["note"]
    assert "journal.resolve" in [a.action for a in db.query(PlatformAudit).all()]
    # повторный resolve — уже не аномалия
    assert client.post(f"/v1/journal/{KM}/resolve", headers=AUTH,
                       json={"target": "NEW"}).status_code == 409


def test_resolve_guards(db, client):
    _sale(db)                                    # KM → PENDING_WITHDRAW, не аномалия
    assert client.post(f"/v1/journal/{KM}/resolve", headers=AUTH,
                       json={"target": "PENDING_RETURN"}).status_code == 409
    assert client.post("/v1/journal/0104630520676025215NOPE001/resolve", headers=AUTH,
                       json={"target": "PENDING_RETURN"}).status_code == 404
    assert client.post(f"/v1/journal/{KM}/resolve", headers=AUTH_RO,
                       json={"target": "PENDING_RETURN"}).status_code == 403
    # целевое состояние — только «нормальные», не аномалии
    assert client.post(f"/v1/journal/{KM}/resolve", headers=AUTH,
                       json={"target": "ANOMALY_RESALE"}).status_code == 422
    assert client.post(f"/v1/journal/{KM}/resolve", headers=AUTH,
                       json={"target": "NEW", "note": "x" * 501}).status_code == 422


# --- проверка КИЗ в ЧЗ (cises/info) ---

class _Cz:
    def __init__(self, answers):
        self.answers = answers

    def cises_info(self, token, cises):
        return [self.answers[km] for km in cises]


def _mt_online(monkeypatch, cz):
    from marko.connector_mt import manager
    monkeypatch.setattr(manager, "get_token", lambda d, c=None: "T")
    monkeypatch.setattr(manager, "default_client", lambda: cz)


def test_cis_sync_route(db, client, monkeypatch):
    _sale(db)
    _cz = _Cz({KM: {"cisInfo": {"status": "RETIRED", "productName": "Шапка"}}})
    _mt_online(monkeypatch, _cz)
    r = client.post("/v1/journal/cis-sync", headers=AUTH, json={})
    assert r.status_code == 200
    body = r.json()
    assert body["translated"] == 1 and body["statuses"]["retired"] == 1
    assert "items" not in body                 # пустое тело = все позиции, без карточек
    r2 = client.post("/v1/journal/cis-sync", headers=AUTH_RO, json={})
    assert r2.status_code == 403
    # явные kms: карточка КМ получает свежие поля
    r3 = client.post("/v1/journal/cis-sync", headers=AUTH, json={"kms": [KM]})
    assert [i["km"] for i in r3.json()["items"]] == [KM]


def test_cis_sync_502_on_mt_error(db, client, monkeypatch):
    from marko.connector_mt import manager
    from marko.connector_mt.client import MtHttpError
    _sale(db)

    def boom():
        raise MtHttpError(500, "cz down")
    monkeypatch.setattr(manager, "default_client", boom)
    r = client.post("/v1/journal/cis-sync", headers=AUTH, json={})
    assert r.status_code == 502


def test_withdraw_preflight_splits_batch(db, client, monkeypatch):
    """«Собрать вывод»: RETIRED без нашей претензии уходит в «вывел WB»,
    документ собирается только из реально ожидающих."""
    from marko.mt.models import MtDoc
    km2 = "0104630520676025215TEST999"
    _sale(db, KM, "p1"); _sale(db, km2, "p2")
    _mt_online(monkeypatch, _Cz({
        KM: {"cisInfo": {"status": "RETIRED"}},
        km2: {"cisInfo": {"status": "INTRODUCED"}},
    }))
    r = client.post("/v1/batches/withdraw", headers=AUTH, json={"inn": INN})
    assert r.status_code == 200
    body = r.json()
    assert body["preflight"]["translated"] == 1
    assert db.get(Item, KM).state == "WITHDRAWN" \
        and db.get(Item, KM).withdrawn_by == "wb"
    doc = db.query(MtDoc).filter(MtDoc.type == "LK_RECEIPT").one()
    assert [p["cis"] for p in doc.payload["products"]] == [km2]


def test_withdraw_preflight_fail_open(db, client, monkeypatch):
    """ЧЗ недоступен — сборка идёт как раньше, preflight=skipped."""
    _sale(db)
    # default_client уже глушится autouse-фикстурой (offline)
    r = client.post("/v1/batches/withdraw", headers=AUTH, json={"inn": INN})
    assert r.status_code == 200
    assert r.json()["preflight"]["skipped"] is True
    assert r.json()["doc_id"] > 0


def test_withdraw_source_hatch(db, client):
    _sale(db)
    r = client.post(f"/v1/journal/{KM}/withdraw-source", headers=AUTH,
                    json={"by": "wb"})
    assert r.status_code == 200
    assert r.json() == {"km": KM, "state": "WITHDRAWN", "withdrawn_by": "wb"}
    # обратный люк без нашего LK_RECEIPT — возврат потерял бы первичку: 409
    assert client.post(f"/v1/journal/{KM}/withdraw-source", headers=AUTH,
                       json={"by": "us"}).status_code == 409
    # гварды
    assert client.post(f"/v1/journal/{KM}/withdraw-source", headers=AUTH,
                       json={"by": "wb"}).status_code == 409      # уже не «к выводу»
    assert client.post("/v1/journal/0104630520676025215NOPE002/withdraw-source",
                       headers=AUTH, json={"by": "wb"}).status_code == 404
    assert client.post(f"/v1/journal/{KM}/withdraw-source", headers=AUTH_RO,
                       json={"by": "wb"}).status_code == 403


def test_withdraw_source_us_with_receipt(db, client):
    """Обратный люк при наличии нашей первички: 'wb'→'us' меняет только пометку."""
    _sale(db)
    client.post("/v1/batches/withdraw", headers=AUTH, json={"inn": INN})
    db.get(Item, KM).withdrawn_by = "wb"; db.commit()      # как после гварда
    r = client.post(f"/v1/journal/{KM}/withdraw-source", headers=AUTH,
                    json={"by": "us"})
    assert r.status_code == 200
    assert r.json() == {"km": KM, "state": "WITHDRAWN", "withdrawn_by": "us"}


# --- удаление черновика документа ---

def test_docs_delete_reverts_items(db, client):
    from marko.mt.models import MtDoc
    _sale(db)
    doc_id = client.post("/v1/batches/withdraw", headers=AUTH,
                         json={"inn": INN}).json()["doc_id"]
    r = client.delete(f"/v1/docs/{doc_id}", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"deleted": True, "reverted": 1, "skipped": 0}
    it = db.get(Item, KM)
    assert it.state == "PENDING_WITHDRAW" and it.withdrawn_by == ""
    assert db.get(MtDoc, doc_id) is None
    ev = db.query(Event).filter_by(kind="revert").one()
    assert ev.source == "manual" and ev.source_event_id == f"docdel:{doc_id}:{KM}"
    from marko.platform.models import PlatformAudit
    assert "doc.delete" in [a.action for a in db.query(PlatformAudit).all()]
    # повторное удаление — 404
    assert client.delete(f"/v1/docs/{doc_id}", headers=AUTH).status_code == 404


def test_docs_delete_guards(db, client):
    from marko.mt.models import MtDoc
    _sale(db)
    doc_id = client.post("/v1/batches/withdraw", headers=AUTH,
                         json={"inn": INN}).json()["doc_id"]
    db.get(MtDoc, doc_id).status = "submitted"; db.commit()
    assert client.delete(f"/v1/docs/{doc_id}", headers=AUTH).status_code == 409
    db.get(MtDoc, doc_id).status = "draft"; db.commit()
    # КМ ушёл в возврат: удаление сломало бы первичку LP_RETURN — запрет
    apply_event(db, source="wb_excise", source_event_id="w-ret", kind="return",
                km=KM, srid="s", payload={"price": 1})
    assert db.get(Item, KM).state == "PENDING_RETURN"
    assert client.delete(f"/v1/docs/{doc_id}", headers=AUTH).status_code == 409
    assert client.delete(f"/v1/docs/{doc_id}", headers=AUTH_RO).status_code == 403
    assert client.delete("/v1/docs/99999", headers=AUTH).status_code == 404


def test_docs_delete_skips_wb_marked(db, client):
    """Код из черновика, помеченный «вывел WB» — реальность ЧЗ, откату не подлежит."""
    from marko.mt.models import MtDoc
    _sale(db)
    doc_id = client.post("/v1/batches/withdraw", headers=AUTH,
                         json={"inn": INN}).json()["doc_id"]
    it = db.get(Item, KM)
    it.withdrawn_by = "wb"; db.commit()
    r = client.delete(f"/v1/docs/{doc_id}", headers=AUTH)
    assert r.json() == {"deleted": True, "reverted": 0, "skipped": 1}
    assert db.get(Item, KM).state == "WITHDRAWN"      # не откачен


def test_docs_delete_lp_return_reverts(db, client):
    from marko.mt.models import MtDoc
    _sale(db)
    client.post("/v1/batches/withdraw", headers=AUTH, json={"inn": INN})
    apply_event(db, source="wb_excise", source_event_id="lp-ret", kind="return",
                km=KM, srid="s", payload={"price": 1793, "fiscal_dt": "2026-09-02",
                                          "fiscal_doc_number": "7"})
    r = client.post("/v1/batches/return", headers=AUTH, json={"inn": INN})
    assert r.json() == {"docs": 1, "blocked": 0}
    lp_id = db.query(MtDoc).filter(MtDoc.type == "LP_RETURN").one().id
    r2 = client.delete(f"/v1/docs/{lp_id}", headers=AUTH)
    assert r2.json() == {"deleted": True, "reverted": 1, "skipped": 0}
    assert db.get(Item, KM).state == "PENDING_RETURN"


def test_docs_delete_allows_when_older_receipt_exists(db, client):
    """КМ в возвратном контуре, но первичкой служит СТАРЫЙ вывод — удаление
    нового черновика безвредно (P2 ревью: ложный 409)."""
    from marko.mt.models import MtDoc
    _sale(db, KM, "o1")
    doc1 = client.post("/v1/batches/withdraw", headers=AUTH,
                       json={"inn": INN, "limit": 1}).json()["doc_id"]
    apply_event(db, source="wb_excise", source_event_id="o-ret", kind="return",
                km=KM, srid="s", payload={"price": 1})
    _sale(db, KM, "o2")                                  # перепродажа
    doc2 = client.post("/v1/batches/withdraw", headers=AUTH,
                       json={"inn": INN, "limit": 1}).json()["doc_id"]
    apply_event(db, source="wb_excise", source_event_id="o-ret2", kind="return",
                km=KM, srid="s", payload={"price": 1})
    assert db.get(Item, KM).state == "PENDING_RETURN"
    r = client.delete(f"/v1/docs/{doc2}", headers=AUTH)
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert db.get(MtDoc, doc1) is not None              # старый вывод жив


def test_withdraw_preflight_covers_unchecked_tail(db, client, monkeypatch):
    """Очередь > limit: переведённые уходят, сборка добирает хвост — цикл
    пре-флайта проверяет и его, документ собирается только из проверенных."""
    from marko.mt.models import MtDoc
    kms = [f"0104630520676025215TAIL{i:02d}" for i in range(4)]
    for i, km in enumerate(kms):
        _sale(db, km, f"t{i}")
    answers = {km: {"cisInfo": {"cis": km, "status": "RETIRED"}} for km in kms[:2]}
    answers.update({km: {"cisInfo": {"cis": km, "status": "INTRODUCED"}} for km in kms[2:]})
    _mt_online(monkeypatch, _Cz(answers))
    r = client.post("/v1/batches/withdraw", headers=AUTH,
                    json={"inn": INN, "limit": 2})
    body = r.json()
    # раунд 1: TAIL00-01 (retd) → переведены; раунд 2: хвост TAIL02-03 проверен
    assert body["preflight"]["checked"] == 4 and body["preflight"]["translated"] == 2
    doc = db.query(MtDoc).filter(MtDoc.type == "LK_RECEIPT").one()
    assert sorted(p["cis"] for p in doc.payload["products"]) == sorted(kms[2:])


def test_withdraw_source_us_requires_receipt(db, client):
    _sale(db)
    client.post(f"/v1/journal/{KM}/withdraw-source", headers=AUTH, json={"by": "wb"})
    # 'us' без нашего LK_RECEIPT — возврат потеряет первичку: 409
    r = client.post(f"/v1/journal/{KM}/withdraw-source", headers=AUTH, json={"by": "us"})
    assert r.status_code == 409


def test_resolve_no_receipt_feeds_return_batch(db, client):
    # регресс ревью: «продажа была до запуска» → PENDING_RETURN обязан
    # превращаться в LP_RETURN (RETAIL_RETURN из чека), а не виснуть blocked:
    # resolve проставляет withdrawn_by='wb', когда нашего LK_RECEIPT нет
    from marko.journal.models import Item
    from marko.mt.models import MtDoc
    _anomaly(db)
    r = client.post(f"/v1/journal/{KM}/resolve", headers=AUTH,
                    json={"target": "PENDING_RETURN", "note": "продажа до запуска контура"})
    assert r.status_code == 200
    assert db.get(Item, KM).withdrawn_by == "wb"
    rr = client.post("/v1/batches/return", headers=AUTH, json={"inn": INN})
    assert rr.status_code == 200 and rr.json() == {"docs": 1, "blocked": 0}
    doc = db.query(MtDoc).filter(MtDoc.type == "LP_RETURN").one()
    assert doc.payload["return_type"] == "RETAIL_RETURN"
    assert doc.payload["primary_document_number"] == "77"


def test_resolve_withdrawn_sets_source(db, client):
    from marko.journal.models import Item
    apply_event(db, source="wb_excise", source_event_id="s-1", kind="sale",
                km=KM, srid="s1", payload={"price": 1})
    apply_event(db, source="wb_excise", source_event_id="s-2", kind="sale",
                km=KM, srid="s2", payload={"price": 1})       # → ANOMALY_RESALE
    r = client.post(f"/v1/journal/{KM}/resolve", headers=AUTH,
                    json={"target": "WITHDRAWN", "note": "уже выведен"})
    assert r.status_code == 200
    it = db.get(Item, KM)
    assert it.state == "WITHDRAWN" and it.withdrawn_by == "wb"


# --- lookup заказа WB (маппинг КМ ↔ ID заказа для отладки) ---
WB_UUID = "i9ba767bf5642be309fb036281a0ecda7"
WB_DOC = f"eBQ.{WB_UUID}"


def _wb_order(db, doc=WB_DOC, delivery_type="fbs", nm_id=412477053):
    from marko.connector_wb.models import WbOrder
    db.add(WbOrder(order_doc=doc, delivery_type=delivery_type, nm_id=nm_id,
                   order_created_at="2026-09-11T15:34:06Z"))
    db.commit()


def _wb_sale(db, km, srid, ev=None):
    apply_event(db, source="wb_excise", source_event_id=ev or f"{srid}:{km}",
                kind="sale", km=km, srid=srid, payload={"price": 1793, "srid": srid})


def test_wb_lookup_found_tail_mismatch(db, client):
    # хвосты '.n.m' расходятся между вводом юзера и строкой эксайза —
    # совпадение по документу без хвоста
    _wb_order(db)
    _wb_sale(db, KM, f"{WB_DOC}.0.0")
    _wb_sale(db, "0104630520676025215TEST789", f"{WB_DOC}.3.0")
    r = client.get(f"/v1/wb/lookup?rid={WB_DOC}.7.0", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["order_doc"] == WB_DOC and body["status"] == "found"
    assert body["order"]["delivery_type"] == "fbs"
    assert body["order"]["nm_id"] == 412477053
    assert {it["km"] for it in body["items"]} == {KM, "0104630520676025215TEST789"}
    assert set(body["items"][0]) == {"km", "state", "withdrawn_by", "updated_at",
                                     "last_event", "cis_status", "cis_product_name",
                                     "cis_checked_at"}


def test_wb_lookup_doc_without_tail_and_bare_uuid(db, client):
    # без хвоста и голый uuid32 (префикс неизвестен — контейнмент-поиск)
    _wb_order(db)
    _wb_sale(db, KM, f"{WB_DOC}.0.0")
    for rid in (WB_DOC, WB_UUID):
        r = client.get(f"/v1/wb/lookup?rid={rid}", headers=AUTH)
        assert r.status_code == 200 and r.json()["status"] == "found", rid
        assert r.json()["items"][0]["km"] == KM


def test_wb_lookup_status_lag(db, client):
    # заказ в реестре есть, строк эксайза ещё нет — типичный живой кейс
    _wb_order(db)
    r = client.get(f"/v1/wb/lookup?rid={WB_DOC}", headers=AUTH)
    assert r.json()["status"] == "lag" and r.json()["items"] == []


def test_wb_lookup_status_fbw(db, client):
    # skip_fbw не создаёт Item: события есть, позиций нет — не «лаг»
    _wb_order(db, delivery_type="fbo")
    log_action(db, source="wb_excise", source_event_id="fbw1", kind="skip_fbw",
               km=KM, srid=f"{WB_DOC}.0.0", payload={})
    r = client.get(f"/v1/wb/lookup?rid={WB_DOC}", headers=AUTH)
    assert r.json()["status"] == "fbw" and r.json()["items"] == []


def test_wb_lookup_status_unknown_and_neighbour_doc(db, client):
    r = client.get("/v1/wb/lookup?rid=eQ.ffffffffffffffffffffffffffffffff", headers=AUTH)
    assert r.json()["status"] == "unknown" and r.json()["order"] is None
    # соседний числовой документ не должен матчиться префиксом ('12345' vs '123456')
    _wb_sale(db, KM, "eN.123456.0.0")
    r = client.get("/v1/wb/lookup?rid=eN.12345", headers=AUTH)
    assert r.json()["status"] == "unknown"


def test_wb_lookup_found_without_registry_row(db, client):
    # выкупленный заказ мог уйти из снапшота WB до прогрева реестра —
    # события и позиции валидны и без строки wb.orders
    _wb_sale(db, KM, f"{WB_DOC}.0.0")
    r = client.get(f"/v1/wb/lookup?rid={WB_DOC}", headers=AUTH)
    assert r.json()["status"] == "found" and r.json()["order"] is None


def test_wb_lookup_scopes_and_validation(db, client):
    assert client.get(f"/v1/wb/lookup?rid={WB_DOC}", headers=AUTH_RO).status_code == 200
    assert client.get(f"/v1/wb/lookup?rid={WB_DOC}").status_code == 401
    assert client.get("/v1/wb/lookup", headers=AUTH).status_code == 422
    for bad in ("  ", ".1.0", "x"):
        assert client.get(f"/v1/wb/lookup?rid={bad}", headers=AUTH).status_code == 422, bad


def test_wb_lookup_prefixed_input_finds_legacy_bare_srid(db, client):
    # легаси-строки с голым uuid в srid (без префикса) должен находить и
    # ввод в текущем формате WB 'eBQ.<uuid>' — контейнмент по телу документа
    _wb_order(db)
    _wb_sale(db, KM, WB_UUID)
    r = client.get(f"/v1/wb/lookup?rid={WB_DOC}.0.0", headers=AUTH)
    assert r.json()["status"] == "found" and r.json()["items"][0]["km"] == KM


def test_wb_lookup_like_metacharacters_literal(db, client):
    # '%', '_', '\' в rid — литералы, не wildcards; чужой заказ не матчится
    # (rid с '%' в query требует кодирования — идём через params)
    weird = "eQ.a%b_c%704f48e1a65d4843837f4e20ae3d1d9"
    _wb_sale(db, KM, f"{weird}.0.0")
    r = client.get("/v1/wb/lookup", params={"rid": weird}, headers=AUTH)
    assert r.json()["status"] == "found"          # сам себя находит (эскейп работает)
    r = client.get("/v1/wb/lookup", params={"rid": "eQ.abbXc"}, headers=AUTH)
    assert r.json()["status"] == "unknown"        # % и _ не раскрылись в wildcard


def test_wb_lookup_found_wins_over_fbw(db, client):
    # если по документу есть и позиции, и skip_fbw-шум — приоритет found
    _wb_order(db)
    _wb_sale(db, KM, f"{WB_DOC}.0.0")
    log_action(db, source="wb_excise", source_event_id="fbw-x", kind="skip_fbw",
               km="0104630520676025215TESTFBW", srid=f"{WB_DOC}.1.0", payload={})
    r = client.get(f"/v1/wb/lookup?rid={WB_DOC}", headers=AUTH)
    assert r.json()["status"] == "found"
    assert {it["km"] for it in r.json()["items"]} == {KM}
