"""REST НК: декларации (справочник), дефолты карточек, атрибутные модели по ТН ВЭД, импорт выгрузки, подача фида,
выгрузной артефакт для 1С (xlsx/csv по published-карточкам батча).

Чтение — scope read, запись — nkmt:import, все мутации через audit().
dicts/attributes: сначала дешёвая валидация 10 цифр (400), и только потом
токен ЧЗ + NkClient — кривой tnved отсекается до какой-либо сети.
"""
import csv
import io
import re
import xml.etree.ElementTree as ET
import zipfile

import openpyxl
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from marko.api.deps import audit, get_db, require_scope
from marko.nkmt.dicts import (agent_context, attrs_model, dict_hints,
                              get_defaults, get_rules, set_defaults)
from marko.nkmt.models import Batch, Card, Declaration, Producer, Rule
from marko.nkmt.parse import RULE_FIELDS
from marko.nkmt.validate import DATE_RE
from marko.platform.models import PlatformToken

router = APIRouter(prefix="/v1/nkmt")

TNVED_10 = re.compile(r"[0-9]{10}")  # [0-9], не \d: \d ловит не-ASCII цифры

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# битый контейнер (BadZipFile), не-xlsx (InvalidFileException), битый внутренний XML
BAD_XLSX = (zipfile.BadZipFile, openpyxl.utils.exceptions.InvalidFileException, ET.ParseError)


class DeclarationBody(BaseModel):
    doc_number: str
    doc_date: str
    doc_type: str = "declaration"   # declaration|certificate
    title: str = ""


class ProducerBody(BaseModel):
    name: str
    inn: str = ""
    kind: str = ""                  # entrepreneur|company|""
    note: str = ""


class RuleBody(BaseModel):
    brand: str = ""
    product_types: list[str] = []
    declaration_id: int
    producer: str = ""
    fields: dict[str, str] = {}


class ResolveBody(BaseModel):
    brand: str = ""
    product_type: str = ""


class SetComponentBody(BaseModel):
    ref: str            # артикул нашей карточки (приоритет) или GTIN 13–14 цифр
    quantity: int = 1


class SetBody(BaseModel):
    """Тело конструктора набора. components пуст + count>0 — набор без
    привязки GTIN (лёгпром допускает: состав задаётся количеством)."""
    article: str = ""           # пусто → авто SET-#### (конструктор)
    name: str = ""              # пусто → авто из имён компонентов
    brand: str = ""             # пусто → дефолтный бренд
    tnved: str = ""             # 10 цифр; пусто → ТНВЭД первого компонента
    gtin: str = ""              # пусто → генерация при подаче
    components: list[SetComponentBody] = []
    count: int = 0              # только для набора без привязки
    composition: str = ""       # текст немаркируемых вложений (attr 16271)


# --- нормализация/дедуп справочников: единая точка для POST и PUT, чтобы
# валидации создания и правки не разъезжались ---


def _norm_declaration(body: DeclarationBody) -> tuple[str, str]:
    # номер и дата обязательны и валидны: пара едет в карточки правилами
    # и справочником брендов, битая дата уронит DATE_RE валидатора построчно
    doc_number, doc_date = body.doc_number.strip(), body.doc_date.strip()
    if not doc_number:
        raise HTTPException(400, "укажите номер декларации")
    if not DATE_RE.fullmatch(doc_date):
        raise HTTPException(400, "дата декларации: ожидается ГГГГ-ММ-ДД")
    return doc_number, doc_date


def _norm_producer(body: ProducerBody) -> tuple[str, str, str, str]:
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "укажите наименование производителя")
    inn = body.inn.strip()
    if inn and (not inn.isdigit() or len(inn) not in (10, 12)):
        raise HTTPException(400, "ИНН: 10 или 12 цифр")
    kind = body.kind if body.kind in ("entrepreneur", "company") else ""
    return name, inn, kind, body.note.strip()


def _norm_rule(db: Session, body: RuleBody) -> tuple[str, list[str], dict[str, str]]:
    brand = body.brand.strip()
    # виды товара — список: пустые выбрасываем, дедуп по casefold (первое
    # написание выигрывает), порядок сохраняем — как матчит match_rule
    seen: dict[str, str] = {}
    for t in body.product_types:
        v = t.strip()
        if v:
            seen.setdefault(v.casefold(), v)
    ptypes = list(seen.values())
    if not brand and not ptypes:
        raise HTTPException(400, "укажите бренд или вид товара — правило без условия матчит все строки")
    if db.get(Declaration, body.declaration_id) is None:
        raise HTTPException(404, "declaration not found")
    # дополнительные поля: whitelist, пустые значения выкидываются
    fields = {}
    for key, value in (body.fields or {}).items():
        k = str(key).strip()
        if not k:
            continue
        if k not in RULE_FIELDS:
            raise HTTPException(400, f"поле «{k}» недоступно для подстановки правилом")
        v = str(value).strip()
        if v:
            fields[k] = v
    return brand, ptypes, fields


def _dup_declaration(db: Session, doc_number: str, doc_date: str,
                     exclude_id: int | None = None) -> None:
    q = db.query(Declaration).filter_by(doc_number=doc_number, doc_date=doc_date)
    if exclude_id is not None:   # PUT: своя запись — не дубль сама с собой
        q = q.filter(Declaration.id != exclude_id)
    if q.first():
        raise HTTPException(409, "declaration pair already exists")


def _dup_producer(db: Session, name: str,
                  exclude_id: int | None = None) -> None:
    # дедуп casefold в Python, не lower() БД: локаль сервера не фолдит
    # кириллицу; справочник мал — полный скан дешёвый
    low_name = name.casefold()
    if any(p.name.casefold() == low_name for p in db.query(Producer).all()
           if p.id != exclude_id):
        raise HTTPException(409, "производитель с таким наименованием уже есть")


def _dup_rule_condition(db: Session, brand: str, ptypes: list[str],
                        exclude_id: int | None = None) -> None:
    # дубль условия — как матчит match_rule: бренд и МНОЖЕСТВО видов без учёта
    # регистра; сравнение в Python (casefold), справочник правил мал
    low_brand, low_types = brand.casefold(), {t.casefold() for t in ptypes}
    for r in db.query(Rule).all():
        if r.id == exclude_id:
            continue
        if r.brand.casefold() == low_brand \
                and {t.casefold() for t in (r.product_types or [])} == low_types:
            raise HTTPException(409, "правило с таким условием уже существует")


def _decl_row(d: Declaration) -> dict:
    return {"id": d.id, "doc_number": d.doc_number, "doc_date": d.doc_date,
            "doc_type": d.doc_type, "title": d.title,
            "status": d.status, "date_to": d.date_to,
            "product_name": d.product_name, "tnved_list": d.tnved_list or [],
            "techregs": d.techregs, "applicant": d.applicant,
            "manufacturer": d.manufacturer, "checked_at": d.checked_at}


def _enrich(db, decls) -> None:
    """Best-effort обогащение свежих деклараций из ЧЗ ПО КЭШИРОВАННОМУ токену:
    без сети на обновление токена (get_token может ждать signer до 240 с —
    фоновое обогащение после добавления не должно подвешивать запрос)."""
    from marko.nkmt.client import NkClient
    from marko.nkmt.rd import cached_token, enrich_declarations
    from marko.settings import settings
    token = cached_token(db)
    if not token:
        return   # без свежего токена — обогатится кнопкой «Проверить в ЧЗ»
    try:
        # без ретрай-пауз и с коротким таймаутом: деградация ЧЗ не должна
        # подвешивать «Добавить» (худший случай ~4×8 c вместо ~140 c)
        enrich_declarations(db, NkClient(settings.mt_base_v3,
                                         base_v4=settings.mt_base_v4,
                                         sleeper=lambda _s: None, timeout=8),
                            token, decls)
    except Exception:
        db.commit()   # декларация уже добавлена — обогащение повторится «Проверить в ЧЗ»


@router.get("/declarations")
def declarations_list(
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return [_decl_row(d) for d in db.query(Declaration).order_by(Declaration.id).all()]


@router.post("/declarations")
def declarations_create(
    body: DeclarationBody,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    doc_number, doc_date = _norm_declaration(body)
    _dup_declaration(db, doc_number, doc_date)
    d = Declaration(doc_number=doc_number, doc_date=doc_date,
                    doc_type=body.doc_type, title=body.title)
    db.add(d)
    db.commit()
    db.refresh(d)
    audit(db, tok.principal_id, "nkmt.declaration.create",
          {"id": d.id, "doc_number": doc_number, "doc_date": doc_date})
    _enrich(db, [d])   # rich-поля из ЧЗ сразу, если ЧЗ доступен
    db.refresh(d)
    return {"id": d.id, "found": bool(d.status or d.tnved_list)}


@router.delete("/declarations/{decl_id}")
def declarations_delete(
    decl_id: int,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    d = db.get(Declaration, decl_id)
    if not d:
        raise HTTPException(404, "declaration not found")
    if db.query(Rule).filter(Rule.declaration_id == decl_id).first():
        raise HTTPException(409, "правило РД использует эту декларацию — удалите правило")
    db.delete(d)
    db.commit()
    audit(db, tok.principal_id, "nkmt.declaration.delete",
          {"id": decl_id, "doc_number": d.doc_number})
    return {"ok": True}


@router.put("/declarations/{decl_id}")
def declarations_update(
    decl_id: int,
    body: DeclarationBody,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    """Правка ядровых полей (номер/дата/тип/название). Смена пары — ключ
    rd/list и подстановок: rich-поля ЧЗ сбрасываются и обогащаются заново
    (best-effort); правка только типа/названия их сохраняет."""
    d = db.get(Declaration, decl_id)
    if not d:
        raise HTTPException(404, "declaration not found")
    doc_number, doc_date = _norm_declaration(body)
    _dup_declaration(db, doc_number, doc_date, exclude_id=decl_id)
    was = {"doc_number": d.doc_number, "doc_date": d.doc_date,
           "doc_type": d.doc_type, "title": d.title}
    pair_changed = (doc_number, doc_date) != (d.doc_number, d.doc_date)
    d.doc_number, d.doc_date = doc_number, doc_date
    d.doc_type, d.title = body.doc_type, body.title
    if pair_changed:
        d.status = d.date_to = d.product_name = d.techregs = \
            d.applicant = d.manufacturer = ""
        d.tnved_list = []
        d.checked_at = None
    db.commit()
    db.refresh(d)
    audit(db, tok.principal_id, "nkmt.declaration.update",
          {"id": decl_id, "was": was,
           "now": {"doc_number": d.doc_number, "doc_date": d.doc_date,
                   "doc_type": d.doc_type, "title": d.title}})
    if pair_changed:
        _enrich(db, [d])
        db.refresh(d)
    return {"id": d.id, "found": bool(d.status or d.tnved_list),
            "declaration": _decl_row(d)}


@router.post("/declarations/{decl_id}/check")
def declaration_check(
    decl_id: int,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    """Обновить данные одной декларации из ЧЗ (rd/list): статус, срок,
    ТНВЭД-список, техрегламенты, заявитель/изготовитель."""
    from marko.connector_mt import manager
    from marko.nkmt.client import NkClient, NkHttpError
    from marko.nkmt.rd import enrich_declarations
    from marko.settings import settings
    d = db.get(Declaration, decl_id)
    if not d:
        raise HTTPException(404, "declaration not found")
    try:
        out = enrich_declarations(
            db, NkClient(settings.mt_base_v3, base_v4=settings.mt_base_v4),
            manager.get_token(db), [d])
    except NkHttpError as e:
        raise HTTPException(502, f"nk upstream error: {e}")
    audit(db, tok.principal_id, "nkmt.declaration.check",
          {"id": decl_id, "found": out["found"]})
    return {**out, "declaration": _decl_row(d)}


@router.post("/declarations/check-all")
def declarations_check_all(
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    """Проверить все декларации реестра одним батчем (чанки ≤25 по лимиту ЧЗ)."""
    from marko.connector_mt import manager
    from marko.nkmt.client import NkClient, NkHttpError
    from marko.nkmt.rd import enrich_declarations
    from marko.settings import settings
    decls = db.query(Declaration).order_by(Declaration.id).all()
    if not decls:
        raise HTTPException(409, "реестр деклараций пуст")
    try:
        out = enrich_declarations(
            db, NkClient(settings.mt_base_v3, base_v4=settings.mt_base_v4),
            manager.get_token(db), decls)
    except NkHttpError as e:
        raise HTTPException(502, f"nk upstream error: {e}")
    audit(db, tok.principal_id, "nkmt.declaration.check-all",
          {"checked": out["checked"], "found": out["found"]})
    return out


# --- справочник производителей ---


@router.get("/producers")
def producers_list(
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return [{"id": p.id, "name": p.name, "inn": p.inn, "kind": p.kind,
             "note": p.note} for p in db.query(Producer).order_by(Producer.id).all()]


@router.post("/producers")
def producers_create(
    body: ProducerBody,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    name, inn, kind, note = _norm_producer(body)
    _dup_producer(db, name)
    p = Producer(name=name, inn=inn, kind=kind, note=note)
    db.add(p)
    db.commit()
    db.refresh(p)
    audit(db, tok.principal_id, "nkmt.producer.create",
          {"id": p.id, "name": name, "inn": inn})
    return {"id": p.id}


@router.delete("/producers/{producer_id}")
def producers_delete(
    producer_id: int,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    p = db.get(Producer, producer_id)
    if not p:
        raise HTTPException(404, "producer not found")
    db.delete(p)
    db.commit()
    audit(db, tok.principal_id, "nkmt.producer.delete", {"id": producer_id, "name": p.name})
    return {"ok": True}


@router.put("/producers/{producer_id}")
def producers_update(
    producer_id: int,
    body: ProducerBody,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    """Правка производителя (полная замена). Правила/дефолты ссылаются на него
    текстом — переименование их не переписывает (only hints updated)."""
    p = db.get(Producer, producer_id)
    if not p:
        raise HTTPException(404, "producer not found")
    name, inn, kind, note = _norm_producer(body)
    _dup_producer(db, name, exclude_id=producer_id)
    was = {"name": p.name, "inn": p.inn, "kind": p.kind, "note": p.note}
    p.name, p.inn, p.kind, p.note = name, inn, kind, note
    db.commit()
    db.refresh(p)
    audit(db, tok.principal_id, "nkmt.producer.update",
          {"id": producer_id, "was": was,
           "now": {"name": name, "inn": inn, "kind": kind, "note": note}})
    return {"id": p.id}


# --- правила РД: бренд × вид товара → декларация/производитель ---

@router.get("/rules")
def rules_list(
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return get_rules(db)


@router.post("/rules")
def rules_create(
    body: RuleBody,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    brand, ptypes, fields = _norm_rule(db, body)
    _dup_rule_condition(db, brand, ptypes)
    r = Rule(brand=brand, product_types=ptypes,
             declaration_id=body.declaration_id, producer=body.producer.strip(),
             fields=fields)
    db.add(r)
    db.commit()
    db.refresh(r)
    audit(db, tok.principal_id, "nkmt.rule.create",
          {"id": r.id, "brand": brand, "product_types": ptypes,
           "declaration_id": body.declaration_id, "producer": r.producer,
           "fields": fields})
    return {"id": r.id}


@router.delete("/rules/{rule_id}")
def rules_delete(
    rule_id: int,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    r = db.get(Rule, rule_id)
    if not r:
        raise HTTPException(404, "rule not found")
    db.delete(r)
    db.commit()
    audit(db, tok.principal_id, "nkmt.rule.delete",
          {"id": rule_id, "brand": r.brand, "product_types": r.product_types})
    return {"ok": True}


@router.put("/rules/{rule_id}")
def rules_update(
    rule_id: int,
    body: RuleBody,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    """Правка правила (полная замена: условие, декларация, подстановки).
    Действует со следующего резолва; созданные карточки не пересобираются."""
    r = db.get(Rule, rule_id)
    if not r:
        raise HTTPException(404, "rule not found")
    brand, ptypes, fields = _norm_rule(db, body)
    _dup_rule_condition(db, brand, ptypes, exclude_id=rule_id)
    was = {"brand": r.brand, "product_types": r.product_types or [],
           "declaration_id": r.declaration_id, "producer": r.producer,
           "fields": r.fields or {}}
    r.brand, r.product_types = brand, ptypes
    r.declaration_id = body.declaration_id
    r.producer = body.producer.strip()
    r.fields = fields
    db.commit()
    db.refresh(r)
    audit(db, tok.principal_id, "nkmt.rule.update",
          {"id": rule_id, "was": was,
           "now": {"brand": brand, "product_types": ptypes,
                   "declaration_id": body.declaration_id, "producer": r.producer,
                   "fields": fields}})
    return {"id": r.id}


# --- наборы: конструктор + импорт xlsx поверх общего конвейера ---

def _nk_client(db):
    """Токен ЧЗ + NkClient для превью/проверок; NkHttpError наверх → 502."""
    from marko.connector_mt import manager
    from marko.nkmt.client import NkClient
    from marko.settings import settings
    return NkClient(settings.mt_base_v3), manager.get_token(db)


@router.get("/sets")
def sets_view(
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    from marko.nkmt.sets import sets_list
    return sets_list(db)


@router.get("/sets/cards")
def sets_cards(
    q: str = "",
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Пикер компонентов конструктора: опубликованные карточки с GTIN."""
    query = db.query(Card).filter(Card.is_set.is_(False), Card.status == "published",
                                  Card.gtin != "")
    if q:
        like = f"%{q}%"
        query = query.filter(Card.article.ilike(like) | Card.gtin.ilike(like)
                             | Card.name.ilike(like))
    return [{"article": c.article, "gtin": c.gtin, "name": c.name,
             "tnved": c.tnved, "status": c.status}
            for c in query.order_by(Card.id.desc()).limit(20).all()]


@router.post("/sets/preview")
def sets_preview(
    body: SetBody,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    """Dry-run конструктора: валидации, статусы компонентов, gtin-план."""
    from marko.nkmt.sets import _row_view, build_set_row, plan_sets
    client, token = _nk_client(db)
    row = build_set_row(db, body.model_dump(), client, token, auto_article=True,
                        allow_update=False)
    p = plan_sets(db, [row])[0]
    return _row_view(p)


@router.post("/sets")
def sets_create(
    body: SetBody,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    from marko.nkmt.client import NkHttpError
    from marko.nkmt.sets import create_set
    try:
        client, token = _nk_client(db)
        card_id, batch_id = create_set(db, body.model_dump(), client, token)
    except NkHttpError as e:
        raise HTTPException(502, f"nk upstream error: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(db, tok.principal_id, "nkmt.set.create",
          {"id": card_id, "batch_id": batch_id, "article": body.article,
           "components": [c.ref for c in body.components]})
    return {"id": card_id, "batch_id": batch_id}


@router.put("/sets/{set_id}")
def sets_update(
    set_id: int,
    body: SetBody,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    from marko.nkmt.client import NkHttpError
    from marko.nkmt.sets import SetSubmittedError, update_set
    try:
        client, token = _nk_client(db)
        card = update_set(db, set_id, body.model_dump(), client, token)
    except NkHttpError as e:
        raise HTTPException(502, f"nk upstream error: {e}")
    except LookupError:
        raise HTTPException(404, "set not found")
    except SetSubmittedError:
        raise HTTPException(409, "набор уже подан — правка недоступна")
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit(db, tok.principal_id, "nkmt.set.update",
          {"id": set_id, "article": card.article,
           "components": [c.ref for c in body.components]})
    return {"id": card.id}


@router.delete("/sets/{set_id}")
def sets_delete(
    set_id: int,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    from marko.nkmt.sets import SetSubmittedError, delete_set
    try:
        article = delete_set(db, set_id)
    except LookupError:
        raise HTTPException(404, "set not found")
    except SetSubmittedError:
        raise HTTPException(409, "набор уже подан — удаление недоступно")
    audit(db, tok.principal_id, "nkmt.set.delete", {"id": set_id, "article": article})
    return {"ok": True}


@router.post("/sets/{set_id}/feed")
def sets_feed(
    set_id: int,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    """Подать фид набора — его батч целиком (обычно батч из одного набора;
    гард наборов отработает по всем ok-карточкам батча)."""
    from marko.nkmt.client import NkHttpError
    from marko.nkmt.service import feed_batch
    card = db.get(Card, set_id)
    if card is None or not card.is_set:
        raise HTTPException(404, "set not found")
    try:
        client, token = _nk_client(db)
        out = feed_batch(db, card.batch_id, client, token)
    except (NkHttpError, RuntimeError) as e:
        raise HTTPException(502, f"nk upstream error: {e}")
    except ValueError as e:
        raise HTTPException(409, str(e))
    audit(db, tok.principal_id, "nkmt.set.feed",
          {"id": set_id, "batch_id": card.batch_id, "feed_id": out["feed_id"]})
    return out


@router.post("/sets/{set_id}/check")
def sets_check(
    set_id: int,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    """Проверить набор в ЧЗ (/nk/product): статус карточки, состав set_gtins."""
    from marko.nkmt.client import NkHttpError
    from marko.nkmt.sets import check_set
    card = db.get(Card, set_id)
    if card is None or not card.is_set:
        raise HTTPException(404, "set not found")
    if not card.gtin:
        raise HTTPException(409, "у набора ещё нет GTIN — подайте фид")
    try:
        client, token = _nk_client(db)
        out = check_set(card, client, token)
    except NkHttpError as e:
        raise HTTPException(502, f"nk upstream error: {e}")
    audit(db, tok.principal_id, "nkmt.set.check",
          {"id": set_id, "found": out["found"]})
    return out


@router.get("/sets/import/template")
def sets_import_template(tok: PlatformToken = Depends(require_scope("read"))):
    from marko.nkmt.template import build_set_template
    return Response(build_set_template(), media_type=XLSX_MIME,
                    headers={"Content-Disposition":
                             'attachment; filename="nkmt-sets-template.xlsx"'})


@router.post("/sets/import/preview")
def sets_import_preview(
    file: UploadFile = File(...),
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    from marko.nkmt.client import NkHttpError
    from marko.nkmt.sets import preview_sets
    try:
        client, token = _nk_client(db)
        return preview_sets(db, file.file.read(), client, token)
    except NkHttpError as e:
        raise HTTPException(502, f"nk upstream error: {e}")
    except BAD_XLSX:
        raise HTTPException(400, "не удалось прочитать файл как xlsx")


@router.post("/sets/import")
def sets_import(
    file: UploadFile = File(...),
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    from marko.nkmt.client import NkHttpError
    from marko.nkmt.sets import import_sets
    try:
        client, token = _nk_client(db)
        batch_id = import_sets(db, file.filename, file.file.read(), client, token)
    except NkHttpError as e:
        raise HTTPException(502, f"nk upstream error: {e}")
    except BAD_XLSX:
        raise HTTPException(400, "не удалось прочитать файл как xlsx")
    b = db.get(Batch, batch_id)
    audit(db, tok.principal_id, "nkmt.set.import",
          {"batch_id": batch_id, "filename": file.filename, "stats": b.stats})
    return {"batch_id": batch_id, "stats": b.stats}


@router.post("/resolve")
def nkmt_resolve(
    body: ResolveBody,
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """«Проверка подстановок»: резолв всех подставляемых полей для бренда и вида
    товара без файла (дефолты → правила). Read-only."""
    from marko.nkmt.resolve import resolve_fields
    return resolve_fields(body.brand, body.product_type,
                          get_defaults(db), get_rules(db))


@router.get("/defaults")
def defaults_get(
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return get_defaults(db)


@router.put("/defaults")
def defaults_put(
    value: dict,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    set_defaults(db, value)
    audit(db, tok.principal_id, "nkmt.defaults.set", {"keys": sorted(value)})
    return {"ok": True}


@router.get("/dicts/attributes")
def dicts_attributes(
    tnved: str = Query(...),
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    if not TNVED_10.fullmatch(tnved or ""):
        raise HTTPException(400, "tnved must be exactly 10 digits")
    from marko.connector_mt import manager
    from marko.nkmt.client import NkClient, NkHttpError
    from marko.settings import settings
    try:
        token = manager.get_token(db)
        client = NkClient(settings.mt_base_v3)
        return attrs_model(db, client, token, tnved)
    except NkHttpError as e:
        raise HTTPException(502, f"nk upstream error: {e}")


@router.get("/dicts/hints")
def dicts_hints(
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Подсказки для условий правил РД: пресетные виды товара (из кэша
    атрибутных моделей) и известные бренды. Без сети."""
    return dict_hints(db, get_defaults(db))


@router.get("/context")
def nkmt_context(
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Контекст контура НК для внешних агентов: колонки шаблона, приоритет
    подстановок, дефолты, правила, справочники, карта эндпоинтов. Read-only,
    без сети — агент понимает, как собирается карточка, и может подсказывать."""
    return agent_context(db)


@router.post("/import")
def nkmt_import(
    file: UploadFile = File(...),
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    from marko.connector_mt import manager
    from marko.nkmt.client import NkClient, NkHttpError
    from marko.nkmt.service import import_batch
    from marko.settings import settings
    try:
        token = manager.get_token(db)
        client = NkClient(settings.mt_base_v3)
        batch_id = import_batch(db, file.filename, file.file.read(), client, token)
    except NkHttpError as e:
        raise HTTPException(502, f"nk upstream error: {e}")
    except BAD_XLSX:
        raise HTTPException(400, "не удалось прочитать файл как xlsx")
    b = db.get(Batch, batch_id)
    audit(db, tok.principal_id, "nkmt.import",
          {"batch_id": batch_id, "filename": file.filename, "stats": b.stats})
    return {"batch_id": batch_id, "stats": b.stats}


@router.get("/import/template")
def import_template(tok: PlatformToken = Depends(require_scope("read"))):
    """Шаблон выгрузки: шапка+пример и лист «Инструкция» — из parse.SPEC."""
    from marko.nkmt.template import build_template
    return Response(build_template(), media_type=XLSX_MIME,
                    headers={"Content-Disposition":
                             'attachment; filename="nkmt-import-template.xlsx"'})


@router.post("/import/preview")
def nkmt_import_preview(
    file: UploadFile = File(...),
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    """Dry-run импорта: resolve+plan БЕЗ записи — предпросмотр с подстановками
    (файл/правило/дефолт), судьбой gtin и ошибками валидации построчно."""
    from marko.connector_mt import manager
    from marko.nkmt.client import NkClient, NkHttpError
    from marko.nkmt.service import preview_batch
    from marko.settings import settings
    try:
        token = manager.get_token(db)
        client = NkClient(settings.mt_base_v3)
        return preview_batch(db, file.file.read(), client, token)
    except NkHttpError as e:
        raise HTTPException(502, f"nk upstream error: {e}")
    except BAD_XLSX:
        raise HTTPException(400, "не удалось прочитать файл как xlsx")


@router.post("/batches/{batch_id}/feed")
def nkmt_feed(
    batch_id: int,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    from marko.connector_mt import manager
    from marko.nkmt.client import NkClient, NkHttpError
    from marko.nkmt.service import feed_batch
    from marko.settings import settings
    if not db.get(Batch, batch_id):
        raise HTTPException(404, "batch not found")
    try:
        token = manager.get_token(db)
        client = NkClient(settings.mt_base_v3)
        out = feed_batch(db, batch_id, client, token)
    except (NkHttpError, RuntimeError) as e:
        raise HTTPException(502, f"nk upstream error: {e}")
    except ValueError as e:
        raise HTTPException(409, str(e))
    audit(db, tok.principal_id, "nkmt.feed",
          {"batch_id": batch_id, "feed_id": out["feed_id"], "feed_ids": out["feed_ids"]})
    return out


@router.post("/batches/{batch_id}/refresh")
def nkmt_refresh(
    batch_id: int,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    from marko.connector_mt import manager
    from marko.nkmt.client import NkClient, NkHttpError
    from marko.nkmt.service import refresh_batch
    from marko.settings import settings
    if not db.get(Batch, batch_id):
        raise HTTPException(404, "batch not found")
    try:
        token = manager.get_token(db)
        client = NkClient(settings.mt_base_v3)
        out = refresh_batch(db, batch_id, client, token)
    except (NkHttpError, RuntimeError) as e:
        raise HTTPException(502, f"nk upstream error: {e}")
    except ValueError as e:
        raise HTTPException(409, str(e))
    audit(db, tok.principal_id, "nkmt.refresh",
          {"batch_id": batch_id, "feed_status": out["feed_status"],
           "batch_status": out["batch_status"]})
    return out


@router.post("/batches/{batch_id}/sign")
def nkmt_sign(
    batch_id: int,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    from marko.connector_mt import manager
    from marko.nkmt.client import NkClient, NkHttpError
    from marko.nkmt.service import sign_batch
    from marko.settings import settings
    if not db.get(Batch, batch_id):
        raise HTTPException(404, "batch not found")
    try:
        token = manager.get_token(db)
        client = NkClient(settings.mt_base_v3)
        out = sign_batch(db, batch_id, client, token)
    except (NkHttpError, RuntimeError) as e:
        raise HTTPException(502, f"nk upstream error: {e}")
    except ValueError as e:
        raise HTTPException(409, str(e))
    audit(db, tok.principal_id, "nkmt.sign",
          {"batch_id": batch_id, "signed": out["signed"], "failed": out["failed"]})
    return out


@router.get("/batches")
def batches_list(
    status: str = "",
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    q = db.query(Batch)
    if status:
        q = q.filter(Batch.status == status)
    return [{"id": b.id, "status": b.status, "source_filename": b.source_filename,
             "stats": b.stats, "created_at": b.created_at}
            for b in q.order_by(Batch.id.desc()).all()]


@router.get("/batches/{batch_id}")
def batch_detail(
    batch_id: int,
    card_status: str = "",
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    b = db.get(Batch, batch_id)
    if not b:
        raise HTTPException(404, "batch not found")
    q = db.query(Card).filter(Card.batch_id == batch_id)
    if card_status:
        q = q.filter(Card.status == card_status)
    cards = [{"id": c.id, "article": c.article, "gtin": c.gtin, "name": c.name,
              "status": c.status, "error_text": c.error_text}
             for c in q.order_by(Card.id).all()]
    return {"id": b.id, "status": b.status, "source_filename": b.source_filename,
            "stats": b.stats, "created_at": b.created_at, "cards": cards}


@router.get("/batches/{batch_id}/report")
def batch_report(
    batch_id: int,
    format: str = "xlsx",
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Выгрузной артефакт для 1С: published-карточки батча, сортировка по article.

    xlsx (по умолчанию) — openpyxl, лист «GTIN», шапка GTIN|Наименование;
    csv — gtin;name в UTF-8 с BOM (Excel). Пустой батч — отчёт из одной шапки.
    """
    if not db.get(Batch, batch_id):
        raise HTTPException(404, "batch not found")
    if format not in ("xlsx", "csv"):
        raise HTTPException(400, "format must be xlsx or csv")
    cards = (db.query(Card)
             .filter(Card.batch_id == batch_id, Card.status == "published")
             .order_by(Card.article).all())
    if format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["GTIN", "Наименование"])
        for c in cards:
            w.writerow([c.gtin, c.name])
        return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                        headers={"Content-Disposition":
                                 f'attachment; filename="nkmt-batch-{batch_id}.csv"'})
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "GTIN"
    ws.append(["GTIN", "Наименование"])
    for c in cards:
        ws.append([c.gtin, c.name])
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), media_type=XLSX_MIME,
                    headers={"Content-Disposition":
                             f'attachment; filename="nkmt-batch-{batch_id}.xlsx"'})
