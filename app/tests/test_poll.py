from datetime import datetime, timedelta, timezone

from mpmt.connector_wb.poll import run_once
from mpmt.worker import seconds_until

MSK = timezone(timedelta(hours=3))


class FakeClient:
    def __init__(self):
        self.db = None

    def orders(self, limit=1000):
        return [{"rid": "s1"}]

    def excise_report(self, date_from, date_to):
        return [{"excise_short": "0104630520676025215CCCCCCC", "srid": "s1",
                 "operation_type_id": 1, "price": 1793, "nm_id": 1, "fiscal_dt": "2026-09-01"}]


def test_run_once_ingests(db):
    stats = run_once(db, FakeClient())
    assert stats["sale"] == 1


def test_seconds_until():
    cron = ["06:30", "18:30"]
    # 06:00 MSK → ближайший слот 06:30 сегодня = 1800 c
    assert seconds_until(cron, datetime(2026, 9, 2, 6, 0, tzinfo=MSK)) == 1800.0
    # ровно 06:30 — слот не в будущем (t <= now), следующий 18:30 сегодня = 12 ч
    assert seconds_until(cron, datetime(2026, 9, 2, 6, 30, tzinfo=MSK)) == 12 * 3600.0
    # 19:00 — оба слота сегодня прошли, ближайший 06:30 завтра = 11.5 ч
    assert seconds_until(cron, datetime(2026, 9, 2, 19, 0, tzinfo=MSK)) == 11.5 * 3600.0
