import logging
import time
from datetime import date, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from marko.connector_wb.ingest import ingest_excise
from marko.connector_wb.registry import non_fbs_docs, registry_docs, upsert_orders
from marko.platform.models import PlatformKV
from marko.settings import settings

log = logging.getLogger("marko.poll")


def run_once(db: Session, client) -> dict:
    # Реестр заказов прогревается ДО ingest: классификация эксайз-строки опирается
    # на персистентный wb.orders, а не на «видимость заказа в снапшоте прямо
    # сейчас». orders()/upsert_orders не пишут в журнал — commit внутри
    # _gate_excise (excise_report) не подтянет незавершённых journal-записей.
    orders = client.orders()
    upsert_orders(db, orders)
    known_docs, fbw_docs = registry_docs(db), non_fbs_docs(db)
    to, frm = date.today(), date.today() - timedelta(days=settings.excise_days_back)
    rows = client.excise_report(frm.isoformat(), to.isoformat())
    stats = ingest_excise(db, rows, fbw_docs=fbw_docs, known_docs=known_docs)
    now = time.time()
    marker = {"at": now, "stats": stats}
    db.execute(pg_insert(PlatformKV).values(
        key="wb_last_poll", value=marker,
    ).on_conflict_do_update(
        index_elements=[PlatformKV.key],
        set_={"value": marker},
    ))
    db.commit()
    log.info("poll done: %s (registry=%d, fbw_docs=%d)", stats, len(known_docs), len(fbw_docs))
    return stats
