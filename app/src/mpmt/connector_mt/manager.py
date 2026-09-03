"""Логика контура ЧЗ: токен с кэшем, подача mt.docs через signer, статусы.

Ключевой флоу submit_doc:
  draft → (токен ЧЗ) → doc_sign-задача над бинарным product_document →
  POST /lk/documents/create → mt.docs.status=submitted + external_id.
"""
import base64
import json
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from mpmt.connector_mt.client import MtClient, MtHttpError
from mpmt.mt.models import MtDoc
from mpmt.platform.models import PlatformKV
from mpmt.sign import service as sign_service
from mpmt.settings import settings

log = logging.getLogger("mpmt.mt")

TOKEN_KV = "mt_token"
TOKEN_TTL_FALLBACK = timedelta(hours=9)      # если ЧЗ не отдал expireDate
TOKEN_REFRESH_MARGIN = timedelta(minutes=10)
SIGN_WAIT_SEC = 240                           # signer берёт задачу ≤30с + подпись


def _client() -> MtClient:
    return MtClient(base_v3=settings.mt_base_v3, base_v4=settings.mt_base_v4, pg=settings.mt_pg)


def _sign_via_gateway(db: Session, type_: str, payload: dict) -> str:
    """Задача в очередь подписи + ожидание результата (signer-агент жив в проде)."""
    task_id = sign_service.create_task(db, type_, payload)
    deadline = time.monotonic() + SIGN_WAIT_SEC
    while time.monotonic() < deadline:
        db.expire_all()
        st = sign_service.get_result(db, task_id)
        if st["status"] == "done":
            return st["result"]["signature_b64"]
        if st["status"] == "error":
            raise RuntimeError(f"sign task failed: {st['result'].get('error')}")
        time.sleep(2)
    raise TimeoutError(f"sign task {task_id} not done in {SIGN_WAIT_SEC}s")


def get_token(db: Session, client: MtClient | None = None) -> str:
    """Единый токен UUID с кэшем в platform.kv; обновление за 10 мин до exp."""
    kv = db.get(PlatformKV, TOKEN_KV)
    if kv:
        expires = kv.value.get("expires")
        if expires and datetime.fromisoformat(expires) - TOKEN_REFRESH_MARGIN > datetime.utcnow():
            return kv.value["token"]
    c = client or _client()
    pair = c.auth_key()
    signature = _sign_via_gateway(db, "auth_sign", {"data": pair["data"]})
    resp = c.sign_in(pair["uuid"], signature, settings.mt_inn)
    token = resp.get("token") or resp.get("uuidToken")   # UUID-форма: uuidToken
    if not token:
        raise RuntimeError(f"simpleSignIn no token: {json.dumps(resp)[:300]}")
    exp = TOKEN_TTL_FALLBACK
    if resp.get("expireDate"):
        try:
            exp = datetime.fromisoformat(resp["expireDate"].replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    db.execute(pg_insert(PlatformKV).values(
        key=TOKEN_KV, value={"token": token, "expires": exp.isoformat()}
    ).on_conflict_do_update(index_elements=[PlatformKV.key],
                            set_={"value": {"token": token, "expires": exp.isoformat()}}))
    db.commit()
    return token


def submit_doc(db: Session, mt_doc_id: int, client: MtClient | None = None) -> str:
    """draft → signing → submitted (external_id = uuid документа ЧЗ)."""
    doc = db.get(MtDoc, mt_doc_id)
    if doc is None:
        raise LookupError(f"mt.doc {mt_doc_id} not found")
    if doc.status != "draft":
        raise RuntimeError(f"mt.doc {mt_doc_id} status={doc.status}, expected draft")
    doc.status = "signing"; db.commit()
    try:
        c = client or _client()
        token = get_token(db, c)
        product_document_b64 = doc.product_document_b64  # emitter уже положил base64(JSON(payload))
        signature = _sign_via_gateway(db, "doc_sign", {"data_b64": product_document_b64})
        external_id = c.create_doc_signed(token, doc.type, product_document_b64, signature)
        doc.status, doc.external_id = "submitted", external_id
        db.commit()
        log.info("mt doc %s submitted as %s", mt_doc_id, external_id)
        return external_id
    except Exception:
        doc = db.get(MtDoc, mt_doc_id)
        doc.status = "error"
        db.commit()
        raise


def check_doc(db: Session, mt_doc_id: int, client: MtClient | None = None) -> dict:
    """submitted → checked_ok | error; возвращает сырой ответ ЧЗ."""
    doc = db.get(MtDoc, mt_doc_id)
    if doc is None or not doc.external_id:
        raise LookupError(f"mt.doc {mt_doc_id} not submitted")
    c = client or _client()
    token = get_token(db, c)
    info = c.doc_info(token, doc.external_id)
    status = info.get("status")
    if status == "CHECKED_OK":
        doc.status = "checked_ok"
    elif status in ("REJECTED", "ERROR"):
        doc.status = "error"
    db.commit()
    return info
