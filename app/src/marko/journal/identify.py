"""Универсальный справочник идентификаторов WB: любой ключ → всё известное.

Классификация синтаксисом (КМ → rid → GTIN → число → поставка), числовая
неоднозначность (ID сборочного задания vs nmId) разрешается данными реестра.
Живой слой — закреплённые sgtin по числовым ID (POST orders/meta, квота
300/мин без суточных гейтов): для НЕпроданных заказов («повторная поставка»)
это единственный источник КиЗ — локально связка КМ↔заказ рождается только
эксайз-строкой продажи (ingest), которой у непроданных нет. Ответ кэшируется
в kv на 10 минут: закрепление меняется на этапах сборки/продажи, длинный TTL
врал бы словом «закреплён».
"""
import re
import time

from sqlalchemy import func, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from marko.connector_wb.client import WBClient, WbHttpError, WbLimitError, load_wb_token
from marko.connector_wb.models import WbClientReturn, WbOrder, WbReturn
from marko.connector_wb.registry import order_doc, order_row
from marko.journal import item_row
from marko.journal.lookup import order_lookup
from marko.journal.milestones import enrich
from marko.journal.models import Event, Item
from marko.journal.trace import (TraceError, _card, _return_row, _returns,
                                 _supplies, _wbret_sf, feed_from_cache,
                                 normalize_km)
from marko.platform.models import PlatformKV
from marko.settings import settings

# зеркало ui ORDER_ID_RE: тела rid двух видов (32–33 alnum / uuid), префикс
# любой (eAc=заказ, eAW=продажа, eBQ, служебные), хвост '.n.m' опционален
_RID_BODY = r"(?:[0-9a-z]{32,33}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})"
RID_RE = re.compile(rf"^(?:[0-9a-z_]{{1,16}}\.)?{_RID_BODY}(?:\.\d+\.\d+)?$", re.I)
GTIN_RE = re.compile(r"^\d{13,14}$")        # 13 = EAN → zfill14; nmId/ID задания короче
SUPPLY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/_-]{2,31}$")

KV_META, META_TTL = "wb_meta_cache", 600    # закрепления sgtin: 10 минут


class IdentifyError(ValueError):
    """Невалидный ввод оператора → роут отдаёт 422 с человеческим текстом."""


def classify(raw: str) -> tuple[str, str]:
    """Чистая классификация синтаксиса → (type, value). КМ проверяется первым:
    ни один rid не начинается с '01' и не содержит GS/'!'-криптогруппы
    (зеркало ui isOrderId). Регистр НЕ меняется — серийник КМ значим."""
    s = (raw or "").strip()
    if len(s) < 3:
        raise IdentifyError("пустой или слишком короткий идентификатор")
    try:
        return "km", normalize_km(s)
    except TraceError:
        pass
    if RID_RE.match(s):
        return "order", order_doc(s)
    if GTIN_RE.match(s):
        return "gtin", s.zfill(14)
    if s.isascii() and s.isdigit():   # isdigit() пропускает не-Nd («²») — int() бы упал
        return "num", s
    if "-" in s and SUPPLY_RE.match(s):
        return "supply", s
    raise IdentifyError(
        "не похоже ни на один поддерживаемый идентификатор: rid заказа WB "
        "(например eAc.0123…abcd.0.0), числовой ID сборочного задания, КМ/КИЗ, "
        "GTIN (13–14 цифр), nmId или номер поставки (WB-GI-…)")


def identify(db: Session, raw: str, *, live: bool = True, refresh: bool = False) -> dict:
    """Точка входа справочника: classify → агрегатор по типу. live=False —
    только локальные срезы (RO-токен); refresh обходит кэш закреплений."""
    typ, val = classify(raw)
    if typ == "km":
        return _identify_km(db, raw, val)
    if typ == "gtin":
        return _identify_gtin(db, raw, val)
    if typ == "supply":
        return _identify_supply(db, raw, val)
    if typ == "num":
        return _identify_num(db, raw, val, live=live, refresh=refresh)
    return _identify_order(db, raw, val, live=live, refresh=refresh)


def parse_meta(data: dict) -> list[dict]:
    """Ответ orders/meta → [{'id', 'sgtins': [{'sgtin','decision'}]}]:
    только sgtin-закрепления (gtin/imei/uin-ключи справочнику не нужны).
    Единый парсер для /v1/identify и /v1/trace/wb-meta."""
    out = []
    for o in (data.get("orders") or []):
        out.append({"id": o.get("id"),
                    "sgtins": [{"sgtin": m.get("value"), "decision": m.get("decision")}
                               for m in (o.get("metaDetails") or [])
                               if m.get("key") == "sgtin"]})
    return out


# ---- типы ключей ----

def _identify_km(db: Session, raw: str, km: str) -> dict:
    """КМ — территория Трассировки: подтверждение распознавания + редирект."""
    it = db.get(Item, km)
    return {"type": "km", "key": km, "raw": raw,
            "item": item_row(it) if it else None, "redirect": "trace"}


def _identify_gtin(db: Session, raw: str, gtin: str) -> dict:
    items = _items_by_kms(db, [km for (km,) in
                               db.query(Item.km)
                               .filter(Item.km.like(f"01{gtin}21%"))
                               .order_by(Item.km).limit(200).all()])
    counts = {"items": db.query(func.count(Item.km))
              .filter(Item.km.like(f"01{gtin}21%")).scalar() or 0}
    body = {"type": "gtin", "key": gtin, "raw": raw, "card": _card(db, gtin),
            "items": items, "counts": counts}
    if not items and body["card"] is None:
        body["note"] = "GTIN не найден ни в каталоге НК, ни в журнале КМ — проверьте номер"
    return body


def _identify_supply(db: Session, raw: str, supply_id: str) -> dict:
    orders = [order_row(o) for o in
              db.query(WbOrder).filter(WbOrder.supply_id == supply_id)
              .order_by(WbOrder.order_doc).limit(100).all()]
    kv = db.get(PlatformKV, "wb_supplies")
    supply = ((kv.value.get("by_id") or {}) if kv else {}).get(supply_id)
    body = {"type": "supply", "key": supply_id, "raw": raw,
            "supply": supply, "orders": orders}
    if not orders and supply is None:
        body["note"] = ("поставка не найдена ни в реестре заказов, ни в кэше "
                        "поставок (прогревается раз в час) — проверьте номер")
    return body


def _identify_num(db: Session, raw: str, val: str, *, live: bool, refresh: bool) -> dict:
    """Число: приоритет ID сборочного задания (боль юзера — «какой КиЗ на
    заказе»), nmId — фолбэк; ни то ни другое не нашлось — 422."""
    n = int(val)
    rows = (db.query(WbOrder).filter(WbOrder.order_id == n)
            .order_by(WbOrder.order_doc).all())
    if rows:
        body = _identify_order(db, raw, rows[0].order_doc, live=live, refresh=refresh)
        body["note"] = f"введён числовой ID сборочного задания — документ заказа {rows[0].order_doc}"
        if len(rows) > 1:
            body["note"] += f" (этот ID встречается в {len(rows)} документах, показан первый)"
        elif db.query(WbOrder.order_doc).filter(WbOrder.nm_id == n).first() is not None:
            body["note"] += "; число также подходит под nmId — если искали артикул, он тоже найдётся этим же запросом"
        return body
    rets = _returns_by_order_id(db, n)
    if rets:
        # реестр заказа не видел (ушёл из снапшота до опроса) — ID знает только
        # возвратная строка; статус '' — не подставляем словарь unknown, его
        # текст «заказа нет вообще» противоречил бы возвращённым данным
        return {"type": "order", "key": val, "raw": raw, "order_ids": [n],
                "order": {"rid": val, "order_doc": val, "order": None,
                          "items": [], "status": ""},
                "returns": rets, "client_returns": [], "supplies": [],
                "note": "документ заказа неизвестен — числовой ID известен только "
                        "из возвратной строки (заказ ушёл из снапшота WB)",
                "live": _live_block(db, [n], live, refresh)}
    items = _items_by_nm(db, n)
    orders = [order_row(o) for o in
              db.query(WbOrder).filter(WbOrder.nm_id == n)
              .order_by(WbOrder.order_created_at.desc()).limit(100).all()]
    if orders or items:
        return {"type": "nm", "key": val, "raw": raw, "orders": orders,
                "items": items,
                "counts": {"orders": db.query(func.count(WbOrder.order_doc))
                           .filter(WbOrder.nm_id == n).scalar() or 0,
                           "items": len(items)}}
    raise IdentifyError("число не найдено ни как ID сборочного задания, ни как "
                        "nmId — проверьте номер")


def _identify_order(db: Session, raw: str, doc: str, *, live: bool, refresh: bool) -> dict:
    res = order_lookup(db, doc)
    res["items"] = enrich(db, res["items"])
    # голое тело rid без префикса lookup находит контейнментом — WB-срезы
    # (возвраты, лента, supply) ключуются по НАСТОЯЩЕМУ документу реестра
    if res["order"] and res["order"].get("order_doc"):
        doc = res["order"]["order_doc"]
    ids: set[int] = set()
    if res["order"] and res["order"].get("order_id"):
        ids.add(res["order"]["order_id"])
    ids.update(_return_ids_by_doc(db, {doc}))
    body = {"type": "order", "key": doc, "raw": raw,
            "order": res, "order_ids": sorted(ids),
            "returns": _returns(db, set(), {doc}),
            "client_returns": _client_returns(db, doc),
            "supplies": _supplies(db, [res["order"]] if res["order"] else [])}
    feed = feed_from_cache(db, {doc})
    if feed:
        body["wb_feed"] = feed
    body["live"] = _live_block(db, sorted(ids), live, refresh)
    return body


# ---- локальные срезы ----

def _items_by_kms(db: Session, kms: list[str]) -> list[dict]:
    if not kms:
        return []
    rows = [item_row(it) for it in
            db.query(Item).filter(Item.km.in_(kms))
            .order_by(Item.updated_at.desc()).all()]
    return enrich(db, rows)


def _items_by_nm(db: Session, nm: int) -> list[dict]:
    """КМ журнала по nm_id событий (эксайз-payload) — «все коды артикула»."""
    nm_ev = Event.payload.op("->>")("nm_id")
    kms = {km for (km,) in db.query(Event.km)
           .filter(nm_ev == str(nm), Event.km != "").distinct(Event.km).all()}
    return _items_by_kms(db, sorted(kms)[:200])


def _return_ids_by_doc(db: Session, docs: set[str]) -> set[int]:
    """Числовые ID из возвратных строк по документу заказа (заказ мог уйти из
    снапшота WB до первого опроса — возвратная строка ID сохраняет)."""
    if not docs:
        return set()
    return {oid for (oid,) in db.query(WbReturn.order_id)
            .filter(or_(*_wbret_sf(set(), docs)),
                    WbReturn.order_id > 0).all()}


def _returns_by_order_id(db: Session, order_id: int) -> list[dict]:
    return [_return_row(r) for r in
            db.query(WbReturn).filter(WbReturn.order_id == order_id)
            .order_by(WbReturn.srid).all()]


def _client_returns(db: Session, doc: str) -> list[dict]:
    """R-строки возвратов покупателей по документу заказа продажи."""
    out = []
    for r in (db.query(WbClientReturn).filter(WbClientReturn.order_doc == doc)
              .order_by(WbClientReturn.srid).limit(50).all()):
        out.append({"srid": r.srid, "sale_id": r.sale_id, "date": r.rdate,
                    "warehouse": r.warehouse, "km": r.km,
                    "applied": r.applied_at is not None})
    return out


# ---- живой слой: закреплённые sgtin ----

def _live_block(db: Session, ids: list[int], live: bool, refresh: bool) -> dict:
    if not ids:
        return {"state": "skipped",
                "note": "числовой ID сборочного задания неизвестен — реестр WB "
                        "ещё не прогрет (заказ мог уйти из снапшота до опроса)"}
    if not live:
        return {"state": "skipped",
                "note": "живые закрепления sgtin требуют скоупа docs:submit"}
    return live_sgtins(db, ids, refresh=refresh)


def live_sgtins(db: Session, order_ids: list[int], *, refresh: bool = False) -> dict:
    """Закреплённые sgtin по числовым ID заданий: кэш kv (10 мин) → живой
    orders/meta по недостающим. Пустой ответ WB кэшируется негативно (пустой
    факт), иначе несуществующие ID дёргали бы сеть при каждом заходе. WB упал
    → fail-soft (state=error, HTTP 200 у роута): локальные срезы справочника
    важнее телеметрии. sgtin нормализуется и сшивается с журналом при каждом
    ответе — в кэше только сырой WB-факт."""
    ids = sorted({int(i) for i in order_ids if i})[:100]
    now = time.time()
    kv = db.get(PlatformKV, KV_META)
    by_id = {k: v for k, v in ((kv.value.get("by_id") or {}) if kv else {}).items()
             if now - v.get("fetched_at", 0) < META_TTL}   # протухшие выпали
    if refresh:
        by_id = {k: v for k, v in by_id.items() if int(k) not in set(ids)}
    fetched, error = False, ""
    missing = [i for i in ids if str(i) not in by_id]
    if missing:
        fetched = True
        try:
            client = WBClient(token=load_wb_token(settings.wb_token_file), db=db)
            fresh = parse_meta(client.orders_meta(missing))
            stamp = time.time()
            for o in fresh:
                if o.get("id") is None:      # мусорная запись не должна попасть
                    continue                 # в by_id — refresh ломался бы int(k)
                by_id[str(o["id"])] = {"fetched_at": stamp, "sgtins": o["sgtins"]}
            for i in missing:               # негативный кэш пустых ответов WB
                by_id.setdefault(str(i), {"fetched_at": stamp, "sgtins": []})
            _meta_cache_put(db, by_id)
        except (WbHttpError, WbLimitError, OSError) as e:
            error = f"WB не ответил: {str(e)[:160]}"
    entries = _enrich_sgtins(db, [(i, by_id[str(i)]) for i in ids if str(i) in by_id])
    if not entries:
        return {"state": "error" if error else "empty", "fetched": fetched,
                "error": error or "WB не вернул закреплений по этому заданию "
                                 "(ID не существует или чужой)"}
    out = {"state": "live" if fetched else "cache", "fetched": fetched,
           "orders": entries}
    if error:                       # часть пришла из кэша, часть не удалась
        out["error"] = error
    return out


def _enrich_sgtins(db: Session, pairs: list[tuple[int, dict]]) -> list[dict]:
    out = []
    for oid, ent in pairs:
        sgtins = []
        for s in ent.get("sgtins") or []:
            km = None
            try:
                km = normalize_km(s.get("sgtin") or "")
            except TraceError:      # чужой формат — показываем сырой sgtin как есть
                pass
            it = db.get(Item, km) if km else None
            sgtins.append({"sgtin": s.get("sgtin"), "km": km,
                           "decision": s.get("decision"),
                           "item": item_row(it) if it else None})
        out.append({"id": oid, "ts": ent.get("fetched_at"), "sgtins": sgtins})
    return out


def meta_decision(db: Session, sgtin: str) -> tuple[str | None, float | None]:
    """(решение WB по точному sgtin, fetched_at) из кэша закреплений — без
    сети, свежесть по META_TTL. Единственный читатель схемы кэша кроме
    live_sgtins — guard печати этикеток (marko.label): дрейф схемы виден
    здесь, а не молча выключает блокировку печати."""
    kv = db.get(PlatformKV, KV_META)
    if kv is None:
        return None, None
    now = time.time()
    for ent in (kv.value.get("by_id") or {}).values():
        ts = ent.get("fetched_at") or 0
        if now - ts > META_TTL:
            continue
        for s in ent.get("sgtins") or []:
            if s.get("sgtin") == sgtin:
                return s.get("decision"), ts
    return None, None


def _meta_cache_put(db: Session, by_id: dict) -> None:
    items = sorted(by_id.items(),               # потолок роста: свежие 500
                   key=lambda kv: kv[1].get("fetched_at", 0), reverse=True)[:500]
    value = {"by_id": dict(items)}
    db.execute(pg_insert(PlatformKV).values(key=KV_META, value=value)
               .on_conflict_do_update(index_elements=[PlatformKV.key],
                                      set_={"value": value}))
    db.commit()
