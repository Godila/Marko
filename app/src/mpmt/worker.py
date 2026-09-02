import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from mpmt.connector_wb.client import WBClient, WbHttpError, WbLimitError, load_wb_token
from mpmt.connector_wb.poll import run_once
from mpmt.db import SessionLocal
from mpmt.log import setup_logging
from mpmt.notifier import send
from mpmt.settings import settings

log = logging.getLogger("mpmt.worker")
MSK = timezone(timedelta(hours=3))


async def poll_cycle():
    db = SessionLocal()
    try:
        client = WBClient(token=load_wb_token(settings.wb_token_file), db=db)
        stats = run_once(db, client)
        if stats["sale"] or stats["return"]:
            await send(f"WB poll: {stats}")
    except (WbHttpError, WbLimitError) as e:
        log.error("poll failed: %s", e)
        await send(f"ALERT: WB poll failed: {e}")
    except Exception:
        log.exception("poll cycle crashed")
    finally:
        db.close()


def seconds_until(cron_times: list[str], now: datetime) -> float:
    nxt = None
    for hhmm in cron_times:
        h, m = map(int, hhmm.split(":"))
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if t <= now:
            t += timedelta(days=1)
        nxt = t if nxt is None or t < nxt else nxt
    return (nxt - now).total_seconds()


def main():
    setup_logging()
    log.info("worker started, excise cron %s MSK", settings.poll_excise_cron)
    while True:
        wait = seconds_until(settings.poll_excise_cron, datetime.now(MSK))
        log.info("next excise poll in %.0f s", wait)
        time.sleep(max(wait, 1))
        asyncio.run(poll_cycle())


if __name__ == "__main__":
    main()
