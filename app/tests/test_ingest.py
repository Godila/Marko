import json
import pathlib

from marko.connector_wb.ingest import excise_rows_to_events, fbs_rids, ingest_excise

FIXT = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "excise-report.json").read_text("utf-8")
)["response"]["data"]


def test_rows_to_events_shape():
    evs = excise_rows_to_events(FIXT)
    assert len(evs) == len(FIXT) == 1022
    e = evs[0]
    assert set(e) == {"source", "source_event_id", "kind", "km", "srid", "payload"}
    assert e["source"] == "wb_excise"
    assert e["source_event_id"] == f"{e['srid']}:{e['km']}:1"
    assert e["kind"] == "sale"
    assert len(e["km"]) == 31


def test_ingest_filters_fbw(db):
    # вся фикстура прода — FBW: ни один srid не входит в fbs-множество.
    # В фикстуре 1022 строки, но одна пара байт-в-байт одинаковых строк
    # (WB отдал один и тот же возврат дважды) → журнал дедуплицирует: 1021 + 1 dup.
    # FBW — вне контура: только аудит-события, позиций в журнале НЕ создаётся.
    stats = ingest_excise(db, FIXT, fbs=set())
    assert stats == {"sale": 0, "return": 0, "skipped_fbw": 1021, "duplicates": 1}
    from marko.journal.models import Event, Item
    assert db.query(Item).count() == 0
    assert db.query(Event).filter_by(kind="skip_fbw").count() == 1021


def test_ingest_sale_and_return(db):
    rows = [
        {"excise_short": "0104630520676025215AAAAAAA", "srid": "s1", "operation_type_id": 1,
         "price": 1793, "nm_id": 1, "fiscal_dt": "2026-06-01"},
        {"excise_short": "0104630520676025215BBBBBBB", "srid": "s2", "operation_type_id": 2,
         "price": 1793, "nm_id": 1, "fiscal_dt": "2026-06-05"},
    ]
    stats = ingest_excise(db, rows, fbs=fbs_rids([{"rid": "s1"}, {"rid": "s2"}, {}]))
    assert stats == {"sale": 1, "return": 1, "skipped_fbw": 0, "duplicates": 0}


def test_ingest_idempotent_rerun(db):
    # повторный прогон тех же строк — все дубликаты, состояние не меняется
    first = ingest_excise(db, FIXT, fbs=set())
    assert first == {"sale": 0, "return": 0, "skipped_fbw": 1021, "duplicates": 1}
    second = ingest_excise(db, FIXT, fbs=set())
    assert second == {"sale": 0, "return": 0, "skipped_fbw": 0, "duplicates": 1022}
