"""Трассировка КМ: жизненный цикл одного кода по локальным данным (read-only)."""
import pytest
from fastapi.testclient import TestClient
from marko.api.app import create_app
from marko.platform.models import PlatformPrincipal, PlatformToken, hash_token
from marko.journal import apply_event, log_action
from marko.journal.trace import GS, TraceError, normalize_km

KM = "0104630520676025215TRACE001"          # как журнальный (короткая тестовая форма)
KM2 = "0104630520676025215UKsE;hVmAtad"      # прод-формат, 31 символ
INN = "090201471350"
AUTH = {"Authorization": "Bearer t1"}
AUTH_RO = {"Authorization": "Bearer t-ro"}
WB_UUID = "i9ba767bf5642be309fb036281a0ecda7"
WB_DOC = f"eBQ.{WB_UUID}"


@pytest.fixture
def client(db):
    p = PlatformPrincipal(kind="user", name="owner"); db.add(p); db.flush()
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t1"), scopes="read,docs:submit"))
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t-ro"), scopes="read"))
    db.commit()
    return TestClient(create_app())


# --- нормализация КИЗ → КМ ---

def test_normalize_km_variants():
    full = KM2 + GS + "91EE16" + GS + "92dGVzdA=="
    assert normalize_km(full) == KM2                       # полный КИЗ с GS
    assert normalize_km(full.replace(GS, "!")) == KM2      # то же с '!'
    assert normalize_km(KM2 + "91EE16") == KM2             # слитный криптохвост
    assert normalize_km(f"  {KM} \n") == KM                 # пробелы по краям
    assert normalize_km(KM) == KM                          # короткий как есть
    # регистр серийника значим (ЧЗ base-подобные серийники) — не портим
    assert normalize_km("0104630520676025215UKse;hvmATAD") == "0104630520676025215UKse;hvmATAD"


def test_normalize_km_rejects_garbage():
    for bad in ("", "   ", "abc", "2104630520676021215XXXX", "01Abcdef01234567215XXX",
                "0104630520676025", "010463052067602121"):
        with pytest.raises(TraceError):
            normalize_km(bad)


def test_normalize_km_bang_inside_serial():
    """'!' допустим ВНУТРИ серийника (расширенный алфавит ЧЗ): '!'-разрез
    валиден только перед крипто-AI 91/92, иначе код проходит целиком."""
    with_bang = "0104630520676025215UK!sE;hVmAt"          # 31, '!' в серийнике
    assert normalize_km(with_bang) == with_bang           # не разрезан
    assert normalize_km(with_bang + "!91EE16") == with_bang   # криптохвост отрезан
    assert normalize_km(with_bang + GS + "92dGVzdA==") == with_bang


# --- GET /v1/trace: полный жизненный цикл ---

def _sale(db, km=KM, ev="e1", srid=f"{WB_DOC}.0.0", **payload):
    apply_event(db, source="wb_excise", source_event_id=ev, kind="sale",
                km=km, srid=srid, payload={"price": 1793, "nm_id": 412478853, **payload})


def test_trace_full_lifecycle(db, client):
    _sale(db, ev="s1", fiscal_dt="2026-09-01", fiscal_doc_number=77)
    client.post("/v1/batches/withdraw", headers=AUTH, json={"inn": INN})
    apply_event(db, source="wb_excise", source_event_id="r1", kind="return",
                km=KM, srid=f"{WB_DOC}.0.0",
                payload={"price": 1793, "fiscal_dt": "2026-09-05", "fiscal_doc_number": 88})
    client.post("/v1/batches/return", headers=AUTH, json={"inn": INN})
    r = client.get(f"/v1/trace?km={KM}", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["km"] == KM and body["found"] is True
    assert body["item"]["state"] == "RETURNED"
    assert body["gtin"] == "04630520676025"
    kinds = [e["kind"] for e in body["timeline"]]
    # ts честно смешивает время чека (fiscal_dt) и время наших документов
    # (created_at): проверяем монотонность и полный состав, а не жёсткий порядок
    assert kinds[0] == "sale"                              # путь начинается продажей
    assert set(kinds) == {"sale", "withdraw", "return", "return_apply"}
    tss = [e["ts"] for e in body["timeline"]]
    assert tss == sorted(tss)                              # хронология восходящая
    systems = {e["kind"]: e["system"] for e in body["timeline"]}
    assert systems["sale"] == "wb" and systems["withdraw"] == "marko"
    assert systems["return"] == "wb" and systems["return_apply"] == "marko"
    sale = body["timeline"][0]
    assert sale["ts"] == "2026-09-01"                    # fiscal_dt вместо created_at
    assert "77" in sale["detail"] and "1 793" in sale["detail"]
    assert body["docs"][0]["type"] == "LK_RECEIPT"
    assert [d["type"] for d in body["docs"]] == ["LK_RECEIPT", "LP_RETURN"]
    assert body["counts"] == {"events": 4}


def test_trace_input_variants_same_km(db, client):
    _sale(db)
    for raw in (KM, f"  {KM} ", KM + "!91EE16", KM + GS + "91EE16"):
        r = client.get("/v1/trace", params={"km": raw}, headers=AUTH)
        assert r.status_code == 200 and r.json()["found"] is True, raw
        assert r.json()["km"] == KM


def test_trace_orders_returns_card(db, client):
    from marko.connector_wb.models import WbOrder, WbReturn
    from marko.nkmt.models import Batch, Card
    _sale(db, srid=f"{WB_DOC}.3.0")
    db.add(WbOrder(order_doc=WB_DOC, order_id=5632423, delivery_type="fbs",
                   nm_id=412477053, order_created_at="2026-09-11T15:34:06Z"))
    db.add(WbReturn(srid=f"{WB_DOC}.0.0", order_id=5632423, status="Готов к выдаче",
                    expired_dt="2026-09-20T10:00:00",
                    payload={"reason": "Размер", "isStatusActive": True}))
    b = Batch(source_filename="x.xlsx"); db.add(b); db.flush()
    db.add(Card(batch_id=b.id, article="CAP-001", gtin="04630520676025",
                tnved="6505003000", name="Шапка спортивная", status="published"))
    db.commit()
    r = client.get(f"/v1/trace?km={KM}", headers=AUTH).json()
    assert [o["order_doc"] for o in r["orders"]] == [WB_DOC]   # хвосты расходятся — матчинг по документу
    assert r["orders"][0]["order_id"] == 5632423
    ret = r["returns"][0]
    assert ret["srid"] == f"{WB_DOC}.0.0" and ret["reason"] == "Размер"
    assert r["card"]["name"] == "Шапка спортивная" and r["card"]["article"] == "CAP-001"


def test_trace_unknown_km(db, client):
    r = client.get(f"/v1/trace?km={KM2}", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["found"] is False and body["item"] is None
    assert body["timeline"] == [] and body["docs"] == []
    assert body["orders"] == [] and body["returns"] == []
    assert body["counts"] == {"events": 0}


def test_trace_skip_fbw_no_item_but_events(db, client):
    log_action(db, source="wb_excise", source_event_id="fbw1", kind="skip_fbw",
               km=KM2, srid=f"{WB_DOC}.0.0", payload={"nm_id": 5})
    body = client.get(f"/v1/trace?km={KM2}", headers=AUTH).json()
    assert body["found"] is False                     # позиции нет — вне FBS-контура
    assert [e["kind"] for e in body["timeline"]] == ["skip_fbw"]
    assert body["counts"]["events"] == 1


def test_trace_validation_and_scopes(db, client):
    assert client.get("/v1/trace?km=0104630520676025215TRACE001").status_code == 401
    assert client.get("/v1/trace", headers=AUTH).status_code == 422
    for bad in ("  ", "abc", "x" * 40):
        r = client.get("/v1/trace", params={"km": bad}, headers=AUTH)
        assert r.status_code == 422, bad
    assert client.get("/v1/trace", params={"km": KM + "!91EE16"}, headers=AUTH_RO).status_code == 200


# --- POST /v1/trace/wb-meta: телеметрия закрепления sgtin на WB ---

def test_trace_wb_meta_route(db, client, monkeypatch):
    from marko.api import routes_journal
    from marko.connector_wb.models import WbOrder
    from marko.platform.models import PlatformAudit

    class FakeWb:
        def __init__(self):
            self.seen = []

        def orders_meta(self, order_ids):
            self.seen.append(list(order_ids))
            return {"orders": [{"id": 5632423, "metaDetails": [
                {"key": "sgtin", "value": KM, "decision": "sgtinIntroduced"},
                {"key": "gtin", "value": "04630520676025", "decision": "filled"}]}]}

    fake = FakeWb()
    monkeypatch.setattr(routes_journal, "load_wb_token", lambda p: "t")
    monkeypatch.setattr(routes_journal, "WBClient", lambda **kw: fake)
    _sale(db, srid=f"{WB_DOC}.3.0")
    db.add(WbOrder(order_doc=WB_DOC, order_id=5632423, delivery_type="fbs",
                   nm_id=1, order_created_at=""))
    db.commit()
    r = client.post("/v1/trace/wb-meta", headers=AUTH, json={"km": KM})
    assert r.status_code == 200
    body = r.json()
    assert fake.seen == [[5632423]]                        # числовые ID сборочных заданий
    assert body["orders"][0]["id"] == 5632423
    assert body["orders"][0]["sgtins"] == [{"sgtin": KM, "decision": "sgtinIntroduced"}]
    assert "trace.wb_meta" in [a.action for a in db.query(PlatformAudit).all()]


def test_trace_wb_meta_no_known_orders(db, client, monkeypatch):
    from marko.api import routes_journal

    monkeypatch.setattr(routes_journal, "load_wb_token", lambda p: "t")
    monkeypatch.setattr(routes_journal, "WBClient",
                        lambda **kw: pytest.fail("WB не должен зваться без заказов"))
    _sale(db, srid=f"{WB_DOC}.3.0")                        # реестра с order_id нет
    r = client.post("/v1/trace/wb-meta", headers=AUTH, json={"km": KM})
    assert r.status_code == 200 and r.json()["orders"] == []
    assert "note" in r.json()


def test_km_wb_order_ids_from_returns(db, client):
    """Возвратная строка несёт числовой ID сборочного задания даже без
    прогретого реестра wb.orders (выкупленный заказ уходит из снапшота)."""
    from marko.connector_wb.models import WbReturn
    from marko.journal.trace import km_wb_order_ids
    apply_event(db, source="wb_excise", source_event_id="ret1", kind="return",
                km=KM, srid=f"{WB_DOC}.0.0", payload={"price": 1})
    db.add(WbReturn(srid=f"{WB_DOC}.2.0", order_id=77001, status="Готов к выдаче",
                    expired_dt="", payload={}))
    db.commit()
    assert km_wb_order_ids(db, KM) == [77001]


def test_trace_wb_meta_errors_and_scopes(db, client, monkeypatch):
    from marko.api import routes_journal
    from marko.connector_wb.client import WbHttpError
    from marko.connector_wb.models import WbOrder

    class Boom:
        def orders_meta(self, order_ids):
            raise WbHttpError(500, "wb down")

    monkeypatch.setattr(routes_journal, "load_wb_token", lambda p: "t")
    monkeypatch.setattr(routes_journal, "WBClient", lambda **kw: Boom())
    _sale(db, srid=f"{WB_DOC}.3.0")
    db.add(WbOrder(order_doc=WB_DOC, order_id=7, delivery_type="fbs", nm_id=1))
    db.commit()
    assert client.post("/v1/trace/wb-meta", headers=AUTH, json={"km": KM}).status_code == 502
    assert client.post("/v1/trace/wb-meta", headers=AUTH, json={"km": "abc"}).status_code == 422
    assert client.post("/v1/trace/wb-meta", headers=AUTH_RO,
                       json={"km": KM}).status_code == 403


# --- v2: живой слой ЧЗ (полный cisInfo) и телеметрия заказа WB (order-feed) ---

CZ_FULL = {"cisInfo": {
    "cis": KM, "status": "INTRODUCED", "productName": "Костюм_8800_меланж_52",
    "emissionDate": "2025-12-13T10:02:55.102Z", "applicationDate": "2025-12-13T10:10:02.122Z",
    "introducedDate": "2025-12-13T10:34:02.465Z", "producedDate": "2025-12-01T00:00:00.000Z",
    "emissionType": "LOCAL", "packageType": "BUNDLE", "brand": "YCPB",
    "producerName": "ИП БАЙКУЛОВ ДИНИСЛАМ АХМАТОВИЧ", "producerInn": "090201471350",
    "gtin": "04630520676025", "tnVedEaes": "6104192000", "markWithdraw": False,
    "certDoc": [{"number": "ЕАЭС N RU Д-RU.РА09.В.28397/25",
                 "type": "CONFORMITY_DECLARATION", "date": "2025-10-15"}],
}}


class _CzFull:
    def __init__(self, answers):
        self.answers = answers

    def cises_info(self, token, cises):
        return [self.answers[km] for km in cises]


def _mt_full(monkeypatch, cz):
    from marko.connector_mt import manager
    monkeypatch.setattr(manager, "get_token", lambda d, c=None: "T")
    monkeypatch.setattr(manager, "default_client", lambda: cz)


def test_trace_live_cz_full_snapshot(db, client, monkeypatch):
    _sale(db)
    _mt_full(monkeypatch, _CzFull({KM: CZ_FULL}))
    r = client.get("/v1/trace", params={"km": KM, "live": 1}, headers=AUTH)
    body = r.json()
    assert body["cz"]["emissionDate"].startswith("2025-12-13")
    assert body["cz"]["introducedDate"].startswith("2025-12-13")
    assert body["cz"]["producerName"] == "ИП БАЙКУЛОВ ДИНИСЛАМ АХМАТОВИЧ"
    assert body["cz"]["certDoc"][0]["number"].startswith("ЕАЭС N RU Д-RU.ПА09") \
        or body["cz"]["certDoc"][0]["number"].startswith("ЕАЭС")
    # live-проверка заодно обновляет штатные колонки позиции
    assert body["item"]["cis_status"] == "introduced"
    assert body["item"]["cis_product_name"] == "Костюм_8800_меланж_52"


def test_trace_live_unknown_km_and_no_live(db, client, monkeypatch):
    _mt_full(monkeypatch, _CzFull({KM2: {"cisInfo": {"cis": KM2, "status": "RETIRED",
                                                     "productName": "Чужой код"}}}))
    # код вне журнала: cz приходит из infos-среза, позиция не создаётся
    body = client.get("/v1/trace", params={"km": KM2, "live": 1}, headers=AUTH).json()
    assert body["found"] is False
    assert body["cz"]["status"] == "retired" and body["cz"]["productName"] == "Чужой код"
    from marko.journal.models import Item
    assert db.get(Item, KM2) is None
    # live=0 — сети нет: ключа cz нет (только локальные данные)
    from marko.connector_mt import manager
    monkeypatch.setattr(manager, "default_client",
                        lambda: pytest.fail("live=0 не должен звать ЧЗ"))
    body2 = client.get("/v1/trace", params={"km": KM, "live": 0}, headers=AUTH).json()
    assert "cz" not in body2
    # read-only токен: живой слой выключен принудительно
    body3 = client.get("/v1/trace", params={"km": KM, "live": 1}, headers=AUTH_RO).json()
    assert "cz" not in body3


def test_trace_live_cz_fail_soft(db, client, monkeypatch):
    _sale(db)

    class Broken:
        def cises_info(self, token, cises):
            raise RuntimeError("cz down")
    from marko.connector_mt import manager
    monkeypatch.setattr(manager, "get_token", lambda d, c=None: "T")
    monkeypatch.setattr(manager, "default_client", Broken())
    r = client.get("/v1/trace", params={"km": KM, "live": 1}, headers=AUTH)
    assert r.status_code == 200                        # локальные данные живы
    assert "error" in r.json()["cz"]


FEED_ORDER = {"srid": f"{WB_DOC}.0.0", "status": "buyout", "createdAt": "2026-09-16T10:00:00Z",
              "updatedAt": "2026-09-18T18:30:00Z", "warehouseName": "Коледино",
              "isMp": True, "destinationCity": "Казань", "destinationDistrict": "Приволжский",
              "sellerPrice": 3250, "isB2b": False, "nmId": 412478853}


def test_trace_wb_feed_route_and_cache(db, client, monkeypatch):
    from marko.api import routes_journal
    from marko.platform.models import PlatformKV

    seen = []

    class FakeWb:
        def order_feed(self, date_from, date_to, nm_ids=None, limit=10000):
            seen.append({"date_from": date_from, "nm": nm_ids})
            return [FEED_ORDER]

    monkeypatch.setattr(routes_journal, "load_wb_token", lambda p: "t")
    monkeypatch.setattr(routes_journal, "WBClient", lambda **kw: FakeWb())
    _sale(db, srid=f"{WB_DOC}.3.0", nm_id=412478853)
    r = client.post("/v1/trace/wb-feed", headers=AUTH, json={"km": KM})
    assert r.status_code == 200
    body = r.json()
    assert body["orders"][0]["status"] == "buyout"           # хвосты расходятся — матч по документу
    assert body["orders"][0]["destinationCity"] == "Казань"
    assert seen and seen[0]["nm"] is None               # кэш общий: без nmIds-фильтра
    assert body["fetched_at"]
    # повтор в окне троттла — из кэша kv, WB не дёргается
    monkeypatch.setattr(routes_journal, "WBClient",
                        lambda **kw: pytest.fail("кэш 3ч — WB не должен зваться"))
    r2 = client.post("/v1/trace/wb-feed", headers=AUTH, json={"km": KM})
    assert r2.json()["orders"][0]["destinationCity"] == "Казань"
    # протухший кэш → новая выгрузка (фейк WB возвращаем на место)
    monkeypatch.setattr(routes_journal, "WBClient", lambda **kw: FakeWb())
    from marko.db import SessionLocal
    s = SessionLocal()
    row = s.get(PlatformKV, "trace_wb_feed")
    row.value = {"fetched_at": 0, "by_doc": {}}
    s.commit(); s.close()
    r3 = client.post("/v1/trace/wb-feed", headers=AUTH, json={"km": KM})
    assert r3.json()["orders"][0]["status"] == "buyout"


def test_trace_wb_feed_errors_and_scopes(db, client, monkeypatch):
    from marko.api import routes_journal
    from marko.connector_wb.client import WbHttpError

    class Boom:
        def order_feed(self, *a, **kw):
            raise WbHttpError(500, "wb down")

    monkeypatch.setattr(routes_journal, "load_wb_token", lambda p: "t")
    monkeypatch.setattr(routes_journal, "WBClient", lambda **kw: Boom())
    _sale(db, srid=f"{WB_DOC}.0.0")
    assert client.post("/v1/trace/wb-feed", headers=AUTH, json={"km": KM}).status_code == 502
    assert client.post("/v1/trace/wb-feed", headers=AUTH, json={"km": "abc"}).status_code == 422
    assert client.post("/v1/trace/wb-feed", headers=AUTH_RO, json={"km": KM}).status_code == 403
    # событий нет → note без сети
    monkeypatch.setattr(routes_journal, "WBClient",
                        lambda **kw: pytest.fail("без событий WB не зывается"))
    r = client.post("/v1/trace/wb-feed", headers=AUTH,
                    json={"km": "0104630520676025215NOFEED1"})
    assert r.status_code == 200 and r.json()["orders"] == [] and "note" in r.json()
