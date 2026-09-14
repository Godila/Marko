import json
import pathlib

from marko.connector_wb.ingest import excise_rows_to_events, ingest_excise
from marko.connector_wb.registry import order_doc

FIXT = json.loads(
    (pathlib.Path(__file__).parent / "fixtures" / "excise-report.json").read_text("utf-8")
)["response"]["data"]


def test_rows_to_events_shape():
    evs = excise_rows_to_events(FIXT)
    assert len(evs) == len(FIXT) == 1022
    e = evs[0]
    assert set(e) == {"source", "source_event_id", "kind", "km", "srid", "payload"}
    assert e["source"] == "wb_excise"
    assert e["source_event_id"] == f"{e['srid']}:{e['km']}:{FIXT[0]['operation_type_id']}"
    assert e["kind"] == ("sale" if FIXT[0]["operation_type_id"] == 1 else "return")
    assert len(e["km"]) == 31


def test_ingest_journals_despite_suffix_drift(db):
    # регресс инцидента 09.2026: эксайз srid '.3.0' против rid заказа '.2.0' —
    # один документ, продажа наша, обязана попасть в журнал
    doc = "eAL.rfa7f255e5c1646ef97d7f483176a2908"
    rows = [{"excise_short": "0104630520676025215AAAAAAA", "srid": f"{doc}.3.0",
             "operation_type_id": 1, "price": 1793, "nm_id": 1, "fiscal_dt": "2026-09-01"}]
    stats = ingest_excise(db, rows, fbw_docs=set(), known_docs={doc})
    assert stats == {"sale": 1, "return": 0, "skipped_fbw": 0, "duplicates": 0, "fbs_unknown": 0}
    from marko.journal.models import Item
    assert db.get(Item, "0104630520676025215AAAAAAA").state == "PENDING_WITHDRAW"


def test_ingest_unknown_doc_is_our_fbs(db):
    # заказ исчез из снапшота раньше эксайз-строки: документ вне реестра —
    # классифицируем как наш FBS и считаем в fbs_unknown (трипваер)
    rows = [{"excise_short": "0104630520676025215BBBBBBB", "srid": "eZ.runknown.0.0",
             "operation_type_id": 1, "price": 1793, "nm_id": 1, "fiscal_dt": "2026-09-01"}]
    stats = ingest_excise(db, rows, fbw_docs=set(), known_docs=set())
    assert stats == {"sale": 1, "return": 0, "skipped_fbw": 0, "duplicates": 0, "fbs_unknown": 1}


def test_ingest_known_fbw_skipped_without_item(db):
    rows = [{"excise_short": "0104630520676025215CCCCCCC", "srid": "eB.rbo.0.0",
             "operation_type_id": 1, "price": 1793, "nm_id": 1, "fiscal_dt": "2026-09-01"}]
    stats = ingest_excise(db, rows, fbw_docs={"eB.rbo"}, known_docs={"eB.rbo"})
    assert stats == {"sale": 0, "return": 0, "skipped_fbw": 1, "duplicates": 0, "fbs_unknown": 0}
    from marko.journal.models import Event, Item
    assert db.query(Item).count() == 0
    assert db.query(Event).filter_by(kind="skip_fbw").count() == 1


def test_ingest_fixture_split(db):
    # живой дамп 1022 строки (982 op=1 / 40 op=2): первые 5 документов объявляем
    # FBW в реестре, остальные — наши FBS. Расклад считаем по строкам, включая
    # байт-в-байт дубль (WB отдал одну строку дважды).
    docs = [order_doc(r["srid"]) for r in FIXT]
    first_docs: list = []
    for d in docs:
        if d not in first_docs:
            first_docs.append(d)
    fbw, known = set(first_docs[:5]), set(docs)

    expected = {"sale": 0, "return": 0, "skipped_fbw": 0, "duplicates": 0, "fbs_unknown": 0}
    seen, applied_kms = set(), set()
    for r, d in zip(FIXT, docs):
        seid = f"{r['srid']}:{r['excise_short']}:{r['operation_type_id']}"
        if seid in seen:
            expected["duplicates"] += 1
            continue
        seen.add(seid)
        if d in fbw:
            expected["skipped_fbw"] += 1
        else:
            applied_kms.add(r["excise_short"])
            expected["sale" if r["operation_type_id"] == 1 else "return"] += 1

    stats = ingest_excise(db, FIXT, fbw_docs=fbw, known_docs=known)
    assert stats == expected
    from marko.journal.models import Event, Item
    assert db.query(Item).count() == len(applied_kms)
    assert db.query(Event).filter_by(kind="skip_fbw").count() == expected["skipped_fbw"]


def test_ingest_idempotent_rerun(db):
    docs = {order_doc(r["srid"]) for r in FIXT}
    ingest_excise(db, FIXT, fbw_docs=set(), known_docs=docs)
    second = ingest_excise(db, FIXT, fbw_docs=set(), known_docs=docs)
    # скользящее 7-дневное окно: повтор — все дубликаты, unknown не переспамляет
    assert second == {"sale": 0, "return": 0, "skipped_fbw": 0,
                      "duplicates": 1022, "fbs_unknown": 0}
