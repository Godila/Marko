"""Даты-вехи строк журнала для списка консоли: выкуп и заказ WB.

Read-model поверх journal.events и wb.orders: два bulk-запроса на страницу,
без N+1; item_row остаётся чистой сериализацией — контракт /v1/wb/lookup и
/v1/trace не меняется. Другой маркетплейс — своя пара вех здесь (паттерн
SYSTEMS в trace.py).
"""
from sqlalchemy.orm import Session

from marko.connector_wb.models import WbOrder
from marko.connector_wb.registry import order_doc
from marko.journal.models import Event


def sale_dates(db: Session, kms: list[str]) -> dict[str, tuple[str, str]]:
    """КМ → (fiscal_dt, srid) последней датированной продажи (kind='sale').
    NULL-даты исключены фильтром: иначе пустая payload победила бы в DESC."""
    if not kms:
        return {}
    fd = Event.payload.op("->>")("fiscal_dt")
    rows = (db.query(Event.km, Event.srid, fd)
            .distinct(Event.km)
            .filter(Event.kind == "sale", Event.km.in_(kms), fd.isnot(None))
            .order_by(Event.km, fd.desc(), Event.id.desc())
            .all())
    return {km: (dt, srid) for km, srid, dt in rows}


def order_dates(db: Session, docs: set[str]) -> dict[str, tuple[str, str]]:
    """order_doc → (order_created_at, delivery_type) из персистентного
    реестра wb.orders; контур ('fbs'/'fbo') — точный флаг заказа, где реестр
    его видел (снапшот слеп назад: выкупленный уходит из него на 1–3 дн)."""
    if not docs:
        return {}
    return {doc: (created, dt or "") for doc, created, dt in
            db.query(WbOrder.order_doc, WbOrder.order_created_at,
                     WbOrder.delivery_type)
            .filter(WbOrder.order_doc.in_(docs)).all()}


def doc_refs(db: Session, kms: list[str]) -> dict[str, dict]:
    """КМ → последний документ ЧЗ с этим кодом {id, status}: этап «выведен»
    (черновик/подан/принят) и «возвращён» — UI различает намерение и факт
    (инцидент 23.09: плашки «выведен»+ЧЗ «в обороте» противоречили)."""
    if not kms:
        return {}
    wanted = set(kms)
    out: dict[str, dict] = {}
    from marko.mt.models import MtDoc
    for doc in (db.query(MtDoc)
                .filter(MtDoc.type.in_(("LK_RECEIPT", "LP_RETURN")))
                .order_by(MtDoc.id).all()):     # больший id побеждает
        products = (doc.payload.get("products", []) if doc.type == "LK_RECEIPT"
                    else doc.payload.get("products_list", []))
        key = "cis" if doc.type == "LK_RECEIPT" else "ki"
        for p in products:
            km = p.get(key) if isinstance(p, dict) else None
            if km in wanted:
                out[km] = {"id": doc.id, "status": doc.status}
    return out


def enrich(db: Session, rows: list[dict]) -> list[dict]:
    """Строки item_row + sale_dt / order_dt / delivery_type ('' = неизвестно)
    + doc_ref (этап документа вывода/возврата).

    Ключ заказа — srid последней продажи (дата заказа согласована с датой
    выкупа, а не с текстом соседней колонки «Последний сигнал»); для строк
    без продаж — srid последнего события, если он есть.
    """
    if not rows:
        return rows
    sales = sale_dates(db, [r["km"] for r in rows])
    doc_of: dict[str, str] = {}
    for r in rows:
        srid = sales[r["km"]][1] if r["km"] in sales \
            else (r.get("last_event") or {}).get("srid") or ""
        doc_of[r["km"]] = order_doc(srid)
    ords = order_dates(db, {d for d in doc_of.values() if d})
    refs = doc_refs(db, [r["km"] for r in rows])
    for r in rows:
        r["sale_dt"] = sales[r["km"]][0] if r["km"] in sales else ""
        created, delivery = ords.get(doc_of[r["km"]], ("", ""))
        r["order_dt"] = created
        r["delivery_type"] = delivery
        r["doc_ref"] = refs.get(r["km"])
    return rows
