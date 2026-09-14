from datetime import datetime, timedelta, timezone

from marko.connector_wb.poll import run_once
from marko.worker import seconds_until

MSK = timezone(timedelta(hours=3))

DOC = "eAL.rfa7f255e5c1646ef97d7f483176a2908"


class FakeClient:
    def __init__(self):
        self.db = None

    def orders(self, limit=1000):
        # rid заказа с суффиксом '.2.0' — эксайз придёт с '.3.0' (дрейф инцидента)
        return [{"rid": f"{DOC}.2.0", "deliveryType": "fbs", "nmId": 1,
                 "createdAt": "2026-08-30T00:00:00Z"}]

    def excise_report(self, date_from, date_to):
        return [{"excise_short": "0104630520676025215CCCCCCC", "srid": f"{DOC}.3.0",
                 "operation_type_id": 1, "price": 1793, "nm_id": 1, "fiscal_dt": "2026-09-01"}]


def test_run_once_ingests_despite_suffix_drift(db):
    stats = run_once(db, FakeClient())
    assert stats["sale"] == 1 and stats["fbs_unknown"] == 0
    from marko.connector_wb.models import WbOrder
    from marko.journal.models import Item
    # реестр персистентен: документ заказа останется после ухода из снапшота
    assert db.get(WbOrder, DOC).delivery_type == "fbs"
    assert db.get(Item, "0104630520676025215CCCCCCC").state == "PENDING_WITHDRAW"


def test_run_once_idempotent(db):
    run_once(db, FakeClient())
    stats = run_once(db, FakeClient())
    assert stats == {"sale": 0, "return": 0, "skipped_fbw": 0, "duplicates": 1, "fbs_unknown": 0}


def test_seconds_until():
    cron = ["06:30", "18:30"]
    # 06:00 MSK → ближайший слот 06:30 сегодня = 1800 c
    assert seconds_until(cron, datetime(2026, 9, 2, 6, 0, tzinfo=MSK)) == 1800.0
    # ровно 06:30 — слот не в будущем (t <= now), следующий 18:30 сегодня = 12 ч
    assert seconds_until(cron, datetime(2026, 9, 2, 6, 30, tzinfo=MSK)) == 12 * 3600.0
    # 19:00 — оба слота сегодня прошли, ближайший 06:30 завтра = 11.5 ч
    assert seconds_until(cron, datetime(2026, 9, 2, 19, 0, tzinfo=MSK)) == 11.5 * 3600.0
