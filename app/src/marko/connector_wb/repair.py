"""Одноразовый ремонт журнала: wb_excise/skip_fbw → события продажи/возврата.

Инцидент 09.2026: классификация FBS/FBW по текущему снапшоту orders() отправила
131 нашу FBS-продажу в skip_fbw. Реплей пересобирает события через apply_event
(единая точка стейт-машины): старое событие удаляется и вставляется то же
source_event_id с kind по operation_type_id и маркером replayed_from — дедуп
остаётся непрерывным. Идемпотентно: после реплея строк под фильтром нет.

Запуск: docker exec marko-worker-1 python -m marko.connector_wb.repair [--dry-run]
"""
import sys

from sqlalchemy.orm import Session

from marko.connector_wb.registry import non_fbs_docs, order_doc
from marko.journal import apply_event
from marko.journal.models import Event


def replay_skip_fbw(db: Session, *, dry_run: bool = False) -> dict:
    # Реплей воспроизводит классификацию нового кода: события, чей документ
    # реестр знает как не-FBS, остаются skip_fbw (review P2: реплей после
    # первого poll-слота нового кода не должен трогать легитимные FBW-скипы).
    # По fiscal_dt: для КМ с парой sale+return продажа применится первой
    # (NEW→PENDING_WITHDRAW→PENDING_RETURN), а не ANOMALY_NO_RECEIPT.
    fbw = non_fbs_docs(db)
    all_events = (db.query(Event)
                  .filter_by(source="wb_excise", kind="skip_fbw")
                  .order_by(Event.payload.op("->>")("fiscal_dt"), Event.id)
                  .all())
    events = [ev for ev in all_events if order_doc(ev.srid) not in fbw]
    res = {"dry_run": dry_run, "found": len(events), "replayed": 0, "kinds": {},
           "left_fbw": len(all_events) - len(events)}
    for ev in events:
        kind = "sale" if ev.payload.get("operation_type_id") == 1 else "return"
        res["kinds"][kind] = res["kinds"].get(kind, 0) + 1
        if dry_run:
            continue
        payload = {**ev.payload, "replayed_from": "skip_fbw", "orig_event_id": ev.id}
        seid, km, srid = ev.source_event_id, ev.km, ev.srid
        db.delete(ev)
        db.flush()  # освободить uq_source_event внутри транзакции…
        _, created = apply_event(db, source="wb_excise", source_event_id=seid,
                                 kind=kind, km=km, srid=srid, payload=payload)
        # …внутренний commit apply_event запечатывает delete+insert+Item атомарно
        res["replayed"] += int(created)
    return res


if __name__ == "__main__":
    from marko.db import SessionLocal

    db = SessionLocal()
    try:
        print(replay_skip_fbw(db, dry_run="--dry-run" in sys.argv))
    finally:
        db.close()
