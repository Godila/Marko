import pytest
from marko.journal.state import transition


@pytest.mark.parametrize("state,kind,expected", [
    ("NEW", "sale", "PENDING_WITHDRAW"),
    ("NEW", "return", "ANOMALY_NO_RECEIPT"),
    ("PENDING_WITHDRAW", "sale", "ANOMALY_RESALE"),
    ("PENDING_WITHDRAW", "return", "PENDING_RETURN"),
    ("WITHDRAWN", "return", "PENDING_RETURN"),
    ("PENDING_RETURN", "sale", "PENDING_WITHDRAW"),
    ("PENDING_RETURN", "return", "ANOMALY_RERETURN"),
    ("RETURNED", "sale", "PENDING_WITHDRAW"),
    ("NEW", "skip_fbw", "SKIPPED_FBW"),
    ("WITHDRAWN", "sale", "ANOMALY_UNKNOWN_TRANSITION"),
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
