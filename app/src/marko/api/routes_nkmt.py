"""REST НК: декларации (справочник), дефолты карточек, атрибутные модели по ТН ВЭД, импорт выгрузки, подача фида,
выгрузной артефакт для 1С (xlsx/csv по published-карточкам батча).

Чтение — scope read, запись — nkmt:import, все мутации через audit().
dicts/attributes: сначала дешёвая валидация 10 цифр (400), и только потом
токен ЧЗ + NkClient — кривой tnved отсекается до какой-либо сети.
"""
import csv
import io
import re

import openpyxl
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from marko.api.deps import audit, get_db, require_scope
from marko.nkmt.dicts import attrs_model, get_defaults, set_defaults
from marko.nkmt.models import Batch, Card, Declaration
from marko.platform.models import PlatformToken

router = APIRouter(prefix="/v1/nkmt")

TNVED_10 = re.compile(r"[0-9]{10}")  # [0-9], не \d: \d ловит не-ASCII цифры


class DeclarationBody(BaseModel):
    doc_number: str
    doc_date: str
    doc_type: str = "declaration"   # declaration|certificate
    title: str = ""


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
    dup = db.query(Declaration).filter_by(
        doc_number=body.doc_number, doc_date=body.doc_date).first()
    if dup:
        raise HTTPException(409, "declaration pair already exists")
    d = Declaration(doc_number=body.doc_number, doc_date=body.doc_date,
                    doc_type=body.doc_type, title=body.title)
    db.add(d)
    db.commit()
    db.refresh(d)
    audit(db, tok.principal_id, "nkmt.declaration.create",
          {"id": d.id, "doc_number": d.doc_number, "doc_date": d.doc_date})
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
    db.delete(d)
    db.commit()
    audit(db, tok.principal_id, "nkmt.declaration.delete", {"id": decl_id})
    return {"ok": True}


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
    b = db.get(Batch, batch_id)
    audit(db, tok.principal_id, "nkmt.import",
          {"batch_id": batch_id, "filename": file.filename, "stats": b.stats})
    return {"batch_id": batch_id, "stats": b.stats}


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


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
