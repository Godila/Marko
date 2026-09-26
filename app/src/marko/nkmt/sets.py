"""Наборы (sets) НК: сборка карточки набора из существующих карточек.

Два равноправных входа — конструктор UI и импорт xlsx — сходятся в
build_set_row (одинаковые валидации), дальше общий конвейер карточек
(plan_batch → _persist_batch → feed → модерация → подпись). Ключевые правила:

- Ссылка на компонент: артикул нашей карточки (приоритет; работает до
  генерации её GTIN) или внешний GTIN 13–14 цифр.
- «Компоненты не заведены/не опубликованы»: создать черновик можно (warning),
  подача фида гардится (sets_feed_guard — блок с перечнем причин).
- Анти-дубли: идемпотентность по артикулу (plan_batch update), точный дубль
  состава (мультожество ссылка×количество) — блок, набор в набор — блок,
  чужой gtin — «gtin занят» (plan_batch conflict).
- Правка/удаление — только до подачи (status ok|error).

Атрибуты набора лёгпрома — live 22.09 (/nk/attributes?is_set=true): 2478
наименование, 2504 ТЗ, 23821 «Количество маркированных товаров в наборе»,
16271 «Состав набора» (текст немаркируемых). Декларация/вид/размер/цвет
набору не нужны — они на компонентах.
"""
import time

from sqlalchemy.orm import Session

from marko.nkmt import dicts
from marko.nkmt.client import NkHttpError
from marko.nkmt.dicts import TTL, _kv_put, get_defaults
from marko.nkmt.models import Batch, Card, SetItem
from marko.nkmt.parse import SETS_COLUMNS, parse_components, parse_xlsx
from marko.nkmt.service import _persist_batch, plan_batch
from marko.nkmt.validate import TNVED_RE
from marko.platform.models import PlatformKV

SET_SUM_MAX = 1000  # /nk/feed: ≤1000 кодов товаров в наборе (413 сверх)
FEEDABLE_STATUSES = ("ok", "error")  # правка/удаление набора — только до подачи


class SetSubmittedError(Exception):
    """Набор уже подан — правка/удаление недоступны (роут отдаёт 409)."""


def _norm_gtin(raw: str) -> str:
    """13–14 цифр → 14-значный GTIN; всё остальное — '' (невалиден)."""
    s = str(raw or "").strip()
    if s.isdigit() and len(s) in (13, 14):
        return s.zfill(14)
    return ""


# --- компоненты: разыменование ссылок и кэш внешних карточек ---

def _resolve_ref(db: Session, ref: str) -> dict | None:
    """Артикул нашей карточки (приоритет — свой каталог источник правды) или
    внешний GTIN; None = не похоже ни на то, ни на другое."""
    card = db.query(Card).filter_by(article=ref).first()
    if card is not None:
        return {"kind": "ours", "card": card}
    gtin = _norm_gtin(ref)
    if gtin:
        return {"kind": "external", "gtin": gtin}
    return None


def _product_cached(db: Session, client, token: str, gtin: str) -> dict:
    """Карточка внешнего GTIN из НК (kv nk_products:{gtin}, TTL как у атрибутных
    моделей): {found, is_set?, name?, status?}. 404 НК кэшируется как not found."""
    key = f"nk_products:{gtin}"
    kv = db.get(PlatformKV, key)
    if kv and time.time() - kv.value.get("fetched_at", 0) <= TTL:
        return kv.value
    try:
        res = client.product(token, gtin) or {}
    except NkHttpError as e:
        if e.status == 404:
            val = {"fetched_at": time.time(), "found": False}
        else:
            raise
    else:
        val = {"fetched_at": time.time(), "found": True,
               "is_set": bool(res.get("is_set")),
               "name": str(res.get("good_name", "")),
               "status": str(res.get("good_status", ""))}
    _kv_put(db, key, val)
    return val


def _components_key(items: list[dict]) -> tuple:
    """Ключ дедупа: мультожество (ссылка, количество); ссылка = артикул или
    GTIN (у наших компонентов article_src приоритетнее gtin — стабильнее)."""
    return tuple(sorted((it["article_src"] or it["gtin"], it["quantity"])
                        for it in items))


def _dup_components(db: Session, items: list[dict],
                    exclude_card_id: int | None = None) -> Card | None:
    """Существующий набор с тем же составом (мультожеством ссылок) или None."""
    key = _components_key(items)
    if not key:
        return None
    q = db.query(Card).filter(Card.is_set.is_(True))
    if exclude_card_id is not None:
        q = q.filter(Card.id != exclude_card_id)
    for s in q.all():
        s_items = db.query(SetItem).filter_by(card_id=s.id).all()
        if tuple(sorted((i.article_src or i.gtin, i.quantity)
                        for i in s_items)) == key:
            return s
    return None


def _next_set_article(db: Session) -> str:
    """SET-0001, SET-0002… — следующий свободный (конструктор без артикула)."""
    used = {c.article for c in db.query(Card).filter(
        Card.article.like("SET-%")).all()}
    n = 1
    while f"SET-{n:04d}" in used:
        n += 1
    return f"SET-{n:04d}"


# --- сборка строки набора: единые валидации для конструктора и импорта ---

def build_set_row(db: Session, payload: dict, client=None, token: str = "",
                  auto_article: bool = False,
                  exclude_card_id: int | None = None,
                  allow_update: bool = True) -> dict:
    """payload {article, name, brand, tnved, gtin, components, count,
    composition} → строка-план набора (совместима с plan_batch) + components/
    warnings. components — список {ref, quantity} или packed-строка (xlsx).
    Ошибки — в error (строка не годится), некритичное — в warnings."""
    errors: list[str] = []
    warnings: list[str] = []
    article = str(payload.get("article", "")).strip()
    name = str(payload.get("name", "")).strip()
    brand = str(payload.get("brand", "")).strip()
    tnved = str(payload.get("tnved", "")).strip()
    gtin = _norm_gtin(payload.get("gtin", ""))
    if str(payload.get("gtin", "")).strip() and not gtin:
        errors.append(f"GTIN набора: ожидаются 13–14 цифр («{payload.get('gtin')}»)")
    composition = str(payload.get("composition", "")).strip()

    raw = payload.get("components") or []
    if isinstance(raw, str):
        # известный артикул целиком не режется количеством («HX2» ≠ «H×2»)
        known = lambda a: db.query(Card).filter_by(article=a).first() is not None  # noqa: E731
        comps = parse_components(raw, known_article=known)
    else:
        comps = []
        for c in raw:
            try:
                q = int(c.get("quantity", 1))
            except (TypeError, ValueError):
                q = 0
            comps.append({"ref": str(c.get("ref", "")).strip(), "quantity": q})
    count = 0
    raw_count = str(payload.get("count", "") or "").strip()
    if raw_count:
        try:
            count = int(raw_count)
        except ValueError:
            errors.append(f"кол-во предметов: ожидается целое («{raw_count}»)")

    seen: set[str] = set()
    for c in comps:
        low = c["ref"].casefold()
        if not c["ref"]:
            errors.append("пустая ссылка на компонент")
            continue
        if c["quantity"] < 1:
            errors.append(f"количество компонента «{c['ref']}»: ожидается ≥ 1")
        if low in seen:
            errors.append(f"компонент «{c['ref']}» указан дважды")
        seen.add(low)
        if article and c["ref"] == article:
            errors.append("набор не может ссылаться на себя")
    total = sum(c["quantity"] for c in comps)
    if total > SET_SUM_MAX:
        errors.append(f"в наборе {total} предметов — НК допускает не более {SET_SUM_MAX}")
    if not comps and count < 1:
        errors.append("укажите компоненты или количество предметов (набор без привязки)")

    items: list[dict] = []
    ours_tnveds: list[str] = []
    for c in comps:
        if not c["ref"]:
            continue
        hit = _resolve_ref(db, c["ref"])
        if hit is None:
            errors.append(f"компонент «{c['ref']}»: нет такой карточки и не GTIN (13–14 цифр)")
            continue
        if hit["kind"] == "ours":
            sc = hit["card"]
            if sc.is_set:
                errors.append(f"компонент «{c['ref']}» сам набор — в набор вкладываются только товары")
            elif sc.status != "published":
                warnings.append(f"компонент «{c['ref']}» ещё не опубликован ({sc.status}) — "
                                "набор можно сохранить черновиком, подача подождёт")
            items.append({"gtin": sc.gtin or "", "article_src": sc.article,
                          "quantity": c["quantity"], "name": sc.name,
                          "status": sc.status, "kind": "ours"})
            if sc.tnved:
                ours_tnveds.append(sc.tnved)
        else:
            ext = None
            if client is not None and token:
                try:
                    ext = _product_cached(db, client, token, hit["gtin"])
                except Exception as e:
                    warnings.append(f"внешний компонент {hit['gtin']}: ЧЗ недоступен ({e})")
            if ext is not None:
                if not ext.get("found"):
                    warnings.append(f"внешний компонент {hit['gtin']} не найден в НК — "
                                    "подача будет заблокирована")
                elif ext.get("is_set"):
                    errors.append(f"внешний компонент {hit['gtin']} сам набор — "
                                  "в набор вкладываются только товары")
            items.append({"gtin": hit["gtin"], "article_src": "",
                          "quantity": c["quantity"],
                          "name": (ext or {}).get("name", ""),
                          "status": (ext or {}).get("status", ""),
                          "kind": "external"})

    if not tnved and ours_tnveds:
        tnved = ours_tnveds[0]
        warnings.append(f"ТН ВЭД набора взят из первого компонента: {tnved}")
    if tnved and not TNVED_RE.fullmatch(tnved):
        errors.append(f"ТН ВЭД набора: ожидаются 10 цифр («{tnved}»)")
        tnved = ""
    elif not tnved:
        errors.append("укажите ТН ВЭД набора (или добавьте компонент из каталога — возьмём его)")
    if len(set(ours_tnveds)) > 1:
        warnings.append("у компонентов разные ТН ВЭД — GS1 требует одну категорию; проверьте ТН ВЭД набора")

    if not name:
        ours_names = [i["name"] for i in items if i["kind"] == "ours" and i["name"]]
        if ours_names:
            name = "Набор: " + " + ".join(ours_names[:3]) + (
                f" +{len(ours_names) - 3}" if len(ours_names) > 3 else "")
        else:
            errors.append("укажите наименование — из компонентов его не собрать")

    if not brand:
        brand = get_defaults(db).get("brand", "")
    if brand and client is not None and token:
        try:
            dicts.resolve_brand(db, client, token, brand)
        except dicts.UnknownBrand as e:
            errors.append(f"бренд не найден: {e.args[0]}")
        except Exception as e:
            warnings.append(f"бренд не проверен: {e}")

    if not article and auto_article:
        article = _next_set_article(db)
    if not article:
        errors.append("укажите артикул набора")
    else:
        existing = db.query(Card).filter_by(article=article).first()
        if existing is not None and not existing.is_set \
                and existing.id != exclude_card_id:
            errors.append(f"артикул «{article}» занят карточкой товара")
        elif existing is not None and existing.is_set and exclude_card_id is None:
            if existing.status not in FEEDABLE_STATUSES:
                # поданный набор read-only: строка не пишется (plan_sets → dup),
                # конструктор с этим артикулом — 400
                errors.append(f"набор «{article}» уже подан ({existing.status}) — "
                              "состав поданного набора не меняется, соберите аналог")
            elif allow_update:
                # повторный импорт своей строки = обновление, не дубль состава
                exclude_card_id = existing.id
            else:
                # конструктор не обновляет чужой набор молча — только правка
                errors.append(f"артикул «{article}» уже используется набором — "
                              "правьте его или возьмите другой артикул")

    if items:
        count = total
        dup = _dup_components(db, items, exclude_card_id=exclude_card_id)
        if dup is not None:
            errors.append(f"состав дублирует набор {dup.article} ({dup.name})")
    elif count < 1 and "кол-во предметов" not in "; ".join(errors):
        errors.append("укажите количество предметов в наборе (≥ 1)")

    cat_id = ""
    if tnved and client is not None and token:
        try:
            cat_id = str(dicts.resolve_category(client, token, tnved, ""))
        except dicts.AmbiguousCategory:
            errors.append("категория НК по этому ТН ВЭД неоднозначна")
        except Exception as e:
            errors.append(f"категория: {e}")

    attributes = {"2478": name, "2504": brand, "23821": count, "16271": composition}
    return {"article": article, "tnved": tnved, "name": name, "gtin": gtin,
            "cat_id": cat_id, "attributes": attributes,
            "ok": not errors, "error": "; ".join(errors), "size_warning": "",
            "is_set": True, "components": items, "warnings": warnings,
            "mode": "bound" if items else "unbound", "count": count}


def plan_sets(db: Session, rows: list[dict]) -> list[dict]:
    """Строки наборов через общий plan_batch (артикул-идемпотентность, gtin
    new/update/conflict) + защита: строки «артикул занят карточкой товара» и
    «уже подан» не пишутся вовсе (dup), чтобы не перезаписать чужую карточку
    и не сбросить поданный набор в черновик."""
    planned = plan_batch(db, rows)
    for p in planned:
        if "занят карточкой товара" in p["error"] or "уже подан" in p["error"]:
            p["dup"] = True
    return planned


# --- импорт xlsx (превью и импорт гоняют один и тот же разбор) ---

def _rows_from_xlsx(db: Session, data: bytes, client, token: str) -> list[dict]:
    return [build_set_row(db, r, client, token) for r in parse_xlsx(data, SETS_COLUMNS)]


def _row_view(p: dict) -> dict:
    return {"article": p["article"], "name": p["name"], "tnved": p["tnved"],
            "gtin": p["gtin_final"], "gtin_status": p["gtin_status"],
            "brand": p["attributes"].get("2504", ""), "count": p["count"],
            "mode": p["mode"],
            "components": [{"gtin": i["gtin"], "article": i["article_src"],
                            "quantity": i["quantity"], "name": i["name"],
                            "status": i["status"], "kind": i["kind"]}
                           for i in p["components"]],
            "warnings": p["warnings"],
            "ok": p["dup"] is False and p["error"] == "",
            "error": p["error"], "dup": p["dup"]}


def _stats(planned: list[dict]) -> dict:
    return {"ok": sum(1 for p in planned if p["dup"] is False and p["error"] == ""),
            "error": sum(1 for p in planned if p["dup"] or p["error"]),
            "new": sum(1 for p in planned if p["gtin_status"] == "new"),
            "update": sum(1 for p in planned if p["gtin_status"] == "update"),
            "conflict": sum(1 for p in planned if p["gtin_status"] == "conflict")}


def preview_sets(db: Session, data: bytes, client, token: str) -> dict:
    planned = plan_sets(db, _rows_from_xlsx(db, data, client, token))
    return {"rows": [_row_view(p) for p in planned], "stats": _stats(planned)}


def import_sets(db: Session, filename: str, data: bytes, client, token: str) -> int:
    planned = plan_sets(db, _rows_from_xlsx(db, data, client, token))
    return _persist_batch(db, filename, planned)


# --- конструктор: превью / создание / правка / удаление ---

def create_set(db: Session, payload: dict, client, token: str) -> tuple[int, int]:
    """Валидный набор → карточка+компоненты в новом батче; ValueError с текстом
    строковых ошибок (роут отдаёт 400)."""
    row = build_set_row(db, payload, client, token, auto_article=True,
                        allow_update=False)
    p = plan_sets(db, [row])[0]
    if p["dup"] or p["error"]:
        raise ValueError(p["error"] or "артикул уже используется")
    batch_id = _persist_batch(db, "конструктор наборов", [p])
    card = db.query(Card).filter_by(article=row["article"]).one()
    return card.id, batch_id


def _get_set(db: Session, card_id: int) -> Card:
    card = db.get(Card, card_id)
    if card is None or not card.is_set:
        raise LookupError(f"set {card_id} not found")
    return card


def update_set(db: Session, card_id: int, payload: dict, client, token: str) -> Card:
    """Полная замена полей/состава до подачи. Артикул — ключ, не меняется.
    LookupError → 404, ValueError → 400/409 (роут различает по тексту гварда)."""
    card = _get_set(db, card_id)
    if card.status not in FEEDABLE_STATUSES:
        raise SetSubmittedError(card.status)
    payload = dict(payload, article=card.article)
    row = build_set_row(db, payload, client, token, exclude_card_id=card_id)
    p = plan_sets(db, [row])[0]
    if p["dup"] or p["error"]:
        raise ValueError(p["error"] or "артикул уже используется")
    card.tnved, card.name, card.cat_id = row["tnved"], row["name"], row["cat_id"]
    card.attributes = row["attributes"]
    if p["gtin_final"]:
        card.gtin = p["gtin_final"]
    card.status, card.error_text = "ok", ""
    db.query(SetItem).filter_by(card_id=card.id).delete()
    for it in row["components"]:
        db.add(SetItem(card_id=card.id, gtin=it["gtin"], article_src=it["article_src"],
                       quantity=it["quantity"]))
    db.commit()
    return card


def delete_set(db: Session, card_id: int) -> str:
    """Удаление черновика набора (до подачи). Пустой батч удаляется тоже.
    Возвращает article для аудита."""
    card = _get_set(db, card_id)
    if card.status not in FEEDABLE_STATUSES:
        raise SetSubmittedError(card.status)
    article, batch_id = card.article, card.batch_id
    db.delete(card)  # set_items — CASCADE
    db.commit()
    if db.query(Card).filter_by(batch_id=batch_id).count() == 0:
        b = db.get(Batch, batch_id)
        if b is not None:
            db.delete(b)
            db.commit()
    return article


# --- список и гард подачи ---

def sets_list(db: Session) -> list[dict]:
    sets = db.query(Card).filter(Card.is_set.is_(True)).order_by(Card.id.desc()).all()
    items_by_card: dict[int, list[SetItem]] = {}
    for it in db.query(SetItem).order_by(SetItem.id).all():
        items_by_card.setdefault(it.card_id, []).append(it)
    by_article = {c.article: c for c in db.query(Card).all()}
    out = []
    for s in sets:
        comps = []
        for it in items_by_card.get(s.id, []):
            src = by_article.get(it.article_src) if it.article_src else None
            comps.append({"article": it.article_src, "gtin": it.gtin,
                          "quantity": it.quantity,
                          "name": src.name if src else "",
                          "status": src.status if src else "",
                          "kind": "ours" if src else ("external" if it.gtin else "lost")})
        attrs = s.attributes or {}
        out.append({"id": s.id, "batch_id": s.batch_id, "article": s.article,
                    "gtin": s.gtin, "name": s.name, "tnved": s.tnved,
                    "brand": attrs.get("2504", ""), "count": attrs.get("23821", 0),
                    "composition": attrs.get("16271", ""),
                    "mode": "bound" if comps else "unbound",
                    "status": s.status, "error_text": s.error_text,
                    "created_at": s.created_at, "components": comps})
    return out


def sets_feed_guard(db: Session, cards: list[Card], client, token: str
                    ) -> list[tuple[Card, str]]:
    """Гард подачи батча с наборами: наши компоненты — published с GTIN и не
    наборы; внешние — существуют в НК и не наборы (кэш). Возвращает пары
    (карточка, причина) для блокирующего сообщения; пусто = можно подавать."""
    blocked: list[tuple[Card, str]] = []
    by_article = {c.article: c for c in db.query(Card).all()}
    for card in cards:
        for it in db.query(SetItem).filter_by(card_id=card.id).all():
            if it.article_src:
                src = by_article.get(it.article_src)
                if src is None:
                    blocked.append((card, f"компонент «{it.article_src}» не найден в каталоге"))
                elif src.is_set:
                    blocked.append((card, f"компонент «{it.article_src}» сам набор"))
                elif src.status != "published":
                    blocked.append((card, f"компонент «{it.article_src}» не опубликован ({src.status})"))
                elif not src.gtin:
                    blocked.append((card, f"у компонента «{it.article_src}» нет GTIN"))
                else:
                    continue
                break
            elif it.gtin:
                ext = _product_cached(db, client, token, it.gtin)
                if not ext.get("found"):
                    blocked.append((card, f"внешний компонент {it.gtin} не найден в НК"))
                    break
                if ext.get("is_set"):
                    blocked.append((card, f"внешний компонент {it.gtin} сам набор"))
                    break
    return blocked


def check_set(card: Card, client, token: str) -> dict:
    """«Проверить в ЧЗ»: карточка набора из /nk/product — статус, состав."""
    res = client.product(token, card.gtin) or {}
    return {"found": bool(res), "good_status": str(res.get("good_status", "")),
            "name": str(res.get("good_name", "")),
            "is_set": bool(res.get("is_set")),
            "set_gtins": res.get("set_gtins") or [],
            "update_date": str(res.get("update_date", ""))}
