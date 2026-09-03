"""REST НК: декларации (справочник), дефолты карточек, атрибутные модели по ТН ВЭД.

Чтение — scope read, запись — nkmt:import, все мутации через audit().
dicts/attributes: сначала дешёвая валидация 10 цифр (400), и только потом
токен ЧЗ + NkClient — кривой tnved отсекается до какой-либо сети.
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from mpmt.api.deps import audit, get_db, require_scope
from mpmt.nkmt.dicts import attrs_model, get_defaults, set_defaults
from mpmt.nkmt.models import Declaration
from mpmt.platform.models import PlatformToken

router = APIRouter(prefix="/v1/nkmt")

TNVED_10 = re.compile(r"\d{10}")


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
    from mpmt.connector_mt import manager
    from mpmt.nkmt.client import NkClient, NkHttpError
    from mpmt.settings import settings
    try:
        token = manager.get_token(db)
        client = NkClient(settings.mt_base_v3)
        return attrs_model(db, client, token, tnved)
    except NkHttpError as e:
        raise HTTPException(502, f"nk upstream error: {e}")
