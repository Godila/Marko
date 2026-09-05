import logging
import time
from datetime import date, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from marko.connector_wb.ingest import fbs_rids, ingest_excise
from marko.platform.models import PlatformKV
from marko.settings import settings

log = logging.getLogger("marko.poll")


def run_once(db: Session, client) -> dict:
    # Порядок обязателен (review Task 10): orders() → fbs-множество СТРОГО ДО
    # excise_report()/ingest — строка, journaled как skip_fbw, уже никогда не
    # станет sale. orders() ничего не пишет, поэтому _gate_excise (commit внутри
    # excise_report) не подтянет незавершённые journal-записи.
    orders = client.orders()
    fbs = fbs_rids(orders)
    to, frm = date.today(), date.today() - timedelta(days=settings.excise_days_back)
    rows = client.excise_report(frm.isoformat(), to.isoformat())
    stats = ingest_excise(db, rows, fbs)
    now = time.time()
    marker = {"at": now, "stats": stats}
    db.execute(pg_insert(PlatformKV).values(
        key="wb_last_poll", value=marker,
    ).on_conflict_do_update(
        index_elements=[PlatformKV.key],
        set_={"value": marker},
    ))
    db.commit()
    log.info("poll done: %s (fbs_rids=%d)", stats, len(fbs))
    return stats
