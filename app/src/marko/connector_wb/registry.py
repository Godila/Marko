"""Реестр заказов WB (/api/v3/orders → wb.orders): документ → схема доставки.

Эксайз srid и orders rid живут в одном пространстве документов «префикс.uuid»,
но хвостовые счётчики позиции '.n.m' расходятся между системами (инцидент
09.2026: 0/131 полных совпадений при 3 совпадениях по документу). Классификация
FBS/FBW опирается на order_doc без хвоста и на ПЕРСИСТЕНТНЫЙ реестр —
выкупленный заказ исчезает из снапшота на 1–3 дня раньше, чем приезжает
эксайз-строка (128/131 документов уже вне снапшота на момент инцидента).
"""
import re

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from marko.connector_wb.models import WbOrder

DOC_TAIL = re.compile(r"\.\d+\.\d+$")

# ключ xact-адвизорного лока: poll-слот и почасовой прогрев могут совпасть во
# времени и дать lock-order inversion на пересекающихся ключах wb.orders
_UPSERT_LOCK = 912001


def order_doc(rid_or_srid: str) -> str:
    """'eAL.rfa7…3.0' → 'eAL.rfa7…'; голые uuid/числа — как есть; идемпотентно."""
    return DOC_TAIL.sub("", str(rid_or_srid or "").strip())


def order_row(o: WbOrder) -> dict:
    """Сериализация строки реестра для консольных API (lookup, трассировка)."""
    return {"order_doc": o.order_doc, "order_id": o.order_id,
            "delivery_type": o.delivery_type, "nm_id": o.nm_id,
            "order_created_at": o.order_created_at,
            "first_seen": o.first_seen, "last_seen": o.last_seen}


def _int_or_none(v) -> int | None:
    try:
        return int(v) if v else None
    except (TypeError, ValueError):
        return None


def upsert_orders(db: Session, order_rows: list[dict], chunk: int = 10_000) -> int:
    """Апсерт снапшота заказов; позиции одного заказа (.1.0/.2.0) схлопываются
    в одну строку — иначе PG поднимет "ON CONFLICT cannot affect row a second time"."""
    by_doc: dict[str, dict] = {}
    for o in order_rows:
        rid = str(o.get("rid") or "").strip()
        if not rid:
            continue
        doc = order_doc(rid)
        prev = by_doc.get(doc)
        row = {
            "order_doc": doc,
            "order_id": _int_or_none(o.get("id")),
            "delivery_type": str(o.get("deliveryType") or "").strip().lower()[:8],
            "nm_id": _int_or_none(o.get("nmId")),
            "order_created_at": str(o.get("createdAt") or "")[:32],
        }
        if prev and row["order_id"] is None:
            row["order_id"] = prev["order_id"]     # дедуп позиций id не теряет
        by_doc[doc] = row
    vals = list(by_doc.values())
    if not vals:
        db.commit()
        return 0
    db.execute(select(func.pg_advisory_xact_lock(_UPSERT_LOCK)))
    for i in range(0, len(vals), chunk):
        stmt = pg_insert(WbOrder).values(vals[i:i + chunk])
        stmt = stmt.on_conflict_do_update(
            index_elements=[WbOrder.order_doc],
            set_={"order_id": func.coalesce(stmt.excluded.order_id, WbOrder.order_id),
                  "delivery_type": stmt.excluded.delivery_type,
                  "nm_id": stmt.excluded.nm_id,
                  "order_created_at": stmt.excluded.order_created_at,
                  "last_seen": func.now()})
        db.execute(stmt)
    db.commit()
    return len(vals)


def registry_docs(db: Session) -> set[str]:
    return {r[0] for r in db.query(WbOrder.order_doc).all()}


def non_fbs_docs(db: Session) -> set[str]:
    """Явно не-FBS документы. Пустой тип (поле пропало из API) сюда НЕ попадает —
    вырождение реестра должно давать ingest, а не тихий skip."""
    return {r[0] for r in db.query(WbOrder.order_doc)
            .filter(WbOrder.delivery_type.notin_(("fbs", ""))).all()}
