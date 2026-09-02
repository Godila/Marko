import httpx
import pytest

from mpmt.connector_wb.client import WBClient, WbHttpError, WbLimitError


def test_retry_on_429_then_ok():
    calls = {"n": 0}
    sleeps = []

    def handler(r):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})

    c = WBClient(token="t", transport=httpx.MockTransport(handler), sleeper=sleeps.append)
    r = c.request("GET", "/x")
    assert r.status_code == 200 and calls["n"] == 2
    assert sleeps == [0.0]  # Retry-After приоритетнее бэкоффа 1/4/16


def test_4xx_no_retry():
    def handler(r):
        return httpx.Response(400, json={"error": "bad"})

    c = WBClient(token="t", transport=httpx.MockTransport(handler), sleeper=lambda s: None)
    with pytest.raises(WbHttpError) as e:
        c.request("GET", "/x")
    assert e.value.status == 400


def test_excise_limit_gate(db, monkeypatch):
    import mpmt.connector_wb.client as wb_client

    monkeypatch.setattr(wb_client.time, "time", lambda: 1_800_000_000.0)

    def handler(r):
        return httpx.Response(200, json={"response": {"data": []}})

    c = WBClient(token="t", transport=httpx.MockTransport(handler), sleeper=lambda s: None, db=db)
    c.excise_report("2026-09-01", "2026-09-02")
    c.excise_report("2026-09-01", "2026-09-02")
    with pytest.raises(WbLimitError):
        c.excise_report("2026-09-01", "2026-09-02")


def test_orders_pagination():
    # реальные семантики WB v3: целочисленный курсор, next=0 = документированный конец
    calls = []

    def handler(r):
        calls.append(str(r.url.params["next"]))
        if r.url.params["next"] == "0":
            return httpx.Response(200, json={"next": 1001, "orders": [{"rid": "a"}]})
        return httpx.Response(200, json={"next": 0, "orders": [{"rid": "b"}]})

    c = WBClient(token="t", transport=httpx.MockTransport(handler), sleeper=lambda s: None)
    out = c.orders()
    assert [o["rid"] for o in out] == ["a", "b"]
    assert calls == ["0", "1001"]  # ровно 2 http-вызова; сам тест завершается — зацикливания нет
