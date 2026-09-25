"""Справочник идентификаторов WB: любой ключ → всё известное (read-only).

Классификация, локальные срезы по каждому типу ключа и живой слой
закреплённых sgtin (orders/meta) с kv-кэшем.
"""
import time

import pytest
from fastapi.testclient import TestClient
from marko.api.app import create_app
from marko.journal import apply_event
from marko.journal.identify import IdentifyError, classify, parse_meta
from marko.journal.trace import GS
from marko.platform.models import PlatformPrincipal, PlatformToken, hash_token
from sqlalchemy.dialects.postgresql import insert as pg_insert

KM = "0104630520676025215IDENT001"            # журнальная (короткая тестовая) форма
KM2 = "0104630520676025215UKsE;hVmAtad"       # прод-формат, 31 символ
GTIN = "04630520676025"
AUTH = {"Authorization": "Bearer t1"}
AUTH_RO = {"Authorization": "Bearer t-ro"}
WB_UUID = "rddb532e8c7d9414d964fdf7ab32dfc4"
WB_DOC = f"eAc.{WB_UUID}"
RID = f"{WB_DOC}.0.0"
ORDER_ID = 5632423
SUPPLY = "WB-GI-273320060"


@pytest.fixture
def client(db):
    p = PlatformPrincipal(kind="user", name="owner"); db.add(p); db.flush()
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t1"), scopes="read,docs:submit"))
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t-ro"), scopes="read"))
    db.commit()
    return TestClient(create_app())


def _sale(db, km=KM, ev="e1", srid=RID, **payload):
    apply_event(db, source="wb_excise", source_event_id=ev, kind="sale",
                km=km, srid=srid, payload={"price": 1793, "nm_id": 412477053, **payload})


def _order_row(db, doc=WB_DOC, order_id=ORDER_ID, supply_id=SUPPLY):
    from marko.connector_wb.models import WbOrder
    db.add(WbOrder(order_doc=doc, order_id=order_id, supply_id=supply_id,
                   delivery_type="fbs", nm_id=412477053,
                   order_created_at="2026-09-11T15:34:06Z"))
    db.commit()


class FakeWb:
    """orders_meta с полным КиЗ (GS + криптохвост) — как живой WB."""

    def __init__(self, value=None, ids=None):
        self.seen = []
        self.value = value if value is not None else KM2 + GS + "91EE16" + GS + "92dGVzdA=="
        self.ids = ids

    def orders_meta(self, order_ids):
        self.seen.append(list(order_ids))
        rows = [{"id": i, "metaDetails": [
            {"key": "sgtin", "value": self.value, "decision": "sgtinIntroduced"},
            {"key": "gtin", "value": GTIN, "decision": "filled"}]}
            for i in (self.ids or order_ids)]
        return {"orders": rows}


def _fake_wb(monkeypatch, fake):
    from marko.journal import identify
    monkeypatch.setattr(identify, "load_wb_token", lambda p: "t")
    monkeypatch.setattr(identify, "WBClient", lambda **kw: fake)


# --- classify: синтаксическая классификация ---

def test_classify_types():
    assert classify(RID) == ("order", WB_DOC)             # хвост .n.m срезан
    assert classify(WB_UUID) == ("order", WB_UUID)        # голое тело rid
    assert classify(f"{WB_DOC}.3.0") == ("order", WB_DOC)
    assert classify(KM2 + GS + "91EE16") == ("km", KM2)   # КМ с криптохвостом
    assert classify(GTIN) == ("gtin", GTIN)
    assert classify("4630520676025") == ("gtin", GTIN)    # EAN-13 → zfill14
    assert classify(str(ORDER_ID)) == ("num", str(ORDER_ID))
    assert classify(SUPPLY) == ("supply", SUPPLY)


def test_classify_rejects_garbage():
    for bad in ("", "  ", "ab", "abc", "НЕ-КОД", "eAc.", "eAc.abc", "01" + "2" * 20 + "X"):
        with pytest.raises(IdentifyError):
            classify(bad)


# --- GET /v1/identify: заказ (rid) — локальные срезы ---

def test_identify_order_local_slices(db, client):
    from marko.connector_wb.models import WbClientReturn
    from marko.platform.models import PlatformKV
    _sale(db, ev="s1", fiscal_dt="2026-09-01")
    _order_row(db)
    sup = {"id": SUPPLY, "name": "03.09 17:46", "createdAt": "2026-09-03T14:46:07Z",
           "closedAt": "2026-09-04T15:48:53Z", "scanDt": "2026-09-04T19:17:06Z",
           "rejectDt": None, "done": True}
    feed_row = {"srid": RID, "status": "created", "createdAt": "2026-09-11T15:34:06Z",
                "updatedAt": "2026-09-12T10:00:00Z", "warehouseName": "Коледино",
                "isMp": True, "destinationCity": "Казань", "sellerPrice": 3250,
                "isB2b": False, "nmId": 412477053}
    db.execute(pg_insert(PlatformKV).values(key="wb_supplies", value={"by_id": {SUPPLY: sup}}))
    db.execute(pg_insert(PlatformKV).values(
        key="trace_wb_feed",
        value={"fetched_at": time.time(), "by_doc": {WB_DOC: feed_row}}))
    db.add(WbClientReturn(srid=f"{WB_DOC}.0.0", sale_id="R1", rdate="2026-09-14",
                          warehouse="Склад продавца", order_doc=WB_DOC, km=None,
                          payload={"saleID": "R1"}))
    db.commit()
    r = client.get("/v1/identify", params={"key": RID, "live": 0}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "order" and body["key"] == WB_DOC
    assert body["order"]["status"] == "found"
    assert body["order"]["order"]["order_id"] == ORDER_ID
    assert body["order"]["items"][0]["sale_dt"] == "2026-09-01"    # enrich
    assert body["order_ids"] == [ORDER_ID]
    assert body["supplies"][0]["scanDt"].startswith("2026-09-04")
    assert body["wb_feed"]["orders"][0]["destinationCity"] == "Казань"
    assert body["client_returns"][0]["sale_id"] == "R1"
    # live выключен — блок честно поясняет
    assert body["live"]["state"] == "skipped"


def test_identify_order_live_sgtin_auto_and_audit(db, client, monkeypatch):
    from marko.platform.models import PlatformAudit
    _sale(db)
    _order_row(db)
    fake = FakeWb()
    _fake_wb(monkeypatch, fake)
    r = client.get("/v1/identify", params={"key": RID}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert fake.seen == [[ORDER_ID]]
    live = body["live"]
    assert live["state"] == "live" and live["fetched"] is True
    s = live["orders"][0]["sgtins"][0]
    assert s["km"] == KM2                              # полный КиЗ нормализован
    assert s["decision"] == "sgtinIntroduced"
    assert s["item"] is None                           # KM2 в журнале нет — не ошибка
    assert "identify.live_meta" in [a.action for a in db.query(PlatformAudit).all()]


def test_identify_order_live_cache_and_refresh(db, client, monkeypatch):
    _sale(db)
    _order_row(db)
    fake = FakeWb()
    _fake_wb(monkeypatch, fake)
    client.get("/v1/identify", params={"key": RID}, headers=AUTH)
    assert len(fake.seen) == 1
    body2 = client.get("/v1/identify", params={"key": RID}, headers=AUTH).json()
    assert len(fake.seen) == 1                          # кэш 10 мин — WB не дёргается
    assert body2["live"]["state"] == "cache" and body2["live"]["fetched"] is False
    assert body2["live"]["orders"][0]["sgtins"][0]["km"] == KM2
    body3 = client.get("/v1/identify", params={"key": RID, "refresh": 1},
                       headers=AUTH).json()
    assert len(fake.seen) == 2                          # refresh обходит кэш
    assert body3["live"]["state"] == "live"


def test_identify_order_live_sgtin_matches_journal(db, client, monkeypatch):
    """Живой sgtin = КМ журнала → сшивка item (главный сценарий юзера)."""
    _sale(db, km=KM2)
    _order_row(db)
    _fake_wb(monkeypatch, FakeWb(value=KM2 + GS + "91EE16" + GS + "92dGVzdA=="))
    body = client.get("/v1/identify", params={"key": RID}, headers=AUTH).json()
    s = body["live"]["orders"][0]["sgtins"][0]
    assert s["item"]["km"] == KM2 and s["item"]["state"] == "PENDING_WITHDRAW"


def test_identify_order_live_fail_soft(db, client, monkeypatch):
    from marko.connector_wb.client import WbHttpError

    class Boom:
        def orders_meta(self, order_ids):
            raise WbHttpError(500, "wb down")

    _sale(db)
    _order_row(db)
    _fake_wb(monkeypatch, Boom())
    r = client.get("/v1/identify", params={"key": RID}, headers=AUTH)
    assert r.status_code == 200                         # локальный срез жив
    body = r.json()
    assert body["live"]["state"] == "error"
    assert "wb down" in body["live"]["error"]
    assert body["order"]["status"] == "found"


def test_identify_order_no_numeric_id(db, client, monkeypatch):
    """order_id NULL и возвратных строк нет — live пропущен с причиной."""
    from marko.journal import identify
    _sale(db)
    _order_row(db, order_id=None)
    monkeypatch.setattr(identify, "WBClient", lambda **kw: pytest.fail("нет ID — WB не зовётся"))
    body = client.get("/v1/identify", params={"key": RID}, headers=AUTH).json()
    assert body["live"]["state"] == "skipped"
    assert "ID" in body["live"]["note"]


def test_identify_returns_id_when_registry_lost(db, client, monkeypatch):
    """Заказ ушёл из снапшота: числовой ID знает только возвратная строка."""
    from marko.connector_wb.models import WbReturn
    _sale(db)
    db.add(WbReturn(srid=f"{WB_DOC}.2.0", order_id=77001, status="Готов к выдаче",
                    expired_dt="2026-09-20T10:00:00",
                    payload={"reason": "Размер", "isStatusActive": True}))
    db.commit()
    fake = FakeWb()
    _fake_wb(monkeypatch, fake)
    body = client.get("/v1/identify", params={"key": RID}, headers=AUTH).json()
    assert body["order_ids"] == [77001]
    assert fake.seen == [[77001]]
    assert body["returns"][0]["reason"] == "Размер"


def test_identify_numeric_known_only_from_returns(db, client, monkeypatch):
    """Числовой вход, которого нет в реестре: статус '' (не словарь unknown —
    его «заказа нет вообще» противоречил бы возвратной строке рядом)."""
    from marko.connector_wb.models import WbReturn
    db.add(WbReturn(srid=f"{WB_DOC}.2.0", order_id=77001, status="Готов к выдаче",
                    expired_dt="", payload={"reason": "Размер"}))
    db.commit()
    fake = FakeWb()
    _fake_wb(monkeypatch, fake)
    body = client.get("/v1/identify", params={"key": "77001"}, headers=AUTH).json()
    assert body["type"] == "order" and body["order"]["status"] == ""
    assert body["order_ids"] == [77001] and fake.seen == [[77001]]
    assert "возвратной строки" in body["note"]
    assert body["returns"][0]["srid"] == f"{WB_DOC}.2.0"


def test_identify_bare_uuid_resolves_registry_doc(db, client):
    """Голое тело rid без префикса: WB-срезы ключуются по документу реестра,
    а не по введённому телу (форма ввода не должна менять результат)."""
    from marko.connector_wb.models import WbReturn
    _sale(db, srid=f"{WB_DOC}.0.0")
    _order_row(db)
    db.add(WbReturn(srid=f"{WB_DOC}.2.0", order_id=77001, status="Готов к выдаче",
                    expired_dt="", payload={"reason": "Размер"}))
    db.commit()
    body = client.get("/v1/identify", params={"key": WB_UUID, "live": 0},
                      headers=AUTH).json()
    assert body["key"] == WB_DOC                          # нормализован реестром
    assert body["order_ids"] == [77001, ORDER_ID]         # отсортированы числом
    assert body["returns"][0]["order_id"] == 77001
    assert body["client_returns"] == []                   # срезы по настоящему doc


def test_identify_live_negative_cache(db, client, monkeypatch):
    """Пустой ответ WB кэшируется негативно — «мёртвый» ID не дёргает сеть
    на каждом заходе, но честно показывает «кодов не закреплено»."""
    _sale(db)
    _order_row(db)

    class EmptyWb:
        def __init__(self): self.seen = []
        def orders_meta(self, order_ids):
            self.seen.append(list(order_ids))
            return {"orders": []}                         # WB не знает ID

    fake = EmptyWb()
    _fake_wb(monkeypatch, fake)
    body = client.get("/v1/identify", params={"key": RID}, headers=AUTH).json()
    assert body["live"]["state"] == "live"
    assert body["live"]["orders"][0]["sgtins"] == []      # негативный факт, не ошибка
    body2 = client.get("/v1/identify", params={"key": RID}, headers=AUTH).json()
    assert len(fake.seen) == 1 and body2["live"]["state"] == "cache"
    assert body2["live"]["orders"][0]["sgtins"] == []


# --- числовой ключ: ID сборочного задания / nmId ---

def test_identify_numeric_order_id_via_registry(db, client, monkeypatch):
    _sale(db)
    _order_row(db)
    fake = FakeWb()
    _fake_wb(monkeypatch, fake)
    body = client.get("/v1/identify", params={"key": str(ORDER_ID)}, headers=AUTH).json()
    assert body["type"] == "order" and body["key"] == WB_DOC
    assert body["order"]["status"] == "found"
    assert fake.seen == [[ORDER_ID]]


def test_identify_numeric_nm_fallback(db, client):
    _sale(db)
    _order_row(db, order_id=777, supply_id=None)
    db.commit()
    body = client.get("/v1/identify", params={"key": "412477053", "live": 0},
                      headers=AUTH).json()
    assert body["type"] == "nm"
    assert [o["order_doc"] for o in body["orders"]] == [WB_DOC]
    assert body["counts"]["orders"] == 1
    assert [i["km"] for i in body["items"]] == [KM]    # события с этим nm_id


def test_identify_numeric_unknown_422(db, client):
    r = client.get("/v1/identify", params={"key": "99999999999", "live": 0}, headers=AUTH)
    assert r.status_code == 422


# --- КМ → трассировка, GTIN, поставка ---

def test_identify_km_redirect_to_trace(db, client, monkeypatch):
    from marko.journal import identify
    _sale(db)
    monkeypatch.setattr(identify, "WBClient", lambda **kw: pytest.fail("КМ — без live-WB"))
    body = client.get("/v1/identify", params={"key": KM + "!91EE16"},
                      headers=AUTH).json()
    assert body["type"] == "km" and body["key"] == KM
    assert body["redirect"] == "trace"
    assert body["item"]["km"] == KM and body["item"]["state"] == "PENDING_WITHDRAW"


def test_identify_gtin(db, client):
    from marko.nkmt.models import Batch, Card
    _sale(db, km=KM)
    b = Batch(source_filename="x.xlsx"); db.add(b); db.flush()
    db.add(Card(batch_id=b.id, article="CAP-001", gtin=GTIN,
                tnved="6104620000", name="Брюки спортивные", status="published"))
    db.commit()
    body = client.get("/v1/identify", params={"key": GTIN, "live": 0}, headers=AUTH).json()
    assert body["type"] == "gtin"
    assert body["card"]["article"] == "CAP-001"
    assert [i["km"] for i in body["items"]] == [KM]
    # без карточки НК — items журнала всё равно есть (EAN-13 тоже находит)
    db.delete(db.query(Card).first()); db.commit()
    body2 = client.get("/v1/identify", params={"key": "4630520676025", "live": 0},
                       headers=AUTH).json()
    assert body2["card"] is None and body2["items"][0]["km"] == KM


def test_identify_supply(db, client):
    from marko.platform.models import PlatformKV
    _sale(db)
    _order_row(db)
    sup = {"id": SUPPLY, "name": "03.09 17:46", "createdAt": "2026-09-03T14:46:07Z",
           "closedAt": "2026-09-04T15:48:53Z", "scanDt": "2026-09-04T19:17:06Z",
           "rejectDt": None, "done": True}
    db.execute(pg_insert(PlatformKV).values(key="wb_supplies", value={"by_id": {SUPPLY: sup}}))
    db.commit()
    body = client.get("/v1/identify", params={"key": SUPPLY, "live": 0}, headers=AUTH).json()
    assert body["type"] == "supply" and body["supply"]["done"] is True
    assert [o["order_doc"] for o in body["orders"]] == [WB_DOC]
    # незнакомая поставка — не 422, а честное пустое состояние с причиной
    body2 = client.get("/v1/identify", params={"key": "WB-GI-000000001", "live": 0},
                       headers=AUTH).json()
    assert body2["supply"] is None and body2["orders"] == []
    assert body2.get("note")


# --- валидация, скоупы ---

def test_identify_validation_and_scopes(db, client, monkeypatch):
    from marko.journal import identify
    assert client.get("/v1/identify", params={"key": RID}).status_code == 401
    assert client.get("/v1/identify", params={"key": "abc!!"}, headers=AUTH).status_code == 422
    # read-only токен: локальные срезы есть, live-слой выключен
    _sale(db)
    _order_row(db)
    monkeypatch.setattr(identify, "WBClient", lambda **kw: pytest.fail("RO — без live-WB"))
    r = client.get("/v1/identify", params={"key": RID}, headers=AUTH_RO)
    assert r.status_code == 200 and r.json()["live"]["state"] == "skipped"


def test_parse_meta_shape():
    data = {"orders": [{"id": 5, "metaDetails": [
        {"key": "sgtin", "value": KM, "decision": "filled"},
        {"key": "gtin", "value": GTIN, "decision": "optional"}]}]}
    assert parse_meta(data) == [{"id": 5,
                                 "sgtins": [{"sgtin": KM, "decision": "filled"}]}]
