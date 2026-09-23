"""Клиентские возвраты покупателей (sales R-строки) → журнал.

Доктрина (discovery 23.09, crpt-specs/returns-discovery-2026-09-23.md §5–6):
после чек-сплита 01.09 WB-чеки FBS-контура идут без КМ — продажа не выводит
код автоматически, возврат покупателя не возвращает (статус ЧЗ замирает).
Финансовый след (saleID «R…») — единственный детектор возврата.

Правила применения к журналу:
- PENDING_WITHDRAW (вывод ещё не проведён) → RETURNED: продажа отменена,
  код в обороте, товар на фулфилменте — обязательство снимается;
- WITHDRAWN + withdrawn_by='us' (наш LK_RECEIPT) → PENDING_RETURN: код
  выбыл, товар вернулся к продавцу — собирать LP_RETURN;
- прочее (вывод WB 'wb'/FBO-зона, аномалии) — только событие-наблюдение,
  состояние не трогаем: код в зоне ответственности WB.
"""
from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from marko.connector_wb.models import WbClientReturn
from marko.connector_wb.registry import order_doc
from marko.journal import apply_event, log_action
from marko.journal.lookup import _esc
from marko.journal.models import Event, Item


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# «Склад WB РФ» в sales-отчёте = FBW-склад (FBO-остатки, зона WB); остальное —
# склады продавца/фулфилмента. Реестр delivery_type точнее — он побеждает
def contour_of(warehouse: str, delivery_type: str | None) -> str | None:
    if delivery_type:
        return delivery_type
    if not warehouse:
        return None
    return "fbo" if "Склад WB" in warehouse else "fbs"


def ingest_client_returns(db: Session, rows: list[dict]) -> dict:
    """UPSERT R-строк → матчинг по документу заказа → применение к журналу.

    Повторный вызов (часовой цикл, окно 30 дней) идемпотентен: применённые
    строки пропускаются, неподобранные до-матчатся, когда доедет эксайз.
    """
    stats = {"new": 0, "staged": 0, "applied": 0, "observed": 0}
    for row in rows:
        sid = str(row.get("saleID") or "").strip()
        srid = str(row.get("srid") or "").strip()
        if not sid or not srid:
            continue
        wh = str(row.get("warehouseName") or "")[:64]
        rdate = str(row.get("date") or "")[:32]
        doc = order_doc(srid)
        obj = db.get(WbClientReturn, srid)
        if obj is None:
            db.add(WbClientReturn(srid=srid, sale_id=sid, rdate=rdate,
                                  warehouse=wh, order_doc=doc, payload=row))
            stats["new"] += 1
        elif (obj.sale_id, obj.rdate, obj.warehouse) != (sid, rdate, wh):
            obj.sale_id, obj.rdate, obj.warehouse = sid, rdate, wh
            obj.payload = row
    db.commit()

    # матчинг стейджинга: (а) точное совпадение srid — sales и эксайз одна
    # система WB; (б) doc-фолбэк (суффиксы '.n.m' расходятся — сравнение по
    # order_doc) ДЕТЕРМИНИРОВАН: ORDER BY id, один КМ на R-строку — у
    # многопозиционного заказа свой КМ на позицию, чужой брать нельзя
    # (ревью 23.09: «последний выиграл» без порядка рвал частичные возвраты)
    pending = (db.query(WbClientReturn)
               .filter(WbClientReturn.km.is_(None),
                       WbClientReturn.applied_at.is_(None)).all())
    if pending:
        exact = {s: km for s, km in
                 db.query(Event.srid, Event.km)
                 .filter(Event.srid.in_([r.srid for r in pending]),
                         Event.kind == "sale").all()}
        docs = sorted({r.order_doc for r in pending if r.order_doc})
        sf = or_(*[Event.srid.like(_esc(d) + ".%", escape="\\") for d in docs])
        doc_km: dict[str, list[str]] = {}
        for s, km in (db.query(Event.srid, Event.km)
                      .filter(sf, Event.kind == "sale")
                      .order_by(Event.id).all()):
            doc_km.setdefault(order_doc(s), []).append(km)
        taken = {k for (k,) in db.query(WbClientReturn.km)
                 .filter(WbClientReturn.km.isnot(None),
                         WbClientReturn.applied_at.isnot(None))
                 .distinct().all()}
        for r in pending:
            km = exact.get(r.srid)
            if not km:
                for cand in doc_km.get(r.order_doc, []):
                    if cand not in taken:
                        km = cand
                        break
            if km:
                r.km = km
                taken.add(km)
        db.commit()
        stats["staged"] = sum(1 for r in pending if not r.km)

    # применение: два живых правила + наблюдение для зоны WB; ключ события
    # «saleID:srid» — у одного финдокумента может быть несколько R-строк
    ready = (db.query(WbClientReturn)
             .filter(WbClientReturn.km.isnot(None),
                     WbClientReturn.applied_at.is_(None))
             .order_by(WbClientReturn.rdate).all())
    for r in ready:
        it = db.get(Item, r.km)
        if it is None:
            continue
        ev_id = f"{r.sale_id}:{r.srid}"[:128]
        payload = {"date": r.rdate, "warehouse": r.warehouse,
                   "sale_id": r.sale_id, "order": r.order_doc}
        if it.state == "PENDING_WITHDRAW" or (it.state == "WITHDRAWN"
                                              and it.withdrawn_by == "us"):
            _, created = apply_event(db, source="wb_sales", source_event_id=ev_id,
                                     kind="client_return", km=r.km, srid=r.srid,
                                     payload=payload)
            if created:
                stats["applied"] += 1
        else:
            payload["observed_only"] = True
            if log_action(db, source="wb_sales", source_event_id=ev_id,
                          kind="client_return", km=r.km, srid=r.srid,
                          payload=payload):
                stats["observed"] += 1
        r.applied_at = _now()
    db.commit()
    return stats
