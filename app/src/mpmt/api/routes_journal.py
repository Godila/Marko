from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from mpmt.api.deps import audit, get_db, require_scope
from mpmt.emitter.batch import return_batch, to_csv, withdraw_batch
from mpmt.journal.models import Item
from mpmt.mt.models import MtDoc
from mpmt.platform.models import PlatformToken

router = APIRouter(prefix="/v1")


class BatchBody(BaseModel):
    inn: str
    limit: int = Field(100, ge=1, le=1000)


@router.get("/journal")
def journal_list(
    state: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    q = db.query(Item)
    if state:
        q = q.filter_by(state=state)
    return [
        {"km": it.km, "state": it.state, "updated_at": it.updated_at,
         "last_event": it.last_event}
        for it in q.order_by(Item.updated_at.desc()).limit(limit).all()
    ]


@router.get("/journal/stats")
def journal_stats(
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    rows = db.query(Item.state, func.count()).group_by(Item.state).all()
    return {state: count for state, count in rows}


@router.post("/batches/withdraw")
def batches_withdraw(
    body: BatchBody,
    tok: PlatformToken = Depends(require_scope("docs:submit")),
    db: Session = Depends(get_db),
):
    doc_id = withdraw_batch(db, body.inn, body.limit)
    audit(db, tok.principal_id, "batch.withdraw", {"inn": body.inn, "result": doc_id})
    return {"doc_id": doc_id}


@router.post("/batches/return")
def batches_return(
    body: BatchBody,
    tok: PlatformToken = Depends(require_scope("docs:submit")),
    db: Session = Depends(get_db),
):
    docs, blocked = return_batch(db, body.inn, body.limit)
    audit(db, tok.principal_id, "batch.return",
          {"inn": body.inn, "result": {"docs": docs, "blocked": blocked}})
    return {"docs": docs, "blocked": blocked}


@router.get("/docs")
def docs_list(
    limit: int = Query(100, ge=1, le=1000),
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return [
        {"id": d.id, "type": d.type, "status": d.status, "created_at": d.created_at}
        for d in db.query(MtDoc).order_by(MtDoc.id.desc()).limit(limit).all()
    ]


@router.get("/docs/{doc_id}")
def docs_detail(
    doc_id: int,
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    d = db.get(MtDoc, doc_id)
    if not d:
        raise HTTPException(404, "doc not found")
    return {"id": d.id, "type": d.type, "status": d.status,
            "created_at": d.created_at, "payload": d.payload}


@router.get("/docs/{doc_id}/csv")
def docs_csv(
    doc_id: int,
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    if not db.get(MtDoc, doc_id):
        raise HTTPException(404, "doc not found")
    return PlainTextResponse(to_csv(db, doc_id), media_type="text/csv")


@router.post("/docs/{doc_id}/submit")
def submit_mt_doc(doc_id: int,
                  tok=Depends(require_scope("docs:submit")),
                  db: Session = Depends(get_db)):
    from mpmt.connector_mt import manager
    from mpmt.api.deps import audit
    try:
        external_id = manager.submit_doc(db, doc_id)
    except LookupError:
        raise HTTPException(404, "doc not found")
    except Exception as e:
        raise HTTPException(502, f"submit failed: {e}")
    audit(db, tok.principal_id, "doc.submit", {"doc_id": doc_id, "external_id": external_id})
    return {"external_id": external_id, "status": "submitted"}


@router.post("/docs/{doc_id}/check")
def check_mt_doc(doc_id: int,
                 tok=Depends(require_scope("docs:submit")),
                 db: Session = Depends(get_db)):
    from mpmt.connector_mt import manager
    from mpmt.api.deps import audit
    try:
        info = manager.check_doc(db, doc_id)
    except LookupError:
        raise HTTPException(404, "doc not found or not submitted")
    except Exception as e:
        raise HTTPException(502, f"check failed: {e}")
    doc = db.get(MtDoc, doc_id)
    audit(db, tok.principal_id, "doc.check", {"doc_id": doc_id, "mt_status": info.get("status")})
    return {"status": doc.status, "mt_status": info.get("status")}
