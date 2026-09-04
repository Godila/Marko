"""Sign: подписание карточек через signer-шлюз (/nk/feed-product-sign-pkcs).

Чанк ≤10 карточек: /nk/feed-product-document отдаёт xmls [{goodId, gtin, xml}]
— карточки спариваются ПО GTIN (ответ может быть подмножеством и в любом
порядке, дамп 85502-85519); каждый xml подписывается doc_sign-задачей (CAdES
PKCS#7 detached, base64); items уходят одним запросом. Но и 200 несёт
errors[{goodId, message}] по отдельным товарам (дамп 85193-85203) —
отклонённый goodId → error_sign + message, прочие → published. Батч
published только когда нет notsigned/signing/error_sign (импортные
error/errors не мешают), иначе остаётся signing.
"""
import base64

import pytest

from mpmt.nkmt.client import NkHttpError
from mpmt.nkmt.models import Batch, Card
from mpmt.nkmt.service import sign_batch
from tests.test_api_nkmt_dicts import AUTH, client  # noqa: F401  (фикстура client)


class FakeNk:
    """Документ: xmls c gtin на обе карточки (+ опциональные errors); sign_pkcs —
    {"signed", "errors"} (пер-item отклонения), либо NkHttpError."""

    GTIN_A, GTIN_B = "4630520699980", "4630520699981"

    def __init__(self, sign_error=None, doc=None, sign_errors=None):
        self.sign_error = sign_error
        self.doc = doc  # подменный ответ feed_product_document (подмножество/порядок)
        self.sign_errors = sign_errors or []
        self.gtins = []
        self.items = []

    def feed_product_document(self, token, gtins):
        self.gtins.append(list(gtins))
        if self.doc is not None:
            return self.doc
        return {"xmls": [{"goodId": 501, "gtin": self.GTIN_A, "xml": "<x1/>"},
                         {"goodId": 502, "gtin": self.GTIN_B, "xml": "<x2/>"}]}

    def feed_product_sign_pkcs(self, token, items):
        if self.sign_error:
            raise self.sign_error
        self.items = items
        return {"signed": [i["goodId"] for i in items], "errors": self.sign_errors}


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


def test_sign_pkcs_item_errors_200(db, seeds, signer):
    """200 c errors по одному goodId (дамп 85193-85203): карта → error_sign
    с message, соседка published, батч не published."""
    fake = FakeNk(sign_errors=[{"goodId": 501, "message": "Товар не готов к подписанию"}])
    out = sign_batch(db, seeds.id, fake, "T")
    assert out == {"signed": 1, "failed": 1}
    cs = cards(db, seeds.id)
    assert cs["A"].status == "error_sign"
    assert "не готов к подписанию" in cs["A"].error_text
    assert (cs["B"].status, cs["B"].good_id) == ("published", "502")
    assert db.get(Batch, seeds.id).status == "signing"  # error_sign блокирует published


def test_sign_document_subset_reorder(db, seeds, signer):
    """xmls только по одному gtin (порядок произволен, есть чужой gtin) +
    errors[] документа по GTIN: спарилась своя карта со своим good_id,
    непарная — error_sign с message документа, батч не published."""
    doc = {"xmls": [{"goodId": 999, "gtin": "1111111111111", "xml": "<xf/>"},  # чужой
                    {"goodId": 502, "gtin": FakeNk.GTIN_B, "xml": "<x2/>"}],   # только B
           "errors": [{"GTIN": FakeNk.GTIN_A, "message": "Не удалось получить товар по GTIN"}]}
    fake = FakeNk(doc=doc)
    out = sign_batch(db, seeds.id, fake, "T")
    assert out == {"signed": 1, "failed": 1}
    cs = cards(db, seeds.id)
    assert (cs["B"].status, cs["B"].good_id) == ("published", "502")
    assert cs["A"].status == "error_sign"  # не published вслепую и не чужой good_id
    assert "Не удалось получить товар" in cs["A"].error_text
    assert db.get(Batch, seeds.id).status == "signing"
    # в подписание ушла только спаренная карточка
    assert [i["goodId"] for i in fake.items] == [502]
    assert [base64.b64decode(p["data_b64"]) for _, p in signer] == [b"<x2/>"]


def test_sign_batch_published_strict(db, seeds, signer):
    """Finding 3: батч → published только без error_sign; импортные
    error-карточки частичной выгрузки published не блокируют (как в refresh)."""
    db.add(Card(article="C", gtin="", batch_id=seeds.id, status="error",
                tnved="6109100000", name="Футболка C", error_text="битая строка"))
    db.commit()
    assert sign_batch(db, seeds.id, FakeNk(), "T") == {"signed": 2, "failed": 0}
    assert db.get(Batch, seeds.id).status == "published"  # error не мешает

    b2 = Batch(source_filename="mix.xlsx", status="signing")  # микс: half-fail
    db.add(b2); db.flush()
    for art, gtin in (("M1", FakeNk.GTIN_A), ("M2", FakeNk.GTIN_B)):
        db.add(Card(article=art, gtin=gtin, batch_id=b2.id, status="notsigned",
                    tnved="6109100000", name=f"Футболка {art}"))
    db.commit()
    sign_batch(db, b2.id, FakeNk(
        sign_errors=[{"goodId": 501, "message": "не готов"}]), "T")
    cs = cards(db, b2.id)
    assert (cs["M1"].status, cs["M2"].status) == ("error_sign", "published")
    assert db.get(Batch, b2.id).status == "signing"  # error_sign → батч не published


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
            return {"xmls": [{"goodId": 1000 + i, "gtin": g, "xml": f"<x{g}/>"}
                             for i, g in enumerate(gtins)]}

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
