"""Sign: подписание карточек через signer-шлюз (/nk/feed-product-sign-pkcs).

Чанк ≤10 карточек: /nk/feed-product-document отдаёт xmls [{goodId, xml}],
каждый xml подписывается doc_sign-задачей (CAdES PKCS#7 detached, base64);
items уходят одним запросом. Успех чанка → published, NkHttpError →
error_sign + error_text, батч published только когда подписаны все.
"""
import base64

import pytest

from mpmt.nkmt.client import NkHttpError
from mpmt.nkmt.models import Batch, Card
from mpmt.nkmt.service import sign_batch
from tests.test_api_nkmt_dicts import AUTH, client  # noqa: F401  (фикстура client)


class FakeNk:
    """feed_product_document → xmls на 2 gtin; sign_pkcs — ok или NkHttpError."""

    def __init__(self, sign_error=None):
        self.sign_error = sign_error
        self.gtins = []
        self.items = []

    def feed_product_document(self, token, gtins):
        self.gtins.append(list(gtins))
        return {"xmls": [{"goodId": 501, "xml": "<x1/>"},
                         {"goodId": 502, "xml": "<x2/>"}]}

    def feed_product_sign_pkcs(self, token, items):
        if self.sign_error:
            raise self.sign_error
        self.items = items
        return {}


@pytest.fixture
def seeds(db):
    """Батч signing с двумя notsigned-карточками (прошли модерацию фида)."""
    b = Batch(source_filename="sign.xlsx", status="signing")
    db.add(b); db.flush()
    for art, gtin in (("A", "4630520699980"), ("B", "4630520699981")):
        db.add(Card(article=art, gtin=gtin, batch_id=b.id, status="notsigned",
                    tnved="6109100000", name=f"Футболка {art}"))
    db.commit()
    return b


def cards(db, batch_id):
    return {c.article: c for c in db.query(Card).filter(Card.batch_id == batch_id).all()}


@pytest.fixture
def signer(monkeypatch):
    """Боевой signer-шлюз заменён заглушкой; фиксирует вызовы doc_sign."""
    calls = []
    monkeypatch.setattr("mpmt.nkmt.service._sign_via_gateway",
                        lambda db, type_, payload: (calls.append((type_, payload)), "SIG")[1])
    return calls


def test_sign_happy_path(db, seeds, signer):
    fake = FakeNk()
    out = sign_batch(db, seeds.id, fake, "T")
    assert out == {"signed": 2, "failed": 0}
    assert db.get(Batch, seeds.id).status == "published"
    cs = cards(db, seeds.id)
    assert (cs["A"].status, cs["A"].good_id) == ("published", "501")
    assert (cs["B"].status, cs["B"].good_id) == ("published", "502")
    # фид документов запрошен по gtin обеих карточек
    assert fake.gtins == [["4630520699980", "4630520699981"]]
    # контракт /nk/feed-product-sign-pkcs: goodId / base64Xml / signature
    assert [set(i) for i in fake.items] == [{"goodId", "base64Xml", "signature"}] * 2
    assert [i["goodId"] for i in fake.items] == [501, 502]
    assert all(i["signature"] == "SIG" for i in fake.items)
    # doc_sign-задачи над base64(xml); base64Xml — тот же data_b64
    assert [(t, base64.b64decode(p["data_b64"])) for t, p in signer] == [
        ("doc_sign", b"<x1/>"), ("doc_sign", b"<x2/>")]
    assert [i["base64Xml"] for i in fake.items] == [p["data_b64"] for _, p in signer]


def test_sign_failure_marks_error_sign(db, seeds, signer):
    fake = FakeNk(sign_error=NkHttpError(502, "boom"))
    out = sign_batch(db, seeds.id, fake, "T")
    assert out == {"signed": 0, "failed": 2}
    assert db.get(Batch, seeds.id).status == "signing"  # не published
    cs = cards(db, seeds.id)
    assert all(c.status == "error_sign" and "boom" in c.error_text
               for c in cs.values())


def test_sign_guards(db, seeds):
    for c in db.query(Card).filter(Card.batch_id == seeds.id).all():
        c.status = "published"
    db.commit()
    with pytest.raises(ValueError):  # нет notsigned-карточек
        sign_batch(db, seeds.id, FakeNk(), "T")
    with pytest.raises(ValueError):  # батча нет
        sign_batch(db, 99999, FakeNk(), "T")


def test_sign_chunks_by_ten(db, monkeypatch):
    """11 карточек → два документа: 10 + 1 (лимит /nk/feed-product-*)."""
    monkeypatch.setattr("mpmt.nkmt.service._sign_via_gateway",
                        lambda db, type_, payload: "SIG")
    b = Batch(source_filename="big.xlsx", status="signing")
    db.add(b); db.flush()
    for i in range(11):
        db.add(Card(article=f"A{i}", gtin=f"46305206998{i:02d}", batch_id=b.id,
                    status="notsigned", tnved="6109100000", name=f"Футболка {i}"))
    db.commit()
    seen = []

    class ChunkNk:
        def feed_product_document(self, token, gtins):
            seen.append(list(gtins))
            return {"xmls": [{"goodId": 1000 + g, "xml": f"<x{g}/>"}
                             for g in range(len(gtins))]}

        def feed_product_sign_pkcs(self, token, items):
            return {}

    out = sign_batch(db, b.id, ChunkNk(), "T")
    assert out == {"signed": 11, "failed": 0}
    assert [len(g) for g in seen] == [10, 1]
    assert db.get(Batch, b.id).status == "published"
    assert all(c.status == "published" for c in cards(db, b.id).values())


def test_sign_endpoint(db, client, monkeypatch, seeds, signer):
    monkeypatch.setattr("mpmt.connector_mt.manager.get_token", lambda _db: "T")
    fake = FakeNk()
    monkeypatch.setattr("mpmt.nkmt.client.NkClient", lambda base: fake)
    r = client.post(f"/v1/nkmt/batches/{seeds.id}/sign", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"signed": 2, "failed": 0}
    db.expire_all()  # app-сессия закоммитила — сбрасываем кэш тестовой сессии
    assert db.get(Batch, seeds.id).status == "published"
    assert [i["goodId"] for i in fake.items] == [501, 502]
    for c in db.query(Card).filter(Card.batch_id == seeds.id).all():
        c.status = "published"
    db.commit()
    assert client.post(f"/v1/nkmt/batches/{seeds.id}/sign",
                       headers=AUTH).status_code == 409  # нечего подписывать
    assert client.post("/v1/nkmt/batches/99999/sign", headers=AUTH).status_code == 404
