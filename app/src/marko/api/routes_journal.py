from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from marko.api.deps import audit, get_db, require_scope
from marko.connector_wb.client import WBClient, WbHttpError, WbLimitError, load_wb_token
from marko.connector_wb.models import WbReturn
from marko.connector_wb.returns import run_returns_once
from marko.emitter.batch import return_batch, to_csv, withdraw_batch
from marko.journal.models import Item
from marko.mt.models import MtDoc
from marko.platform.models import PlatformKV, PlatformToken
from marko.settings import settings

router = APIRouter(prefix="/v1")


class BatchBody(BaseModel):
    inn: str
    limit: int = Field(100, ge=1, le=1000)


class EmitterDefaultsBody(BaseModel):
    fias_id: str = ""
    primary_custom_name: str = ""


@router.get("/emitter/defaults")
def emitter_defaults_get(
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    kv = db.get(PlatformKV, "emitter_defaults")
    return kv.value if kv else {"fias_id": "", "primary_custom_name": ""}


@router.put("/emitter/defaults")
def emitter_defaults_put(
    body: EmitterDefaultsBody,
    tok: PlatformToken = Depends(require_scope("docs:submit")),
    db: Session = Depends(get_db),
):
    value = body.model_dump()
    db.execute(pg_insert(PlatformKV).values(key="emitter_defaults", value=value)
               .on_conflict_do_update(index_elements=[PlatformKV.key],
                                      set_={"value": value}))
    db.commit()
    audit(db, tok.principal_id, "emitter.defaults", value)
    return value


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


@router.get("/wb/returns")
def wb_returns_list(
    active: bool | None = None,
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    out = []
    for r in db.query(WbReturn).order_by(WbReturn.updated_at.desc()).limit(500).all():
        p = r.payload or {}
        if active is not None and bool(p.get("isStatusActive")) != active:
            continue
        out.append({"srid": r.srid, "order_id": r.order_id, "status": r.status,
                    "expired_dt": r.expired_dt, "reason": p.get("reason"),
                    "return_type": p.get("returnType"), "subject": p.get("subjectName"),
                    "office": p.get("dstOfficeAddress"), "order_dt": p.get("orderDt"),
                    "ready_dt": p.get("readyToReturnDt"), "completed_dt": p.get("completedDt"),
                    "is_active": bool(p.get("isStatusActive"))})
    return out


@router.post("/wb/returns/poll")
def wb_returns_poll(
    tok: PlatformToken = Depends(require_scope("docs:submit")),
    db: Session = Depends(get_db),
):
    """Ручной поллинг goods-return (квота 2/1ч — гейт внутри клиента)."""
    import asyncio
    from marko.notifier import send
    try:
        client = WBClient(token=load_wb_token(settings.wb_token_file), db=db)
        res = run_returns_once(db, client)
    except (WbHttpError, WbLimitError) as e:
        raise HTTPException(502, f"wb poll failed: {e}")
    for text in res.get("alerts", []):
        asyncio.run(send(text))
    audit(db, tok.principal_id, "wb.returns.poll",
          {k: v for k, v in res.items() if k != "alerts"})
    return res


@router.get("/docs")
def docs_list(
    limit: int = Query(100, ge=1, le=1000),
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    return [
        {"id": d.id, "type": d.type, "status": d.status, "external_id": d.external_id, "created_at": d.created_at}
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
    from marko.connector_mt import manager
    from marko.api.deps import audit
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
    from marko.connector_mt import manager
    from marko.api.deps import audit
    try:
        info = manager.check_doc(db, doc_id)
    except LookupError:
        raise HTTPException(404, "doc not found or not submitted")
    except Exception as e:
        raise HTTPException(502, f"check failed: {e}")
    doc = db.get(MtDoc, doc_id)
    audit(db, tok.principal_id, "doc.check", {"doc_id": doc_id, "mt_status": info.get("status")})
    return {"status": doc.status, "mt_status": info.get("status")}
