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
from sqlalchemy import func
from sqlalchemy.orm import Session

from marko.api.deps import audit, get_db, require_scope
from marko.nkmt.dicts import attrs_model, get_defaults, get_rules, set_defaults
from marko.nkmt.models import Batch, Card, Declaration, Rule
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


class RuleBody(BaseModel):
    brand: str = ""
    product_types: list[str] = []
    declaration_id: int
    producer: str = ""


class ResolveBody(BaseModel):
    brand: str = ""
    product_type: str = ""


@router.get("/declarations")
def declarations_list(
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return [
        {"id": d.id, "doc_number": d.doc_number, "doc_date": d.doc_date,
         "doc_type": d.doc_type, "title": d.title}
        for d in db.query(Declaration).order_by(Declaration.id).all()
    ]


@router.post("/declarations")
def declarations_create(
    body: DeclarationBody,
    tok: PlatformToken = Depends(require_scope("nkmt:import")),
    db: Session = Depends(get_db),
):
    # номер и дата обязательны и валидны: пара едет в карточки правилами
    # и справочником брендов, битая дата уронит DATE_RE валидатора построчно
    doc_number, doc_date = body.doc_number.strip(), body.doc_date.strip()
    if not doc_number:
        raise HTTPException(400, "укажите номер декларации")
    if not DATE_RE.fullmatch(doc_date):
        raise HTTPException(400, "дата декларации: ожидается ГГГГ-ММ-ДД")
    dup = db.query(Declaration).filter_by(
        doc_number=doc_number, doc_date=doc_date).first()
    if dup:
        raise HTTPException(409, "declaration pair already exists")
    d = Declaration(doc_number=doc_number, doc_date=doc_date,
                    doc_type=body.doc_type, title=body.title)
    db.add(d)
    db.commit()
    db.refresh(d)
    audit(db, tok.principal_id, "nkmt.declaration.create",
          {"id": d.id, "doc_number": doc_number, "doc_date": doc_date})
    return {"id": d.id}


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
    brand = body.brand.strip()
    # виды товара — список, пустые/дубли выбрасываем, порядок сохраняем
    ptypes = list(dict.fromkeys(t.strip() for t in body.product_types if t.strip()))
    if not brand and not ptypes:
        raise HTTPException(400, "укажите бренд или вид товара — правило без условия матчит все строки")
    if db.get(Declaration, body.declaration_id) is None:
        raise HTTPException(404, "declaration not found")
    # дубль условия — как матчит match_rule: бренд casefold, список видов точно
    dup = db.query(Rule).filter(func.lower(Rule.brand) == brand.casefold(),
                                Rule.product_types == ptypes).first()
    if dup:
        raise HTTPException(409, "правило с таким условием уже существует")
    r = Rule(brand=brand, product_types=ptypes,
             declaration_id=body.declaration_id, producer=body.producer.strip())
    db.add(r)
    db.commit()
    db.refresh(r)
    audit(db, tok.principal_id, "nkmt.rule.create",
          {"id": r.id, "brand": brand, "product_types": ptypes,
           "declaration_id": body.declaration_id, "producer": r.producer})
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
