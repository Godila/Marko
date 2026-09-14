"""Реплей skip_fbw → sale/return: починка журнала после инцидента 09.2026
(классификация FBS/FBW по снапшоту orders() ошибочно скипала наши продажи)."""
from marko.connector_wb.ingest import ingest_excise
from marko.connector_wb.repair import replay_skip_fbw
from marko.journal.models import Event, Item

ROWS = [
    {"excise_short": "0104630520676025215AAAAAAA", "srid": "eA.ra.0.0",
     "operation_type_id": 1, "price": 1, "nm_id": 1, "fiscal_dt": "2026-09-01"},
    {"excise_short": "0104630520676025215BBBBBBB", "srid": "eB.rb.0.0",
     "operation_type_id": 1, "price": 1, "nm_id": 1, "fiscal_dt": "2026-09-02"},
    {"excise_short": "0104630520676025215CCCCCCC", "srid": "eC.rc.0.0",
     "operation_type_id": 2, "price": 1, "nm_id": 1, "fiscal_dt": "2026-09-03"},
]


def _seed_skip(db) -> dict:
    docs = {"eA.ra", "eB.rb", "eC.rc"}
    return ingest_excise(db, ROWS, fbw_docs=docs, known_docs=docs)


def test_replay_dry_run_changes_nothing(db):
    _seed_skip(db)
    res = replay_skip_fbw(db, dry_run=True)
    assert res["found"] == 3 and res["replayed"] == 0
    assert res["kinds"] == {"sale": 2, "return": 1}
    assert db.query(Event).filter_by(kind="skip_fbw").count() == 3
    assert db.query(Item).count() == 0


def test_replay_restores_events_and_states(db):
    _seed_skip(db)
    res = replay_skip_fbw(db)
    assert res["replayed"] == 3 and res["kinds"] == {"sale": 2, "return": 1}
    assert db.query(Event).filter_by(kind="skip_fbw").count() == 0
    # op=1 → PENDING_WITHDRAW; op=2 без первичной продажи → честная аномалия
    assert db.get(Item, "0104630520676025215AAAAAAA").state == "PENDING_WITHDRAW"
    assert db.get(Item, "0104630520676025215CCCCCCC").state == "ANOMALY_NO_RECEIPT"
    # маркер реплея в payload, source_event_id сохранён (дедуп-непрерывность)
    ev = db.query(Event).filter_by(km="0104630520676025215AAAAAAA").one()
    assert ev.kind == "sale" and ev.payload["replayed_from"] == "skip_fbw"
    assert ev.source_event_id == "eA.ra.0.0:0104630520676025215AAAAAAA:1"


def test_replay_orders_sale_before_return(db):
    # один КМ: продажа 01.09 и возврат 03.09, оба в skip_fbw —
    # реплей по fiscal_dt применяет продажу первой: PENDING_WITHDRAW → PENDING_RETURN
    rows = [
        {"excise_short": "0104630520676025215DDDDDDD", "srid": "eD.rd.0.0",
         "operation_type_id": 1, "price": 1, "nm_id": 1, "fiscal_dt": "2026-09-01"},
        {"excise_short": "0104630520676025215DDDDDDD", "srid": "eD.rd.1.0",
         "operation_type_id": 2, "price": 1, "nm_id": 1, "fiscal_dt": "2026-09-03"},
    ]
    docs = {"eD.rd"}
    ingest_excise(db, rows, fbw_docs=docs, known_docs=docs)
    replay_skip_fbw(db)
    assert db.get(Item, "0104630520676025215DDDDDDD").state == "PENDING_RETURN"


def test_replay_idempotent_and_dedup_continuous(db):
    _seed_skip(db)
    replay_skip_fbw(db)
    assert replay_skip_fbw(db)["found"] == 0
    # повторный ingest тех же строк — дубликаты: source_event_id непрерывен
    stats = ingest_excise(db, ROWS, fbw_docs=set(), known_docs=set())
    assert stats["duplicates"] == 3 and stats["sale"] == 0 and stats["return"] == 0


def test_replay_leaves_known_fbw_skips(db):
    # реплей воспроизводит классификацию нового кода: документ, который реестр
    # знает как не-FBS, остаётся skip_fbw (review P2 — легитимные скипы после
    # деплоя не должны превращаться в sale)
    from marko.connector_wb.registry import order_doc, upsert_orders
    _seed_skip(db)
    upsert_orders(db, [{"rid": "eB.rb.0.0", "deliveryType": "fbo", "nmId": 1,
                        "createdAt": "2026-09-01T00:00:00Z"}])
    res = replay_skip_fbw(db)
    assert res["found"] == 2 and res["left_fbw"] == 1 and res["replayed"] == 2
    ev = db.query(Event).filter_by(kind="skip_fbw").one()
    assert order_doc(ev.srid) == "eB.rb"
    assert db.get(Item, "0104630520676025215BBBBBBB") is None  # fbo — без позиции
