"""Feed батча: generate-gtins для карточек без gtin → entries → /nk/feed (чанки ≤500).

moderation — ПОЛЕ ENTRY (дамп trueapi, /nk/feed, «Параметры тела запроса»):
ставим {"moderation": 1} в каждую entry (НК по умолчанию черновик).
"""
import pytest

from marko.nkmt.models import Batch, Card
from marko.nkmt.service import feed_batch
from tests.test_api_nkmt_dicts import AUTH, client  # noqa: F401  (фикстура client)

# формат attributes из Task 6: ключи — str(attr_id)
ATTRS = {
    "2478": "Футболка тест", "12": "футболка", "36": "БЕЛЫЙ",
    "2483": "100% хлопок", "14013": "M", "2504": "YCPB",
    "35": {"type": "пол", "value": "жен"}, "13914": {"type": "цв", "value": "белый"},
    "13836": ["хб", "шерсть"], "2630": "RU", "2503": "ООО Ромашка",
    "23557": {"number": "ЕАЭС №RU Д-1", "date": "2025-12-01"},
}


class FakeFeedNk:
    """generate_gtins → drafts по числу запроса (drafts≠None — урезанный лимит); feed записывает entries."""

    def __init__(self, drafts: int | None = None):
        self.drafts_cap = drafts
        self.fed: list[list[dict]] = []

    def generate_gtins(self, token, quantity):
        n = quantity if self.drafts_cap is None else min(self.drafts_cap, quantity)
        return {"drafts": [{"gtin": f"4630520699{i:04d}"} for i in range(n)],
                "monthly-limit": {"limit": 11000, "used": 0 if self.drafts_cap is None else 10999}}

    def feed(self, token, entries):
        self.fed.append(entries)
        return {"feed_id": 42}


@pytest.fixture
def seeds(db):
    b = Batch(source_filename="seed.xlsx", status="new")
    db.add(b); db.flush()
    db.add(Card(article="G-1", gtin="4630520699970", batch_id=b.id, tnved="6109100000",
                name="Футболка тест", cat_id="214943", attributes=dict(ATTRS), status="ok"))
    db.add(Card(article="NOGTIN", gtin="", batch_id=b.id, tnved="6109100000",
                name="Футболка тест 2", cat_id="214943", attributes=dict(ATTRS), status="ok"))
    db.commit()
    return b


def test_feed_generates_gtins_and_builds_entries(db, seeds):
    fake = FakeFeedNk()
    out = feed_batch(db, seeds.id, fake, "T")
    assert out == {"feed_id": 42, "feed_ids": [42]}
    cards = {c.article: c for c in db.query(Card).filter(Card.batch_id == seeds.id).all()}
    assert all(c.status == "fed" for c in cards.values())
    gen = cards["NOGTIN"].gtin  # gtin сгенерирован и сохранён
    assert gen.startswith("4630520699") and len(gen) == 14 and gen.isdigit()
    assert cards["G-1"].gtin == "4630520699970"  # имевшийся gtin не тронут
    b = db.get(Batch, seeds.id)
    assert b.status == "moderation" and b.feed_id == "42"
    assert b.stats["feed_ids"] == [42]
    assert b.stats["gtin_limit"] == {"limit": 11000, "used": 0}
    # одна отсылка, обе карточки, формат entry — как в дампе /nk/feed
    assert len(fake.fed) == 1 and len(fake.fed[0]) == 2
    by_gtin = {e["gtin"]: e for e in fake.fed[0]}
    assert set(by_gtin) == {"4630520699970", gen}
    e = by_gtin["4630520699970"]
    assert e["good_name"] == "Футболка тест" and e["tnved"] == "6109100000"
    assert e["brand"] == "YCPB" and e["categories"] == [214943]
    assert e["moderation"] == 1  # поле entry (дамп): модерация сразу после feed
    ga = e["good_attrs"]
    ids = [a["attr_id"] for a in ga]
    assert 2504 not in ids  # бренд уехал в entry.brand, не в good_attrs
    assert all(isinstance(a["attr_id"], int) for a in ga)
    assert all(set(a) <= {"attr_id", "attr_value", "attr_value_type"} for a in ga)
    flat = [a for a in ga if a["attr_id"] == 23557]  # декларация → "номер:::дата"
    assert flat == [{"attr_id": 23557, "attr_value": "ЕАЭС №RU Д-1:::2025-12-01"}]
    per_item = [a for a in ga if a["attr_id"] == 13836]  # список → по записи на элемент
    assert per_item == [{"attr_id": 13836, "attr_value": "хб"},
                        {"attr_id": 13836, "attr_value": "шерсть"}]
    # live: квалифицированные атрибуты — строка attr_value + attr_value_type (не dict)
    a35 = next(a for a in ga if a["attr_id"] == 35)
    assert a35 == {"attr_id": 35, "attr_value": "жен", "attr_value_type": "пол"}
    a13914 = next(a for a in ga if a["attr_id"] == 13914)
    assert a13914 == {"attr_id": 13914, "attr_value": "белый", "attr_value_type": "цв"}


def test_feed_generated_gtin_zfilled_to_14(db, seeds):
    """live: generate-gtins отдаёт 13-значный gtin — карточке и entry нужен 14-значный."""
    class FakeDraft13:
        def __init__(self):
            self.fed: list[list[dict]] = []

        def generate_gtins(self, token, quantity):
            assert quantity == 1
            return {"drafts": [{"gtin": "4630562322348"}], "monthly-limit": {}}

        def feed(self, token, entries):
            self.fed.append(entries)
            return {"feed_id": 42}

    fake = FakeDraft13()
    assert feed_batch(db, seeds.id, fake, "T") == {"feed_id": 42, "feed_ids": [42]}
    card = db.query(Card).filter_by(article="NOGTIN").one()
    assert card.gtin == "04630562322348"  # zfill(14) сохранён в карточке
    assert "04630562322348" in {e["gtin"] for e in fake.fed[0]}  # и в entry фида


def test_feed_entry_omits_empty_attr_values():
    """live-отклонение: «attr_id 2503 можно использовать только с attr_value» —
    пустые значения (None, "", пустой список, value="" у dict) в good_attrs
    не попадают вовсе, а не уходят как {"attr_id": ..., "attr_value": ""}."""
    from marko.nkmt.service import _feed_entry
    card = Card(article="E-1", gtin="4630520699970", tnved="6109100000",
                name="Футболка тест", cat_id="214943", status="ok",
                attributes={"2504": "YCPB", "2503": "",  # producer по умолчанию ""
                            "2478": "Футболка тест", "13836": [],
                            "35": {"type": "пол", "value": ""}})
    e = _feed_entry(card)
    ids = [a["attr_id"] for a in e["good_attrs"]]
    assert 2503 not in ids and 35 not in ids and 13836 not in ids
    assert ids == [2478]  # остались только непустые


def test_feed_guards(db, seeds):
    db.get(Batch, seeds.id).status = "moderation"; db.commit()
    with pytest.raises(ValueError):
        feed_batch(db, seeds.id, FakeFeedNk(), "T")
    with pytest.raises(ValueError):  # батча нет
        feed_batch(db, 99999, FakeFeedNk(), "T")


def test_feed_gtin_limit_shortfall(db, seeds):
    with pytest.raises(RuntimeError, match="monthly"):
        feed_batch(db, seeds.id, FakeFeedNk(drafts=0), "T")
    b = db.get(Batch, seeds.id)
    assert b.stats["gtin_limit"]["used"] == 10999  # лимит сохранён для UI
    assert b.status == "new"  # батч не перешёл ни в какой статус
    assert db.query(Card).filter_by(article="NOGTIN").one().gtin == ""


def test_feed_endpoint(db, client, monkeypatch, seeds):
    monkeypatch.setattr("marko.connector_mt.manager.get_token", lambda _db: "T")
    monkeypatch.setattr("marko.nkmt.client.NkClient", lambda base: FakeFeedNk())
    r = client.post(f"/v1/nkmt/batches/{seeds.id}/feed", headers=AUTH)
    assert r.status_code == 200 and r.json() == {"feed_id": 42, "feed_ids": [42]}
    assert client.post(f"/v1/nkmt/batches/{seeds.id}/feed",
                       headers=AUTH).status_code == 409  # уже moderation
    assert client.post("/v1/nkmt/batches/99999/feed", headers=AUTH).status_code == 404
