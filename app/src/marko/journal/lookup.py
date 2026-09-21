"""Консольный lookup «ID заказа WB → КМ журнала»: нормализация rid/srid через
order_doc() + поиск событий журнала по документу заказа. Эксайз-srid хранится
с хвостом '.n.m', реестр wb.orders — без: совпадение по документу, а не по
полной строке (хвосты расходятся между системами, инцидент 09.2026)."""
from sqlalchemy import or_
from sqlalchemy.orm import Session

from marko.connector_wb.models import WbOrder
from marko.connector_wb.registry import order_doc, order_row
from marko.journal import item_row
from marko.journal.models import Event, Item


def _esc(s: str) -> str:        # rid — пользовательский ввод: гасим LIKE-метасимволы
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def order_lookup(db: Session, rid: str) -> dict:
    """→ {rid, order_doc, order, items, status}; status: found|fbw|lag|unknown."""
    doc = order_doc(rid)
    if len(doc) < 3:
        raise ValueError("пустой или слишком короткий номер заказа")
    if "." in doc:    # документ с префиксом: точное совпадение, его позиции '.n.m'
        body = doc.split(".", 1)[1]
        sf = [Event.srid == doc,
              Event.srid.like(_esc(doc) + ".%", escape="\\")]
        if len(body) >= 32:   # …и легаси-голые srid без префикса; для коротких
            sf.append(        # тел контейнмент даст ложных соседей ('12345'→'123456')
                Event.srid.like("%" + _esc(body) + "%", escape="\\"))
        sf = or_(*sf)
        order = db.get(WbOrder, doc)
    else:             # голый uuid: префикс неизвестен — контейнмент
        sf = Event.srid.like("%" + _esc(doc) + "%", escape="\\")
        order = (db.query(WbOrder)
                 .filter(WbOrder.order_doc.like("%" + _esc(doc) + "%", escape="\\"))
                 .first())
    rows = db.query(Event.km, Event.kind).filter(sf).all()
    kms = {km for km, _ in rows}
    items = ([item_row(it) for it in
              db.query(Item).filter(Item.km.in_(kms))
              .order_by(Item.updated_at.desc()).all()] if kms else [])
    status = ("found" if items else
              "fbw" if "skip_fbw" in {k for _, k in rows} else
              "lag" if order is not None else "unknown")
    return {"rid": rid, "order_doc": doc, "order": order_row(order) if order else None,
            "items": items, "status": status}
