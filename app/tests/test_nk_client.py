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


def test_feed_product_document_unwraps_list_result():
    """Дамп 85410-85414: result — array; пример 85586-85614 оборачивает
    {xmls, errors} в список — клиент разворачивает в первый элемент."""
    body = {"apiversion": 3, "result": [
        {"xmls": [{"goodId": 501, "xml": "<x/>", "gtin": 4630520699980}],
         "errors": [{"GTIN": "111", "message": "нет товара"}]}]}
    c = make_client(lambda r: httpx.Response(200, json=body))
    assert c.feed_product_document("T", ["4630520699980"]) == body["result"][0]
    c2 = make_client(lambda r: httpx.Response(200, json={"apiversion": 3, "result": []}))
    assert c2.feed_product_document("T", ["x"]) == {}  # пустой список → {}


def test_feed_product_sign_pkcs_unwraps_list_result():
    """Дамп типизирует result как number, но пример — {signed, errors};
    list-обёртка разворачивается как в feed-product-document."""
    c = make_client(lambda r: httpx.Response(200, json={
        "apiversion": 3, "result": [{"signed": [501], "errors": []}]}))
    assert c.feed_product_sign_pkcs("T", [{"goodId": 501}]) == {"signed": [501], "errors": []}
