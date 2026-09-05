"""Refresh: статус фида (/nk/feed-status) → статусы карточек и батча.

Кейсы статусов фида (живой дамп): Received/Processing — в полёте,
Moderated — готов к подписи, Signed — опубликовано, Rejected — ошибки.
"""
import pytest

from marko.nkmt.models import Batch, Card
from marko.nkmt.service import refresh_batch
from tests.test_api_nkmt_dicts import AUTH, client  # noqa: F401  (фикстура client)


class FakeNk:
    """feed_status отдаёт заданный статус (str) или по словарю feed_id→статус
    (мультичанковый батч); для Rejected — ещё и позиционную ошибку чанка."""

    def __init__(self, status):
        self.status = status

    def feed_status(self, token, feed_id):
        st = self.status[feed_id] if isinstance(self.status, dict) else self.status
        out = {"status": st}
        if st == "Rejected":
            out["errors"] = [{"row": 1, "error": f"плохой цвет {feed_id}"}]
        return out


@pytest.fixture
def seeds(db):
    b = Batch(source_filename="seed.xlsx", status="moderation", feed_id="42")
    db.add(b); db.flush()
    db.add(Card(article="FED", gtin="4630520699970", batch_id=b.id,
                tnved="6109100000", name="Футболка 1", status="fed"))
    db.add(Card(article="MOD", gtin="4630520699971", batch_id=b.id,
                tnved="6109100000", name="Футболка 2", status="moderation"))
    db.add(Card(article="ERR", gtin="", batch_id=b.id,
                tnved="6109100000", name="Футболка 3", status="error",
                error_text="нет gtin"))
    db.commit()
    return b


CARDS = {"FED", "MOD"}  # карточки в работе; ERR — ошибочная, не трогаем


def cards(db, batch_id):
    return {c.article: c for c in db.query(Card).filter(Card.batch_id == batch_id).all()}


@pytest.mark.parametrize("st,card_st,batch_st,error_text", [
    ("Received", "fed", "moderation", None),      # в полёте — ничего не меняется
    ("Processing", "fed", "moderation", None),
    ("Moderated", "notsigned", "signing", None),  # готов к подписи
    ("Signed", "published", "published", None),   # опубликовано
    ("Rejected", "errors", "error", "плохой цвет"),  # фид отклонён
])
def test_refresh_transitions(db, seeds, st, card_st, batch_st, error_text):
    out = refresh_batch(db, seeds.id, FakeNk(st), "T")
    assert out == {"feed_status": st, "batch_status": batch_st}
    b = db.get(Batch, seeds.id)
    assert b.status == batch_st
    cs = cards(db, seeds.id)
    if st in ("Received", "Processing"):
        assert cs["FED"].status == "fed" and cs["MOD"].status == "moderation"
    else:
        assert all(cs[a].status == card_st for a in CARDS)
    assert cs["ERR"].status == "error" and cs["ERR"].error_text == "нет gtin"
    if error_text is None:
        assert all(not cs[a].error_text for a in CARDS)
    else:
        assert all(error_text in cs[a].error_text for a in CARDS)


def test_refresh_rejected_without_payload(db, seeds):
    class Bare(FakeNk):
        def feed_status(self, token, feed_id):
            return {"status": "Rejected"}  # ошибок НК не прислал

    refresh_batch(db, seeds.id, Bare("Rejected"), "T")
    cs = cards(db, seeds.id)
    assert all(cs[a].status == "errors" and cs[a].error_text for a in CARDS)


@pytest.fixture
def chunked(db):
    """Мультичанковый батч: stats.feed_ids хранит все чанки, feed_id — последний."""
    b = Batch(source_filename="big.xlsx", status="moderation", feed_id="42",
              stats={"feed_ids": ["41", "42"]})
    db.add(b); db.flush()
    for art, gtin in (("A", "4630520699980"), ("B", "4630520699981")):
        db.add(Card(article=art, gtin=gtin, batch_id=b.id,
                    tnved="6109100000", name=f"Футболка {art}", status="fed"))
    db.commit()
    return b


def test_refresh_multichunk_rejected_chunk(db, chunked):
    out = refresh_batch(db, chunked.id, FakeNk({"41": "Rejected", "42": "Signed"}), "T")
    assert out == {"feed_status": "Rejected", "batch_status": "error"}
    assert db.get(Batch, chunked.id).status == "error"
    cs = cards(db, chunked.id)
    assert all(c.status == "errors" and "плохой цвет 41" in c.error_text
               for c in cs.values())  # ошибка первого отклонённого чанка


def test_refresh_multichunk_all_signed(db, chunked):
    out = refresh_batch(db, chunked.id, FakeNk({"41": "Signed", "42": "Signed"}), "T")
    assert out == {"feed_status": "Signed", "batch_status": "published"}
    assert db.get(Batch, chunked.id).status == "published"
    assert all(c.status == "published" for c in cards(db, chunked.id).values())


def test_refresh_multichunk_mixed_inflight(db, chunked):
    out = refresh_batch(db, chunked.id, FakeNk({"41": "Signed", "42": "Processing"}), "T")
    assert out == {"feed_status": "Processing", "batch_status": "moderation"}
    assert all(c.status == "fed" for c in cards(db, chunked.id).values())  # ничего не меняем


def test_refresh_keeps_published_cards(db, seeds):
    """published-карточка пережила все переходы refresh'а: Moderated не
    откатывает её в notsigned (докручиваются только неопубликованные)."""
    cards(db, seeds.id)["FED"].status = "published"
    db.commit()
    out = refresh_batch(db, seeds.id, FakeNk("Moderated"), "T")
    assert out == {"feed_status": "Moderated", "batch_status": "signing"}
    cs = cards(db, seeds.id)
    assert cs["FED"].status == "published"  # не тронута
    assert cs["MOD"].status == "notsigned"


def test_refresh_guards(db, seeds):
    db.get(Batch, seeds.id).status = "new"; db.commit()
    with pytest.raises(ValueError):  # статус не кормимый refresh'ем
        refresh_batch(db, seeds.id, FakeNk("Moderated"), "T")
    db.get(Batch, seeds.id).status, db.get(Batch, seeds.id).feed_id = "signing", ""
    db.commit()
    with pytest.raises(ValueError):  # нет feed_id
        refresh_batch(db, seeds.id, FakeNk("Moderated"), "T")
    with pytest.raises(ValueError):  # батча нет
        refresh_batch(db, 99999, FakeNk("Moderated"), "T")


def test_refresh_endpoint(db, client, monkeypatch, seeds):
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    monkeypatch.setattr("marko.nkmt.client.NkClient", lambda base: FakeNk("Moderated"))
    r = client.post(f"/v1/nkmt/batches/{seeds.id}/refresh", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"feed_status": "Moderated",
                                                "batch_status": "signing"}
    db.get(Batch, seeds.id).status = "published"; db.commit()
    assert client.post(f"/v1/nkmt/batches/{seeds.id}/refresh",
                       headers=AUTH).status_code == 409  # published не обновляется
    assert client.post("/v1/nkmt/batches/99999/refresh", headers=AUTH).status_code == 404
