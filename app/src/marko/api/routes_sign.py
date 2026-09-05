import asyncio
import time

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from marko.api.deps import get_db, require_scope
from marko.platform.models import PlatformKV, PlatformToken
from marko.sign import service

router = APIRouter(prefix="/v1/sign")


def _touch_seen(db: Session, signer_id: int) -> None:
    db.execute(pg_insert(PlatformKV).values(
        key="signer_last_seen", value={"ts": time.time(), "principal_id": signer_id}
    ).on_conflict_do_update(index_elements=[PlatformKV.key],
        set_={"value": {"ts": time.time(), "principal_id": signer_id}}))
    db.commit()


@router.get("/ping")
def ping(tok: PlatformToken = Depends(require_scope("signer"))):
    return {"ok": True}


@router.post("/lease")
async def lease(wait: int = 30,
                tok: PlatformToken = Depends(require_scope("signer")),
                db: Session = Depends(get_db)):
    deadline = time.monotonic() + min(max(wait, 0), 30)
    while True:
        _touch_seen(db, tok.principal_id)
        task = service.try_acquire(db, owner=f"tok:{tok.id}")
        if task:
            return {"task_id": task.id, "type": task.type, "payload": task.payload}
        if time.monotonic() >= deadline:
            return Response(status_code=204)
        await asyncio.sleep(1)   # ponytail: sync-SQLAlchemy в async — короткие запросы, один signer


class ResultBody(BaseModel):
    task_id: str
    signature_b64: str | None = None
    error: str | None = None


@router.post("/results")
def results(body: ResultBody,
            tok: PlatformToken = Depends(require_scope("signer")),
            db: Session = Depends(get_db)):
    try:
        service.submit_result(db, body.task_id, body.signature_b64, body.error)
    except service.LeaseExpired:
        raise HTTPException(404, "lease expired")
    except service.DuplicateResult:
        raise HTTPException(409, "duplicate result")
    return {"ok": True}


class TestTaskBody(BaseModel):
    type: str = Field(pattern="^(auth_sign|doc_sign)$")
    data: str | None = None
    data_b64: str | None = None


@router.post("/test-task")
def test_task(body: TestTaskBody,
              tok: PlatformToken = Depends(require_scope("admin")),
              db: Session = Depends(get_db)):
    payload = ({"data": body.data} if body.type == "auth_sign" else {"data_b64": body.data_b64})
    return {"task_id": service.create_task(db, body.type, payload)}
