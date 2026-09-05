"""Очередь задач подписи: выдача в аренду (lease), приём результата (results).

Идемпотентность на стороне шлюза: задача в 'leased' выдана ровно одному подписанту
до lease_until; просроченная аренда автоматически возвращается в 'pending'
(attempt+1), повторный результат по завершённой/переигранной задаче отклоняется.
"""
import uuid
from datetime import datetime, timedelta

from sqlalchemy import text, update
from sqlalchemy.orm import Session

from marko.sign.models import SignTask

LEASE_TTL = timedelta(seconds=120)


def create_task(db: Session, type_: str, payload: dict) -> str:
    task_id = str(uuid.uuid4())
    db.add(SignTask(id=task_id, type=type_, payload=payload, status="pending"))
    db.commit()
    return task_id


def try_acquire(db: Session, owner: str) -> SignTask | None:
    db.execute(text(
        "UPDATE sign.tasks SET status='pending', lease_until=NULL "
        "WHERE status='leased' AND lease_until < now()"))
    row = db.execute(text(
        "SELECT id FROM sign.tasks WHERE status='pending' "
        "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED")).scalar()
    if not row:
        db.commit()
        return None
    db.execute(update(SignTask).where(SignTask.id == row).values(
        status="leased", lease_until=datetime.utcnow() + LEASE_TTL,
        lease_owner=owner, attempt=SignTask.attempt + 1))
    db.commit()
    return db.get(SignTask, row)


class LeaseExpired(Exception):
    pass


class DuplicateResult(Exception):
    pass


def submit_result(db: Session, task_id: str, signature_b64: str | None, error: str | None) -> None:
    t = db.get(SignTask, task_id)
    if t is None or t.status == "pending":   # нет/переиграна — аренда истекла
        raise LeaseExpired(task_id)
    if t.status != "leased":                 # done|error — дубликат
        raise DuplicateResult(task_id)
    if error:
        t.status, t.result = "error", {"error": error}
    else:
        t.status, t.result = "done", {"signature_b64": signature_b64}
    db.commit()


def get_result(db: Session, task_id: str) -> dict | None:
    t = db.get(SignTask, task_id)
    if t is None:
        return None
    return {"id": t.id, "type": t.type, "status": t.status, "result": t.result}
