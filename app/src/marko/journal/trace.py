"""Трассировка КМ: жизненный цикл одного кода по локальным данным платформы.

Read-only агрегатор над журналом, документами ЧЗ, реестром WB и каталогом НК;
живые проверки (ЧЗ cises/info, WB orders/meta) — отдельные кнопки консоли,
здесь только сшивка уже накопленного. Расширение на другой маркетплейс —
своя строка в SYSTEMS + события его источника в journal.events.
"""
import re
import time
from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from marko.connector_wb.models import WbOrder, WbReturn
from marko.connector_wb.registry import order_doc, order_row
from marko.journal import item_row
from marko.journal.lookup import _esc
from marko.journal.models import Event, Item
from marko.mt.models import MtDoc
from marko.nkmt.models import Card
from marko.platform.models import PlatformKV

GS = "\x1d"            # GS1 group separator (printable-замена — '!')
# только «настоящие» пробелы: str.split()/re \s съедают и сам GS (\x1d —
# юникодный whitespace в Python), ломая разрез криптохвоста
_SPACES_RE = re.compile(r"[ \t\r\n\u00a0\u202f]+")
_MIN_KM, _MAX_KM = 20, 31   # 01+GTIN14+21+серийник; прод = 31, тестовые короче

# система трассировки ← источник события; новый маркетплейс = строка здесь
SYSTEMS = {"marko": "МАРКО", "cz": "Честный знак", "wb": "Wildberries"}


class TraceError(ValueError):
    """Невалидный ввод оператора → роут отдаёт 422 с человеческим текстом."""


def _system(source: str) -> str:
    if source.startswith("wb"):
        return "wb"
    return "cz" if source in ("cz", "guard") else "marko"


def _ok_km(head: str) -> bool:
    return (_MIN_KM <= len(head) <= _MAX_KM and head.startswith("01")
            and head[16:18] == "21" and head[2:16].isdigit())


def normalize_km(raw: str) -> str:
    """Любая форма КИЗ → канонический КМ журнала: без пробелов, криптохвост
    (AI 91/92 после GS/'!') отброшен; слитный хвост без разделителя
    обрезается до 31 (серийник лёгпрома = 13 символов). Регистр НЕ
    меняется: серийник ЧЗ регистрочувствителен, журнал хранит как есть.
    '!' в серийнике допустим — '!'-разрез валиден только когда хвост
    начинается с AI крипточасти (91/92), иначе режем длиной."""
    s = _SPACES_RE.sub("", raw or "")
    if not s:
        raise TraceError("пустой ввод: вставьте КИЗ или КМ целиком")
    candidates = []
    if GS in s:
        candidates.append(s.split(GS, 1)[0])
    # '!'-разрез: валиден у ЛЮБОЙ позиции '!' с AI крипточасти следом —
    # сам '!' может легально стоять внутри серийника
    for i, ch in enumerate(s):
        if ch == "!" and s[i + 1:i + 3] in ("91", "92"):
            candidates.append(s[:i])
            break
    candidates.append(s[:_MAX_KM])      # слитный криптохвост / короткий КМ
    for head in candidates:
        if _ok_km(head):
            return head
    raise TraceError("не похоже на код маркировки: ожидается 01<GTIN>21<серийник>"
                     " (возможно, с криптохвостом после GS-разделителя)")


# (source, kind) → заголовок события; fallback — kind как есть
TITLES = {
    ("wb_excise", "sale"): "Продажа (чек ККТ WB)",
    ("wb_excise", "return"): "Возврат покупателя (чек ККТ WB)",
    ("wb_sales", "client_return"): "Возврат покупателя (финансовый след WB)",
    ("wb_excise", "skip_fbw"): "Строка вне контура FBS (FBW)",
    ("emitter", "withdraw"): "Вывод из оборота — документ LK_RECEIPT",
    ("emitter", "return_apply"): "Возврат в оборот — документ LP_RETURN",
    ("guard", "withdraw"): "Гвард: ЧЗ отказал — код уже выбыл (вывел WB)",
    ("cz", "withdraw"): "Переведён «вывел WB» по данным ЧЗ",
    ("manual", "resolve"): "Ручной разбор аномалии",
    ("manual", "revert"): "Откат после удаления черновика",
}


def _ts(ev: Event) -> str:
    """Момент события: дата чека для WB-строк (fiscal_dt), дата финансового
    возврата для sales R (иначе бэкфилл встанет днём ингеста), иначе момент
    записи в журнал — у наших действий «когда» и есть момент записи."""
    if ev.source == "wb_excise" and ev.payload.get("fiscal_dt"):
        return str(ev.payload["fiscal_dt"])
    if ev.source == "wb_sales" and ev.payload.get("date"):
        return str(ev.payload["date"])
    return ev.created_at.isoformat()


def _price(p) -> str:
    try:
        return f"{int(p):,}".replace(",", " ") + " ₽"
    except (TypeError, ValueError):
        return ""


def _detail(ev: Event) -> str:
    p = ev.payload or {}
    parts: list[str] = []
    if ev.source == "wb_excise":
        if p.get("fiscal_doc_number"):
            parts.append(f"чек {p['fiscal_doc_number']}")
        pr = _price(p.get("price"))
        if pr:
            parts.append(pr)
        if p.get("nm_id"):
            parts.append(f"nm {p['nm_id']}")
    elif ev.source in ("emitter", "guard"):
        if p.get("doc_id"):
            parts.append(f"документ №{p['doc_id']}")
        if p.get("return_type"):
            parts.append(str(p["return_type"]))
        if ev.source == "guard" and p.get("matched"):
            parts.append(f"отказ: {p['matched']}")
    elif ev.source == "cz":
        if p.get("withdrawReason"):
            parts.append(f"причина: {p['withdrawReason']}")
    elif ev.kind == "resolve" and ev.source == "manual":
        if p.get("from") or p.get("to"):
            parts.append(f"{p.get('from', '?')} → {p.get('to', '?')}")
        elif p.get("from_state"):       # ручная пометка источника вывода
            parts.append(f"{p['from_state']} → "
                         f"{'WITHDRAWN · вывел WB' if p.get('by') == 'wb' else 'пометка us'}")
        if p.get("note"):
            parts.append(str(p["note"]))
    elif ev.kind == "revert":
        parts.append(f"документ №{p.get('doc_id', '?')}: "
                     f"{p.get('from', '?')} → {p.get('to', '?')}")
    if ev.srid:
        parts.append(f"заказ {order_doc(ev.srid)}")
    return " · ".join(parts)


def _timeline(events: list[Event]) -> list[dict]:
    rows = [{"id": ev.id, "system": _system(ev.source),
             "system_label": SYSTEMS.get(_system(ev.source), ev.source),
             "ts": _ts(ev), "observed": ev.created_at.isoformat(),
             "kind": ev.kind,
             "title": TITLES.get((ev.source, ev.kind), ev.kind),
             "detail": _detail(ev), "srid": ev.srid,
             "payload": ev.payload} for ev in events]
    rows.sort(key=lambda r: (r["ts"], r["id"]))     # хронология «путь кода»
    return rows


def _docs(db: Session, km: str) -> list[dict]:
    """Наши документы с этим кодом в позициях (скан по образцу active_claims)."""
    out = []
    for d in db.query(MtDoc).order_by(MtDoc.id).all():
        products = (d.payload.get("products", []) if d.type == "LK_RECEIPT"
                    else d.payload.get("products_list", []))
        key = "cis" if d.type == "LK_RECEIPT" else "ki"
        if any(p.get(key) == km for p in products):
            out.append({"id": d.id, "type": d.type, "status": d.status,
                        "external_id": d.external_id, "created_at": d.created_at})
    return out


def _orders(db: Session, docs: set[str]) -> list[dict]:
    if not docs:
        return []
    return [order_row(o) for o in
            db.query(WbOrder).filter(WbOrder.order_doc.in_(docs))
            .order_by(WbOrder.order_doc).all()]


def _returns(db: Session, srids: set[str], docs: set[str]) -> list[dict]:
    """Возвраты на ПВЗ в пространстве документов этого кода: точные srid
    событий + doc-префикс (хвосты '.n.m' расходятся, см. lookup)."""
    sf = _wbret_sf(srids, docs)
    if not sf:
        return []
    return [_return_row(r) for r in
            db.query(WbReturn).filter(or_(*sf)).order_by(WbReturn.srid).all()]


def _wbret_sf(srids: set[str], docs: set[str]) -> list:
    """Фильтр строк WbReturn: точные srid + документы с хвостами '.n.m'
    (хвосты расходятся между системами, инцидент 09.2026). Единая точка для
    трассировки и справочника идентификаторов — дрейф трёх копий этого
    фильтра стоил бы повторения инцидента."""
    return ([WbReturn.srid.in_(srids)] if srids else []) + \
        [WbReturn.srid.like(_esc(d) + ".%", escape="\\") for d in docs]


def _return_row(r: WbReturn) -> dict:
    """Сериализация строки возврата на ПВЗ — читают трассировка и справочник
    идентификаторов одной таблицей UI; дрейф полей ломал бы её молча."""
    p = r.payload or {}
    return {"srid": r.srid, "order_id": r.order_id, "status": r.status,
            "reason": p.get("reason"), "expired_dt": r.expired_dt,
            "is_active": bool(p.get("isStatusActive"))}


def _card(db: Session, gtin: str) -> dict | None:
    c = db.query(Card).filter(Card.gtin == gtin).first() if gtin else None
    if c is None:
        return None
    return {"article": c.article, "name": c.name, "status": c.status}


def _supplies(db: Session, orders: list[dict]) -> list[dict]:
    """Поставки заказов этого кода из кэша воркера (kv wb_supplies): даты
    закрытия/приёмки на складе WB. Читается без сети; пустой кэш — пустая
    секция (первый часовой прогрев наполнит)."""
    ids = {o["supply_id"] for o in orders if o.get("supply_id")}
    if not ids:
        return []
    kv = db.get(PlatformKV, "wb_supplies")
    by_id = (kv.value.get("by_id") or {}) if kv else {}
    return [by_id[i] for i in sorted(ids) if i in by_id]


def trace(db: Session, km_input: str) -> dict:
    km = normalize_km(km_input)
    gtin = km[2:16]
    item = db.get(Item, km)
    events = db.query(Event).filter(Event.km == km).order_by(Event.id).all()
    timeline = _timeline(events)
    srids = {ev.srid for ev in events if ev.srid}
    docs = {order_doc(s) for s in srids}
    orders = _orders(db, docs)
    returns = _returns(db, srids, docs)
    mt_docs = _docs(db, km)
    supplies = _supplies(db, orders)
    return {"km": km, "input": km_input, "gtin": gtin,
            "found": item is not None,
            "item": item_row(item) if item else None,
            "card": _card(db, gtin),
            "timeline": timeline, "docs": mt_docs,
            "orders": orders, "returns": returns, "supplies": supplies,
            "counts": {"events": len(timeline)}}


def km_wb_order_ids(db: Session, km_input: str) -> list[int]:
    """Числовые ID сборочных заданий (WB orders/meta принимает только их) по
    событиям кода: реестр wb.orders.order_id + возвратные строки WbReturn."""
    km = normalize_km(km_input)
    docs = {order_doc(s) for (s,) in
            db.query(Event.srid).filter(Event.km == km, Event.srid != "").all()}
    if not docs:
        return []
    ids = {oid for (oid,) in db.query(WbOrder.order_id)
           .filter(WbOrder.order_doc.in_(docs),
                   WbOrder.order_id.isnot(None)).all()}
    ret_sf = or_(*_wbret_sf(docs, docs))
    ids.update(oid for (oid,) in db.query(WbReturn.order_id)
               .filter(ret_sf, WbReturn.order_id > 0).all())
    return sorted(i for i in ids if i)


FEED_TTL = 3 * 3600 + 60   # окно чуть ДЛИННЕЕ квоты WB 1/3ч: кэш не должен
# истечь раньше, чем освободится квота, иначе клик в зазоре ловит 429


def feed_from_cache(db: Session, docs: set[str]) -> dict | None:
    """Телеметрия ленты заказов из кэша кабинета (kv trace_wb_feed, без
    сети). Читают /v1/trace (все документы кода) и справочник идентификаторов
    (документ заказа); пустой или протухший кэш — None."""
    kv = db.get(PlatformKV, "trace_wb_feed")
    if not kv or time.time() - kv.value.get("fetched_at", 0) >= FEED_TTL:
        return None
    by_doc = kv.value.get("by_doc") or {}
    rows = [by_doc[d] for d in sorted(docs) if d in by_doc]
    if not rows:
        return None
    return {"fetched_at": datetime.fromtimestamp(
        kv.value["fetched_at"], tz=timezone.utc).isoformat(), "orders": rows}
