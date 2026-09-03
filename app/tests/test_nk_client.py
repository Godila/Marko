import httpx
import pytest
from mpmt.nkmt.client import NkClient, NkHttpError


def make_client(handler):
    return NkClient("https://nk.example", transport=httpx.MockTransport(handler))


def test_attributes_unwraps_result():
    c = make_client(lambda r: httpx.Response(200, json={
        "apiversion": 3, "result": [{"attr_id": 12, "attr_name": "Вид товара"}]}))
    assert c.attributes("T", "6109100000", "m")[0]["attr_id"] == 12


def test_feed_posts_entries_and_reads_feed_id():
    seen = {}
    def h(r):
        seen["body"] = r.read().decode(); seen["path"] = r.url.path
        return httpx.Response(200, json={"apiversion": 3, "result": {"feed_id": 7126}})
    c = make_client(h)
    assert c.feed("T", [{"gtin": "1", "good_name": "x"}]) == {"feed_id": 7126}
    assert seen["path"] == "/nk/feed" and '"good_name"' in seen["body"]


def test_4xx_no_retry():
    calls = {"n": 0}
    def h(r):
        calls["n"] += 1
        return httpx.Response(404, json={"error_message": "Данные не найдены"})
    c = make_client(h)
    with pytest.raises(NkHttpError):
        c.brands("T", "zzz")
    assert calls["n"] == 1


def test_5xx_retries_then_ok():
    calls = {"n": 0}
    def h(r):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"apiversion": 3, "result": []})
    c = make_client(h)
    assert c.categories("T", "6109100000") == [] and calls["n"] == 3
