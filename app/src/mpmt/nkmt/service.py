"""Сервис импорта выгрузки НК: parse → defaults → validate → upsert карточек.

Семантика пакета: повторный артикул ВНУТРИ файла — ошибка строки (первое
вхождение выигрывает, дубль идёт только в stats — article в cards UNIQUE);
артикул из прошлых батчей обновляется на месте: контент заново из выгрузки,
good_id/непустой gtin сохраняются (пустой gtin карточки дозаполняется
валидным gtin строки), карточка переезжает в новый батч; gtin, занятый
другой карточкой (другой артикул, включая уже записанные в этом батче), —
ошибка строки «gtin занят», отклонённый gtin в карточку не записывается.
"""
import json

from mpmt.nkmt.dicts import get_defaults
from mpmt.nkmt.models import Batch, Card
from mpmt.nkmt.parse import apply_defaults, parse_xlsx
from mpmt.nkmt.validate import GTIN_RE, validate_rows

FEED_CHUNK = 500  # /nk/feed: не более 500 карточек за запрос


def import_batch(db, filename: str, data: bytes, client, token) -> int:
    """Выгрузка xlsx → батч + карточки; возвращает batch_id."""
    rows = apply_defaults(parse_xlsx(data), get_defaults(db))
    validated = validate_rows(db, client, token, rows)
    batch = Batch(source_filename=filename)
    db.add(batch)
    db.flush()  # id нужен карточкам для FK
    stats = {"ok": 0, "error": 0}
    seen: set[str] = set()
    for v in validated:
        article = v["article"]
        if article in seen:
            stats["error"] += 1  # дубль артикула в файле: карточка первого вхождения уже есть
            continue
        seen.add(article)
        status, error = ("ok", "") if v["ok"] else ("error", v["error"])
        gtin = v["gtin"]
        if gtin and not GTIN_RE.fullmatch(gtin):
            gtin = ""  # битый формат gtin не храним (ошибка уже в error строки)
        if gtin:
            clash = db.query(Card).filter(Card.gtin == gtin,
                                          Card.article != article).first()
            if clash:
                status = "error"
                error = f"{error}; gtin занят" if error else "gtin занят"
                gtin = ""  # отклонённый gtin не сохраняем
        card = db.query(Card).filter_by(article=article).first()
        if card is None:
            card = Card(article=article, gtin=gtin, batch_id=batch.id)
            db.add(card)
        elif gtin and not card.gtin:
            card.gtin = gtin  # дозаполняем только пустой gtin; непустой сохраняем
        card.batch_id = batch.id
        card.tnved, card.name, card.cat_id = v["tnved"], v["name"], v["cat_id"]
        card.attributes = v["attributes"]
        card.status, card.error_text = status, error
        stats["ok" if status == "ok" else "error"] += 1
    batch.status = "new" if stats["error"] == 0 else "partial"
    batch.stats = stats
    db.commit()
    return batch.id


def _feed_entry(card: Card) -> dict:
    """Карточка → entry /nk/feed.

    moderation — ПОЛЕ ENTRY (дамп trueapi: таблица «Параметры тела запроса»
    метода /nk/feed между brand и set_gtins; в JSON-примерах внутри каждого
    элемента массива): 1 — сразу на модерацию, без него НК оставляет черновик.
    Флаттенинг значений: dict остаётся dict, КРОМЕ 23557 → "номер:::дата";
    список 13836 → по записи на элемент; бренд 2504 уезжает в entry.brand.
    """
    attrs = card.attributes or {}
    good_attrs = []
    for k, v in attrs.items():
        if k == "2504":
            continue
        if k == "13836" and isinstance(v, list):
            good_attrs.extend({"attr_id": 13836, "attr_value": item} for item in v)
        elif k == "23557" and isinstance(v, dict):
            good_attrs.append(
                {"attr_id": 23557, "attr_value": f"{v.get('number', '')}:::{v.get('date', '')}"})
        else:
            good_attrs.append({"attr_id": int(k), "attr_value": v})
    return {"gtin": card.gtin, "good_name": card.name, "tnved": card.tnved,
            "brand": attrs["2504"], "categories": [int(card.cat_id)],
            "moderation": 1, "good_attrs": good_attrs}


def feed_batch(db, batch_id: int, client, token) -> dict:
    """Генерация недостающих GTIN → подача фида; ok-карточки → fed, батч → moderation.

    Батч должен существовать и быть new|partial с хотя бы одной ok-карточкой
    (иначе ValueError → 409 в REST). Если generate-gtins вернул меньше drafts,
    чем нужно (месячный лимит), — RuntimeError; лимит сохраняется в stats.
    """
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise ValueError(f"batch {batch_id} not found")
    if batch.status not in ("new", "partial"):
        raise ValueError(f"batch status '{batch.status}' is not feedable")
    cards = db.query(Card).filter(Card.batch_id == batch_id, Card.status == "ok").all()
    if not cards:
        raise ValueError("batch has no ok-cards to feed")

    stats = dict(batch.stats or {})
    need = [c for c in cards if not c.gtin]
    if need:
        resp = client.generate_gtins(token, len(need)) or {}
        stats["gtin_limit"] = resp.get("monthly-limit", resp.get("monthlyLimit"))
        drafts = resp.get("drafts") or []
        if len(drafts) < len(need):
            batch.stats = stats
            db.commit()  # лимит сохраняем, батч остаётся кормимым после разбора
            raise RuntimeError(
                f"generate-gtins monthly limit: got {len(drafts)} of {len(need)} gtins")
        for card, draft in zip(need, drafts):
            card.gtin = draft["gtin"]

    entries = [_feed_entry(c) for c in cards]
    feed_ids = []
    for i in range(0, len(entries), FEED_CHUNK):
        feed_ids.append(client.feed(token, entries[i:i + FEED_CHUNK])["feed_id"])
    for card in cards:
        card.status = "fed"
    batch.feed_id = str(feed_ids[-1])
    stats["feed_ids"] = feed_ids
    batch.stats = stats
    batch.status = "moderation"
    db.commit()
    return {"feed_id": feed_ids[-1], "feed_ids": feed_ids}


def _rejected_error(raw: dict) -> str:
    """Позиционная ошибка отклонённого фида — компактная сериализация среза.

    Дампы trueapi нестабильны (errors/goodErrors, словарь или список) — берём
    что найдётся, сериализуем json.dumps (без гарантий структуры).
    """
    for key in ("errors", "goodErrors"):
        payload = raw.get(key)
        if payload:
            try:
                return json.dumps(payload, ensure_ascii=False)[:500]
            except (TypeError, ValueError):
                return str(payload)[:500]
    return "фид отклонён"


KNOWN_FEED_STATUSES = ("Received", "Processing", "Moderated", "Signed", "Rejected")


def refresh_batch(db, batch_id: int, client, token) -> dict:
    """Статус фида → статусы карточек/батча (опрос /nk/feed-status).

    Применим к батчам moderation|signing с feed_id (иначе ValueError → 409).
    Опрашиваются ВСЕ чанки фида (stats.feed_ids; feed_id — только последний
    чанк). Агрегат: любой Rejected → карточки (кроме error*) → errors с
    текстом ошибки первого отклонённого чанка, батч → error; все Signed →
    published; все Moderated → notsigned + батч → signing; микс с
    Received/Processing — в полёте, ничего не меняем (ярлык «Processing»).
    Незнакомый статус любого чанка — RuntimeError. Возвращает агрегатный
    статус фида и статус батча.
    """
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise ValueError(f"batch {batch_id} not found")
    if batch.status not in ("moderation", "signing") or not batch.feed_id:
        raise ValueError(f"batch status '{batch.status}' is not refreshable")

    ids = (batch.stats or {}).get("feed_ids") or [batch.feed_id]
    polls = []
    for fid in ids:
        raw = client.feed_status(token, fid) or {}
        st = raw.get("status", "")
        if st not in KNOWN_FEED_STATUSES:
            raise RuntimeError(f"unexpected feed status '{st}'")
        polls.append((st, raw))

    statuses = [st for st, _ in polls]
    cards = db.query(Card).filter(
        Card.batch_id == batch_id, Card.status.notin_(
            ("error", "errors", "error_sign"))).all()
    if "Rejected" in statuses:  # любой отклонённый чанк топит весь батч
        st, raw = polls[statuses.index("Rejected")]
        text = _rejected_error(raw)
        for card in cards:
            card.status = "errors"
            card.error_text = text
        batch.status = "error"
    elif all(s == "Signed" for s in statuses):
        st = "Signed"
        for card in cards:
            card.status = "published"
        batch.status = "published"
    elif all(s == "Moderated" for s in statuses):
        st = "Moderated"
        for card in cards:
            card.status = "notsigned"
        batch.status = "signing"
    else:  # Received/Processing или их микс с готовыми чанками — в полёте
        st = statuses[0] if len(set(statuses)) == 1 else "Processing"
        return {"feed_status": st, "batch_status": batch.status}  # ничего не меняем
    db.commit()
    return {"feed_status": st, "batch_status": batch.status}
