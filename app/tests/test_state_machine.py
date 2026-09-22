import pytest
from marko.journal.state import transition


@pytest.mark.parametrize("state,kind,expected", [
    ("NEW", "sale", "PENDING_WITHDRAW"),
    ("NEW", "return", "ANOMALY_NO_RECEIPT"),
    # перепродажа возврата — штатный цикл WB FBS: обязательство вывода
    # идемпотентно продаже («к выводу» остаётся «к выводу», «выведен» —
    # «выведенным»), событие пишется в журнал как след
    ("PENDING_WITHDRAW", "sale", "PENDING_WITHDRAW"),
    ("PENDING_WITHDRAW", "return", "PENDING_RETURN"),
    ("WITHDRAWN", "return", "PENDING_RETURN"),
    ("PENDING_RETURN", "sale", "PENDING_WITHDRAW"),
    ("PENDING_RETURN", "return", "ANOMALY_RERETURN"),
    ("RETURNED", "sale", "PENDING_WITHDRAW"),
    ("WITHDRAWN", "sale", "WITHDRAWN"),
])
def test_transitions(state, kind, expected):
    assert transition(state, kind) == expected


def test_apply_event_idempotent(db):
    from marko.journal import apply_event
    s1, c1 = apply_event(db, source="wb_excise", source_event_id="e1", kind="sale",
                         km="0104630520676025215UKsEhVmAtad", srid="r1", payload={"price": 1793})
    s2, c2 = apply_event(db, source="wb_excise", source_event_id="e1", kind="sale",
                         km="0104630520676025215UKsEhVmAtad", srid="r1", payload={"price": 1793})
    assert (s1, c1) == ("PENDING_WITHDRAW", True)
    assert (s2, c2) == ("PENDING_WITHDRAW", False)   # дубль не создан


def test_resale_is_normal_not_anomaly(db):
    """Повторная продажа (возврат → WB перевыставил единицу) не создаёт
    аномалию: у «к выводу» пересоздаётся обязательство по последней продаже,
    у «выведенного» состояние сохраняется; оба события остаются в журнале."""
    from marko.journal import apply_event
    from marko.journal.models import Item
    km = "0104630520676025215RESALE01"
    apply_event(db, source="wb_excise", source_event_id="r1", kind="sale",
                km=km, srid="r1", payload={"price": 4282, "fiscal_dt": "2026-09-04"})
    s, _ = apply_event(db, source="wb_excise", source_event_id="r2", kind="sale",
                       km=km, srid="r2", payload={"price": 3479, "fiscal_dt": "2026-09-18"})
    assert s == "PENDING_WITHDRAW"
    it = db.get(Item, km)
    assert it.last_event["fiscal_dt"] == "2026-09-18"   # свежий чек для LK_RECEIPT
    assert db.query(Item.km).filter(Item.state.like("ANOMALY%")).count() == 0
    # выведенный (любым способом) при повторной продаже остаётся выведенным
    apply_event(db, source="emitter", source_event_id="w1", kind="withdraw",
                km=km, srid="", payload={"doc_id": 1})
    assert db.get(Item, km).state == "WITHDRAWN"
    s2, _ = apply_event(db, source="wb_excise", source_event_id="r3", kind="sale",
                        km=km, srid="r3", payload={"price": 3100, "fiscal_dt": "2026-09-25"})
    assert s2 == "WITHDRAWN"
