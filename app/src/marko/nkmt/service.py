"""Сервис импорта выгрузки НК: parse → defaults → validate → upsert карточек.

Семантика пакета: повторный артикул ВНУТРИ файла — ошибка строки (первое
вхождение выигрывает, дубль идёт только в stats — article в cards UNIQUE);
артикул из прошлых батчей обновляется на месте: контент заново из выгрузки,
good_id/непустой gtin сохраняются (пустой gtin карточки дозаполняется
валидным gtin строки), карточка переезжает в новый батч; gtin, занятый
другой карточкой (другой артикул, включая уже записанные в этом батче), —
ошибка строки «gtin занят», отклонённый gtin в карточку не записывается.
"""
import base64
import json

from marko.connector_mt.manager import _sign_via_gateway
from marko.nkmt.client import NkHttpError
from marko.nkmt.dicts import get_defaults
from marko.nkmt.models import Batch, Card
from marko.nkmt.parse import apply_defaults, parse_xlsx
from marko.nkmt.validate import GTIN_RE, validate_rows

FEED_CHUNK = 500  # /nk/feed: не более 500 карточек за запрос
SIGN_CHUNK = 10  # /nk/feed-product-document|sign-pkcs: не более 10 товаров за запрос


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
    Флаттенинг значений: dict {type,value} (35, 13914) → строка attr_value +
    поле attr_value_type (live); 23557 → "номер:::дата"; список 13836 → по
    записи на элемент; бренд 2504 уезжает в entry.brand.
    Пустые значения (None, "", пустой список, value="" у dict) не отправляются
    вовсе — live: «attr_id можно использовать только с attr_value» (producer
    по умолчанию "" не должен попадать в фид как {"attr_id": 2503, ""}).
    """
    attrs = card.attributes or {}
    good_attrs = []
    for k, v in attrs.items():
        if k == "2504":
            continue
        if v is None or v == "" or v == []:
            continue
        if k == "13836" and isinstance(v, list):
            good_attrs.extend({"attr_id": 13836, "attr_value": item}
                              for item in v if item not in (None, ""))
        elif k == "23557" and isinstance(v, dict):
            if not v.get("number"):
                continue
            good_attrs.append(
                {"attr_id": 23557, "attr_value": f"{v.get('number', '')}:::{v.get('date', '')}"})
        elif isinstance(v, dict) and set(v) == {"type", "value"}:
            # live: квалифицированное значение (35, 13914) — НК требует строку
            # attr_value + поле attr_value_type, вложенный dict → 400
            if v["value"] is None or v["value"] == "":
                continue
            good_attrs.append({"attr_id": int(k), "attr_value": v["value"],
                               "attr_value_type": v["type"]})
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
    Draft-gtin нормализуется zfill(14) (live: НК отдаёт 13 цифр, feed требует
    14); draft, не приводимый к 14 цифрам, — RuntimeError.
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
            # live: generate-gtins отдаёт 13-значный gtin, /nk/feed требует 14
            gtin = str(draft["gtin"]).zfill(14)
            if not GTIN_RE.fullmatch(gtin):
                raise RuntimeError(
                    f"generate-gtins: draft gtin {draft['gtin']!r} is not 14 digits")
            card.gtin = gtin

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
    чанк). Агрегат: любой Rejected → карточки (кроме error*, published) →
    errors с текстом ошибки первого отклонённого чанка, батч → error; все
    Signed → published; все Moderated → notsigned + батч → signing; published
    не трогаем ни в какой ветке — карточка вышла из-под всех переходов
    refresh'а. Микс с Received/Processing — в полёте, ничего не меняем (ярлык
    «Processing»). Незнакомый статус любого чанка — RuntimeError. Возвращает
    агрегатный статус фида и статус батча.
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
            ("error", "errors", "error_sign", "published"))).all()
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


def sign_batch(db, batch_id: int, client, token) -> dict:
    """Подписание notsigned/error_sign-карточек боевым signer-агентом → published.

    error_sign — не тупик: карточка берётся в повторную попытку (xml
    докачивается заново, ошибки применяются поштучно), старый error_text
    сбрасывается при взятии в попытку. Батч должен существовать и иметь
    re-signable-карточки (notsigned|error_sign, иначе ValueError → 409);
    сразу → signing (коммит). Чанки по ≤10: /nk/feed-product-document
    отдаёт xmls [{goodId, gtin, xml}] — карточки спариваются с xml ПО GTIN
    (live: gtin может лежать под ключом GTIN и без ведущего нуля — 13 цифр;
    обе стороны нормализуются zfill(14)): порядок и полнота ответа
    не гарантируются (дамп: xmls — возможное подмножество + собственный
    errors[] по товарам). Карточка без своего
    xml → error_sign («не получен xml карточки», либо message из errors[]
    ответа, если он по gtin) и никогда не уходит в подписание. Каждый xml
    спаренной карточки подписывается doc_sign-задачей шлюза (CAdES PKCS#7
    detached, base64 — data_b64 = base64(xml)), items {goodId, base64Xml,
    signature} уходят одним запросом /nk/feed-product-sign-pkcs. NkHttpError
    чанка → его карточки error_sign + error_text, остальные чанки
    продолжаются (не рейзим). Но и 200 может нести errors[{goodId, message}]
    по отдельным товарам (дамп 85193-85203): rejected goodId → error_sign с
    message, остальные карточки чанка → published. Батч → published только
    когда нет notsigned/signing И нет error_sign (импортные error/errors
    не мешают — как в refresh_batch), иначе остаётся signing.
    """
    batch = db.get(Batch, batch_id)
    if batch is None:
        raise ValueError(f"batch {batch_id} not found")
    cards = db.query(Card).filter(Card.batch_id == batch_id,
                                  Card.status.in_(("notsigned",
                                                   "error_sign"))).order_by(Card.id).all()
    if not cards:
        raise ValueError(f"batch {batch_id} has no re-signable cards")
    batch.status = "signing"
    for card in cards:  # новая попытка: старый error_text не должен сбивать
        card.error_text = ""
    db.commit()

    n_signed = n_failed = 0
    for i in range(0, len(cards), SIGN_CHUNK):
        chunk = cards[i:i + SIGN_CHUNK]
        doc = client.feed_product_document(token, [c.gtin for c in chunk]) or {}
        # errors[] документа по gtin (дамп: ключи gtin/GTIN + message) — сообщение для карточек без xml
        doc_errors = {}
        for err in doc.get("errors") or []:
            if not isinstance(err, dict):
                continue
            gtin = str(err.get("gtin") or err.get("GTIN") or "")
            if gtin:
                # live: gtin без ведущего нуля — ключ нормализуем к 14 цифрам
                doc_errors[gtin.zfill(14)] = str(err.get("message") or "ошибка получения xml товара")
        by_gtin = {(c.gtin or "").zfill(14): c for c in chunk}
        paired, paired_ids = [], set()
        for entry in doc.get("xmls") or []:
            # битая запись (не dict / без xml / чужой или дубль gtin) не спаривается
            if not isinstance(entry, dict) or not entry.get("xml"):
                continue
            # live: gtin под ключом GTIN и без ведущего нуля (13 цифр) — zfill(14)
            g = str(entry.get("gtin") or entry.get("GTIN") or "").zfill(14)
            card = by_gtin.get(g)
            if card is None or id(card) in paired_ids:
                continue
            paired_ids.add(id(card))
            paired.append((card, entry))
        for card in chunk:
            if id(card) not in paired_ids:  # без своего xml не подписываем никогда
                card.status = "error_sign"
                card.error_text = doc_errors.get((card.gtin or "").zfill(14),
                                                 "не получен xml карточки")
                n_failed += 1
        items = []
        for card, entry in paired:
            data_b64 = base64.b64encode(entry["xml"].encode("utf-8")).decode("ascii")
            sig = _sign_via_gateway(db, "doc_sign", {"data_b64": data_b64})
            items.append({"goodId": entry["goodId"], "base64Xml": data_b64,
                          "signature": sig})
            card.good_id = str(entry["goodId"])
            card.status = "signing"
        if not items:
            continue  # спарить нечего — весь чанк уже в error_sign
        try:
            resp = client.feed_product_sign_pkcs(token, items) or {}
        except NkHttpError as e:
            for card, _ in paired:
                card.status = "error_sign"
                card.error_text = str(e)
                n_failed += 1
            continue
        # 200 может отклонить отдельные товары: errors[{goodId, message}] → error_sign
        sign_errors = {}
        for err in resp.get("errors") or []:
            if isinstance(err, dict) and err.get("goodId") is not None:
                sign_errors[str(err["goodId"])] = str(
                    err.get("message") or "товар не подписан")
        for card, _ in paired:  # good_id записан до вызова — ищем ошибки по нему
            if card.good_id in sign_errors:
                card.status = "error_sign"
                card.error_text = sign_errors[card.good_id]
                n_failed += 1
            else:
                card.status = "published"
                n_signed += 1

    stuck = db.query(Card).filter(Card.batch_id == batch_id,
                                  Card.status.in_(("notsigned", "signing",
                                                   "error_sign"))).count()
    if stuck == 0 and n_signed:
        batch.status = "published"
    db.commit()
    return {"signed": n_signed, "failed": n_failed}
