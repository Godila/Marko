from datetime import datetime, timedelta

from marko.connector_wb.models import WbReturn
from marko.connector_wb.returns import alert_returns, ingest_returns, run_returns_once


def _row(srid="r1", order=100, status="В пути в пвз", expired=None, **over):
    row = {"srid": srid, "orderId": order, "status": status,
           "expiredDt": expired, "reason": "Цвет", "subjectName": "Шапка",
           "isStatusActive": 1, "completedDt": None, "nmId": 123}
    row.update(over)
    return row


class FakeClient:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def goods_return(self, date_from, date_to):
        self.calls.append((date_from, date_to))
        return self.rows


def test_ingest_returns_upsert_by_srid(db):
    s1 = ingest_returns(db, [_row(), _row(srid="r2", order=101)])
    assert s1 == {"new": 2, "updated": 0, "unchanged": 0}
    s2 = ingest_returns(db, [_row(status="Готов к выдаче")])          # тот же srid, новый статус
    assert s2 == {"new": 0, "updated": 1, "unchanged": 0}
    s3 = ingest_returns(db, [_row(status="Готов к выдаче")])          # идемпотентно
    assert s3 == {"new": 0, "updated": 0, "unchanged": 1}
    obj = db.get(WbReturn, "r1")
    assert obj.status == "Готов к выдаче" and obj.order_id == 100
    assert db.get(WbReturn, "r2").order_id == 101


def test_alerts_new_once_and_deadline_window(db):
    soon = (datetime.now() + timedelta(hours=24)).isoformat()
    ingest_returns(db, [_row(expired=soon)])
    a1 = alert_returns(db, datetime.now())
    assert len(a1) == 2                     # новый возврат + дедлайн ≤48 ч
    assert "забрать до" in a1[1]
    a2 = alert_returns(db, datetime.now())
    assert a2 == []                         # флаги — без повторов


def test_alerts_no_deadline_when_completed_or_far(db):
    far = (datetime.now() + timedelta(days=5)).isoformat()
    ingest_returns(db, [_row(srid="rc", expired=far, completedDt="2026-09-04T10:00:00"),
                        _row(srid="rf", expired=far)])
    alerts = alert_returns(db, datetime.now())
    assert len(alerts) == 2                 # только «новый возврат», дедлайнов нет
    assert all("забрать до" not in a for a in alerts)


def test_run_returns_once_end_to_end(db):
    c = FakeClient([_row()])
    res = run_returns_once(db, c)
    assert res["new"] == 1 and len(res["alerts"]) == 1
    assert c.calls and c.calls[0][0] < c.calls[0][1]   # окно dateFrom < dateTo
