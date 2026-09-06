"""Монитор возвратов WB (goods-return): физическое движение товара к продавцу.

Регламент: забрать возврат с ПВЗ надо до expiredDt (WB хранит 7 дней), поэтому
каждый новый возврат и приближение дедлайна (≤48 ч) подсвечиваем TG-алертом.
"""
import logging
import time
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from marko.connector_wb.models import WbReturn

log = logging.getLogger("marko.returns")

DEADLINE_ALERT_HOURS = 48


def ingest_returns(db: Session, rows: list[dict]) -> dict:
    """UPSERT по srid: новые — insert, изменившийся статус/даты — update.
    Флаги alerted_* трогаем только при insert (уведомления не повторяются)."""
    stats = {"new": 0, "updated": 0, "unchanged": 0}
    for row in rows:
        srid = str(row.get("srid") or "").strip()
        if not srid:
            continue
        obj = db.get(WbReturn, srid)
        status = str(row.get("status") or "")
        expired = str(row.get("expiredDt") or "")
        if obj is None:
            db.add(WbReturn(srid=srid, order_id=int(row.get("orderId") or 0),
                            status=status, expired_dt=expired, payload=row))
            stats["new"] += 1
        elif (obj.status, obj.expired_dt) != (status, expired) or obj.payload != row:
            obj.status, obj.expired_dt, obj.payload = status, expired, row
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
    db.commit()
    return stats


def _parse_iso(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def alert_returns(db: Session, now: datetime | None = None) -> list[str]:
    """Готовит тексты TG-алертов и отмечает флаги, чтобы не спамить повторно.

    1) новый возврат (alerted_new=False);
    2) дедлайн забора ≤48 ч, товар ещё не выдан (completedDt пуст).
    """
    now = now or datetime.now()
    out: list[str] = []
    for r in db.query(WbReturn).all():
        p = r.payload or {}
        if not r.alerted_new:
            out.append(f"WB возврат: заказ {r.order_id} ({p.get('subjectName') or p.get('nmId')}) "
                       f"причина «{p.get('reason') or '—'}», статус «{r.status}»")
            r.alerted_new = True
        if not r.alerted_deadline and not p.get("completedDt"):
            dl = _parse_iso(r.expired_dt)
            if dl and 0 <= (dl - now).total_seconds() <= DEADLINE_ALERT_HOURS * 3600:
                out.append(f"WB возврат {r.order_id}: забрать до {r.expired_dt} "
                           f"(иначе вернут на склад WB)")
                r.alerted_deadline = True
    db.commit()
    return out


def run_returns_once(db: Session, client, days_back: int = 7) -> dict:
    rows = client.goods_return((date.today() - timedelta(days=days_back)).isoformat(),
                               date.today().isoformat())
    stats = ingest_returns(db, rows)
    alerts = alert_returns(db)
    if stats["new"] or alerts:
        log.info("returns poll: %s, alerts=%d", stats, len(alerts))
    mark_loop(db)
    return {**stats, "alerts": alerts}


def mark_loop(db: Session) -> None:
    """kv-метка живости мониторинга возвратов — её показывает пульс консоли."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from marko.platform.models import PlatformKV
    db.execute(pg_insert(PlatformKV).values(
        key="returns_loop_last", value={"ts": time.time()},
    ).on_conflict_do_update(
        index_elements=[PlatformKV.key], set_={"value": {"ts": time.time()}},
    ))
    db.commit()
