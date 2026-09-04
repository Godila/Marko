import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from mpmt.connector_mt import manager
from mpmt.connector_wb.client import WBClient, WbHttpError, WbLimitError, load_wb_token
from mpmt.connector_wb.poll import run_once
from mpmt.db import SessionLocal
from mpmt.log import setup_logging
from mpmt.nkmt.client import NkClient
from mpmt.nkmt.models import Batch, Card
from mpmt.nkmt.service import refresh_batch, sign_batch
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
    except Exception as e:
        log.exception("poll cycle crashed")
        await send(f"ALERT: WB poll crashed: {e.__class__.__name__}: {e}")
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


def _signer_watchdog():
    """Раз в 30 мин: signer виделся и молчит > 2 ч → TG-алерт (не чаще раза на случай)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from mpmt.db import SessionLocal
    from mpmt.platform.models import PlatformKV
    while True:
        try:
            db = SessionLocal()
            kv = db.get(PlatformKV, "signer_last_seen")
            if kv:
                seen, alerted = kv.value.get("ts", 0.0), kv.value.get("alerted_ts", 0.0)
                now = time.time()
                if now - seen > 7200 and seen > alerted:
                    asyncio.run(send(f"ALERT: signer молчит > {int((now - seen) / 3600)} ч"))
                    db.execute(pg_insert(PlatformKV).values(
                        key="signer_last_seen",
                        value={**kv.value, "alerted_ts": seen}
                    ).on_conflict_do_update(index_elements=[PlatformKV.key],
                                            set_={"value": {**kv.value, "alerted_ts": seen}}))
                    db.commit()
            db.close()
        except Exception:
            log.exception("signer watchdog failed")
        time.sleep(1800)




def _docs_checker():
    """Раз в 10 мин: документы в submitted → опросить статус в ЧЗ до CHECKED_OK."""
    from mpmt.db import SessionLocal
    from mpmt.mt.models import MtDoc
    from mpmt.connector_mt import manager
    while True:
        try:
            db = SessionLocal()
            for doc in db.query(MtDoc).filter_by(status="submitted").all():
                try:
                    info = manager.check_doc(db, doc.id)
                    if doc.status in ("checked_ok", "error"):
                        asyncio.run(send(f"ЧЗ документ {doc.id} ({doc.type}): {doc.status} — {str(info.get('status'))}"))
                except Exception as e:
                    log.warning("docs check failed for %s: %s", doc.id, e)
            db.close()
        except Exception:
            log.exception("docs checker failed")
        time.sleep(600)


def nkmt_cycle(db) -> None:
    """Один проход НКМТ: moderation-батчи → refresh, signing с notsigned/
    error_sign-карточками → sign; терминальный переход батча (published/
    error) → TG-уведомление (id + статус + счёт карточек по статусам).

    Signing без notsigned/error_sign-карточек не дёргается (sign_batch и так
    бросил бы ValueError — не шумим лишним вызовом). Исключение батча не
    роняет цикл (паттерн _docs_checker): log.exception + rollback, следующий
    батч. send() не бросает и без TG-кредов — no-op (notifier).
    """
    try:
        client = NkClient(settings.mt_base_v3)
        token = manager.get_token(db)
    except Exception:
        log.exception("nkmt: client/token failed")
        return
    for batch in db.query(Batch).filter(
            Batch.status.in_(("moderation", "signing"))).order_by(Batch.id).all():
        try:
            if batch.status == "moderation":
                refresh_batch(db, batch.id, client, token)
            elif db.query(Card).filter(Card.batch_id == batch.id,
                                       Card.status.in_(("notsigned",
                                                        "error_sign"))).count():
                sign_batch(db, batch.id, client, token)
            else:
                continue  # нечего подписывать — терминального перехода нет
            db.refresh(batch)  # сервис мог закоммитить новый статус батча
            if batch.status in ("published", "error"):
                counts = dict(db.query(Card.status, func.count(Card.id))
                              .filter(Card.batch_id == batch.id)
                              .group_by(Card.status).all())
                tally = ", ".join(f"{st}={n}" for st, n in sorted(counts.items()))
                word = ("опубликован" if batch.status == "published"
                        else "завершился ошибкой")
                asyncio.run(send(f"НКМТ батч {batch.id}: {word} "
                                 f"({batch.status}); карточки: {tally}"))
        except Exception:
            db.rollback()
            log.exception("nkmt cycle failed for batch %s", batch.id)


def _nkmt_loop():
    """Раз в 10 мин: refresh модерации + автоподписание signing-батчей."""
    from mpmt.db import SessionLocal
    while True:
        try:
            db = SessionLocal()
            nkmt_cycle(db)
            db.close()
        except Exception:
            log.exception("nkmt loop failed")
        time.sleep(600)


def _wb_returns_loop():
    """Раз в час: монитор возвратов WB (goods-return, квота 2/1ч — гейт в клиенте)
    + TG-алерты (новый возврат; дедлайн забора ≤48 ч)."""
    from mpmt.connector_wb.returns import run_returns_once
    from mpmt.db import SessionLocal
    while True:
        db = None
        try:
            db = SessionLocal()
            client = WBClient(token=load_wb_token(settings.wb_token_file), db=db)
            res = run_returns_once(db, client)
            for text in res.get("alerts", []):
                asyncio.run(send(text))
        except (WbHttpError, WbLimitError) as e:
            log.error("wb returns poll failed: %s", e)
        except Exception:
            log.exception("wb returns loop failed")
        finally:
            if db:
                db.close()
        time.sleep(3600)


def main():
    setup_logging()
    log.info("worker started, excise cron %s MSK", settings.poll_excise_cron)
    import threading
    threading.Thread(target=_signer_watchdog, daemon=True).start()
    threading.Thread(target=_docs_checker, daemon=True).start()
    threading.Thread(target=_nkmt_loop, daemon=True).start()
    threading.Thread(target=_wb_returns_loop, daemon=True).start()
    while True:
        wait = seconds_until(settings.poll_excise_cron, datetime.now(MSK))
        log.info("next excise poll in %.0f s", wait)
        time.sleep(max(wait, 1))
        asyncio.run(poll_cycle())


if __name__ == "__main__":
    main()
