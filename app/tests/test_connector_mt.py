import base64
import json
from datetime import datetime, timedelta

import pytest

from mpmt.connector_mt import manager
from mpmt.journal import apply_event
from mpmt.emitter.batch import withdraw_batch
from mpmt.mt.models import MtDoc
from mpmt.platform.models import PlatformKV
from mpmt.sign import service as sign_service

INN = "090201471350"


class FakeMtClient:
    def __init__(self):
        self.calls = []
        self.doc_status = "CHECKED_OK"

    def auth_key(self):
        self.calls.append("auth_key")
        return {"uuid": "u-1", "data": "GNUFBAZB"}

    def sign_in(self, uuid, signature_b64, inn):
        self.calls.append(("sign_in", uuid, signature_b64, inn))
        return {"token": "TOKEN-1", "expireDate":
                (datetime.utcnow() + timedelta(hours=10)).isoformat() + "Z"}

    def create_doc_signed(self, token, doc_type, product_document_b64, signature_b64):
        self.calls.append(("create", token, doc_type))
        return "EXT-UUID-1"

    def doc_info(self, token, doc_uuid):
        self.calls.append(("info", doc_uuid))
        return {"status": self.doc_status}


@pytest.fixture
def sg(db, monkeypatch):
    """Подменяем ожидание подписи: сразу закрываем задачу валидным результатом."""
    def fake_sign(dbs, type_, payload):
        tid = sign_service.create_task(dbs, type_, payload)
        sign_service.try_acquire(dbs, owner="test")     # эмуляция lease агентом
        sign_service.submit_result(dbs, tid, "SIG-B64", None)
        return "SIG-B64"
    monkeypatch.setattr(manager, "_sign_via_gateway", fake_sign)
    return fake_sign


def test_get_token_and_cache(db, sg):
    c = FakeMtClient()
    t1 = manager.get_token(db, c)
    assert t1 == "TOKEN-1"
    assert ("sign_in", "u-1", "SIG-B64", INN) in c.calls
    kv = db.get(PlatformKV, "mt_token")
    assert kv.value["token"] == "TOKEN-1"
    c2 = FakeMtClient()
    t2 = manager.get_token(db, c2)          # из кэша — клиент не дёргается
    assert t2 == "TOKEN-1" and c2.calls == []


def test_sign_via_gateway_roundtrip(db):
    tid = sign_service.create_task(db, "doc_sign", {"data_b64": "QQ=="})
    sign_service.try_acquire(db, owner="test")
    sign_service.submit_result(db, tid, "REAL-SIG", None)
    db.expire_all()
    st = sign_service.get_result(db, tid)
    assert st["status"] == "done"
    assert st["result"]["signature_b64"] == "REAL-SIG"


def test_get_token_uuid_form(db, sg):
    """ЧЗ в UUID-форме отдаёт токен в поле uuidToken (прод-поведение)."""
    class UuidClient(FakeMtClient):
        def sign_in(self, uuid, signature_b64, inn):
            return {"uuidToken": "UUID-TOK",
                    "expireDate": (datetime.utcnow() + timedelta(hours=10)).isoformat() + "Z"}
    assert manager.get_token(db, UuidClient()) == "UUID-TOK"


def _draft_doc(db):
    km = "0104630520676025215MTTEST1"
    apply_event(db, source="wb_excise", source_event_id="m:1", kind="sale",
                km=km, srid="m", payload={"price": 100, "fiscal_dt": "2026-09-01",
                                          "fiscal_doc_number": "9"})
    doc_id = withdraw_batch(db, INN)
    return db.get(MtDoc, doc_id)


def test_submit_doc(db, sg):
    c = FakeMtClient()
    doc = _draft_doc(db)
    ext = manager.submit_doc(db, doc.id, c)
    assert ext == "EXT-UUID-1"
    doc2 = db.get(MtDoc, doc.id)
    assert doc2.status == "submitted" and doc2.external_id == "EXT-UUID-1"
    create_call = [x for x in c.calls if x[0] == "create"][0]
    assert create_call[1] == "TOKEN-1" and create_call[2] == "LK_RECEIPT"


def test_submit_error_rolls_back_to_error(db, sg, monkeypatch):
    c = FakeMtClient()
    def boom(*a, **k): raise RuntimeError("mt down")
    monkeypatch.setattr(c, "create_doc_signed", boom)
    doc = _draft_doc(db)
    with pytest.raises(RuntimeError):
        manager.submit_doc(db, doc.id, c)
    assert db.get(MtDoc, doc.id).status == "error"


def test_check_doc_checked_ok(db, sg):
    c = FakeMtClient()
    doc = _draft_doc(db)
    manager.submit_doc(db, doc.id, c)
    info = manager.check_doc(db, doc.id, c)
    assert info["status"] == "CHECKED_OK"
    assert db.get(MtDoc, doc.id).status == "checked_ok"


def test_check_doc_rejected(db, sg):
    c = FakeMtClient()
    doc = _draft_doc(db)
    manager.submit_doc(db, doc.id, c)
    c.doc_status = "REJECTED"
    manager.check_doc(db, doc.id, c)
    assert db.get(MtDoc, doc.id).status == "error"
