import time
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from marko.api.deps import audit, get_db, require_scope
from marko.connector_wb.client import WBClient, WbHttpError, WbLimitError, load_wb_token
from marko.connector_wb.models import WbReturn
from marko.connector_wb.returns import _parse_iso, run_returns_once
from marko.emitter.batch import lk_receipts, return_batch, to_csv, withdraw_batch
from marko.journal import log_action
from marko.journal.models import Item
from marko.mt.models import MtDoc
from marko.nkmt.models import Batch
from marko.platform.models import PlatformKV, PlatformToken
from marko.settings import settings

router = APIRouter(prefix="/v1")


class BatchBody(BaseModel):
    inn: str
    limit: int = Field(100, ge=1, le=1000)


class ResolveBody(BaseModel):
    target: Literal["NEW", "PENDING_WITHDRAW", "PENDING_RETURN", "WITHDRAWN", "RETURNED"]
    note: str = Field("", max_length=500)


class CisSyncBody(BaseModel):
    # пусто/None → все позиции журнала; для карточки КМ — список из одного
    kms: list[str] | None = Field(None, max_length=1000)


class WithdrawSourceBody(BaseModel):
    by: Literal["us", "wb"]


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
        {"km": it.km, "state": it.state, "withdrawn_by": it.withdrawn_by,
         "updated_at": it.updated_at, "last_event": it.last_event,
         "cis_status": it.cis_status, "cis_product_name": it.cis_product_name,
         "cis_checked_at": it.cis_checked_at}
        for it in q.order_by(Item.updated_at.desc()).limit(limit).all()
    ]


@router.get("/journal/stats")
def journal_stats(
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    rows = db.query(Item.state, func.count()).group_by(Item.state).all()
    return {state: count for state, count in rows}


@router.post("/journal/{km}/resolve")
def journal_resolve(
    km: str,
    body: ResolveBody,
    tok: PlatformToken = Depends(require_scope("docs:submit")),
    db: Session = Depends(get_db),
):
    """Ручной разбор аномалии: перевод в осмысленное состояние + аудит manual/resolve.

    Resolve — событие, а не новый статус: словарь состояний не расширяется,
    last_event (WB-первичка для LK_RECEIPT/LP_RETURN) не затирается.
    """
    it = db.get(Item, km)
    if it is None:
        raise HTTPException(404, "КМ не найден в журнале")
    if not it.state.startswith("ANOMALY_"):
        raise HTTPException(409, f"не аномалия: {it.state}")
    src, target = it.state, body.target
    # «к возврату»/«выведен» требуют источника вывода для первички LP_RETURN:
    # наш LK_RECEIPT ('us') либо чек ККТ WB ('wb'); без этого return_batch
    # уйдёт в blocked (ревью фичи: «продажа до запуска» вешала код навечно)
    if target in ("PENDING_RETURN", "WITHDRAWN") and not it.withdrawn_by:
        it.withdrawn_by = "us" if km in lk_receipts(db) else "wb"
    # state ДО log_action — паттерн emitter: commit внутри log_action
    # оставляет консистентный снапшот
    it.state = target
    detail = {"from": src, "to": target, "note": body.note}
    log_action(db, source="manual", source_event_id=f"resolve:{int(time.time())}:{km}",
               kind="resolve", km=km, srid="",
               payload={**detail, "withdrawn_by": it.withdrawn_by})
    audit(db, tok.principal_id, "journal.resolve", {"km": km, **detail})
    return {"km": km, "from": src, "to": target}


@router.post("/journal/cis-sync")
def journal_cis_sync(
    body: CisSyncBody,
    tok: PlatformToken = Depends(require_scope("docs:submit")),
    db: Session = Depends(get_db),
):
    """Проверка КИЗ в ЧЗ (cises/info): обновляет колонки cis_*; RETIRED без
    нашей активной претензии → «выведен (WB)». При явных kms возвращает
    свежие карточки позиций (для карточки КМ в консоли)."""
    from marko.connector_mt import manager
    from marko.journal.cis import sync_cis_status
    try:
        res = sync_cis_status(db, body.kms, client=manager.default_client())
    except Exception as e:
        raise HTTPException(502, f"cis sync failed: {e}")
    if body.kms:
        res["items"] = [
            {"km": it.km, "state": it.state, "withdrawn_by": it.withdrawn_by,
             "cis_status": it.cis_status, "cis_product_name": it.cis_product_name,
             "cis_checked_at": it.cis_checked_at}
            for it in db.query(Item).filter(Item.km.in_(body.kms)).all()
        ]
    audit(db, tok.principal_id, "journal.cis_sync",
          {"kms": len(body.kms) if body.kms else "all", **{
              k: v for k, v in res.items()
              if k not in ("statuses", "items")}})   # items несёт datetime
    return res


@router.post("/journal/{km}/withdraw-source")
def journal_withdraw_source(
    km: str,
    body: WithdrawSourceBody,
    tok: PlatformToken = Depends(require_scope("docs:submit")),
    db: Session = Depends(get_db),
):
    """Ручная пометка источника вывода. by='wb' — «код выведен WB» (переводит
    «к выводу» → «выведен»); by='us' — аварийный люк против ложного 'wb'
    (меняет только пометку, состояние не трогает)."""
    it = db.get(Item, km)
    if it is None:
        raise HTTPException(404, "КМ не найден в журнале")
    src = it.state
    if body.by == "wb":
        if it.state != "PENDING_WITHDRAW":
            raise HTTPException(409, f"ожидалось «к выводу», сейчас: {it.state}")
        it.state = "WITHDRAWN"
    else:
        if it.withdrawn_by != "wb":
            raise HTTPException(409, f"пометка не 'wb': {it.withdrawn_by!r}")
        # без нашего LK_RECEIPT ветка 'us' возврата потеряет первичку навсегда
        if km not in lk_receipts(db):
            raise HTTPException(409, "нет нашего LK_RECEIPT — возврат по 'us' "
                                     "уйдёт в вечный blocked")
    it.withdrawn_by = body.by
    log_action(db, source="manual", source_event_id=f"wsrc:{int(time.time())}:{km}",
               kind="resolve", km=km, srid="",
               payload={"by": body.by, "from_state": src})
    audit(db, tok.principal_id, "journal.withdraw_source",
          {"km": km, "by": body.by, "from_state": src})
    return {"km": km, "state": it.state, "withdrawn_by": it.withdrawn_by}


@router.get("/pulse")
def pulse(
    tok: PlatformToken = Depends(require_scope("read")),
    db: Session = Depends(get_db),
):
    """Агрегат для обзора консоли: счётчики журнала/доков/батчей, ближайший
    дедлайн забора возврата, квота goods-return и маркеры живости циклов."""
    stats = dict(db.query(Item.state, func.count()).group_by(Item.state).all())
    docs = {f"{t}:{s}": n for t, s, n in
            db.query(MtDoc.type, MtDoc.status, func.count())
            .group_by(MtDoc.type, MtDoc.status).all()}
    batches = dict(db.query(Batch.status, func.count()).group_by(Batch.status).all())

    nearest, active = "", 0
    for r in db.query(WbReturn).all():
        p = r.payload or {}
        if p.get("completedDt"):
            continue
        dl = _parse_iso(r.expired_dt or "")
        if dl is None:
            continue
        active += 1
        if not nearest or dl < datetime.fromisoformat(nearest):
            nearest = dl.isoformat()

    kv = db.get(PlatformKV, "wb_goodsreturn_usage")
    stamps = [t for t in (kv.value["stamps"] if kv else []) if time.time() - t < 3600 - 120]

    def marker(key: str):
        m = db.get(PlatformKV, key)
        return m.value if m else None

    return {
        "stats": stats,
        "docs": docs,
        "batches": batches,
        "returns": {"active": active, "nearest_deadline": nearest,
                    "pending_return": stats.get("PENDING_RETURN", 0)},
        "quota": {"goods_return_used": len(stamps), "goods_return_limit": 2},
        "markers": {k: marker(k) for k in
                    ("wb_last_poll", "signer_last_seen", "nkmt_loop_last",
                     "returns_loop_last")},
    }


@router.post("/batches/withdraw")
def batches_withdraw(
    body: BatchBody,
    tok: PlatformToken = Depends(require_scope("docs:submit")),
    db: Session = Depends(get_db),
):
    # Пре-флайт ЧЗ: кандидаты «к выводу» проверяются через cises/info; уже
    # «выбывшие» (без нашей активной претензии) переводятся в «вывел WB» и
    # документ не попадают. Цикл до стабилизации: после переводов сборка
    # добирает НОВЫЕ непроверенные строки — проверяем и их, иначе документ
    # соберёт непроверенный хвост (ревью: воспроизводство инцидента 15.09).
    # ЧЗ недоступен — fail-open: собираем как раньше.
    from marko.connector_mt import manager
    from marko.journal.cis import sync_cis_status
    preflight: dict = {"skipped": True}
    try:
        seen: set[str] = set()
        checked = translated = errors = 0
        while True:
            kms = [km for (km,) in db.query(Item.km)
                   .filter_by(state="PENDING_WITHDRAW").order_by(Item.km)
                   .limit(body.limit).all()]
            new = [k for k in kms if k not in seen]
            if not new:
                break
            seen.update(new)
            r = sync_cis_status(db, new, client=manager.default_client())
            checked += r["checked"]; translated += r["translated"]; errors += r["errors"]
            if not r["translated"]:
                break
        preflight = {"checked": checked, "translated": translated, "errors": errors}
    except Exception as e:
        preflight = {"skipped": True, "reason": str(e)[:200]}
    doc_id = withdraw_batch(db, body.inn, body.limit)
    audit(db, tok.principal_id, "batch.withdraw",
          {"inn": body.inn, "result": doc_id, "preflight": {
              k: v for k, v in preflight.items() if k != "statuses"}})
    return {"doc_id": doc_id, "preflight": preflight}


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
    guard_fired = False
    if doc.type == "LK_RECEIPT" and doc.status == "error":
        from marko.emitter.batch import wb_withdraw_guard
        guard_fired = wb_withdraw_guard(db, doc_id, info)
    audit(db, tok.principal_id, "doc.check",
          {"doc_id": doc_id, "mt_status": info.get("status"), "wb_guard": guard_fired})
    return {"status": doc.status, "mt_status": info.get("status"), "wb_guard": guard_fired}


@router.delete("/docs/{doc_id}")
def docs_delete(
    doc_id: int,
    tok: PlatformToken = Depends(require_scope("docs:submit")),
    db: Session = Depends(get_db),
):
    """Удаление ЧЕРНОВИКА с откатом журнала: LK_RECEIPT → позиции «к выводу»,
    LP_RETURN → «к возврату». Позиции, ушедшие дальше по жизни (возврат WB,
    гвард, повторные батчи), не трогаем — только считаем (skipped)."""
    doc = db.get(MtDoc, doc_id)
    if doc is None:
        raise HTTPException(404, "doc not found")
    if doc.status != "draft":
        raise HTTPException(409, f"не черновик: {doc.status}")
    products = (doc.payload.get("products", []) if doc.type == "LK_RECEIPT"
                else doc.payload.get("products_list", []))
    key = "cis" if doc.type == "LK_RECEIPT" else "ki"
    kms = [p[key] for p in products if p.get(key)]
    if doc.type == "LK_RECEIPT":
        # блокируем только если НАШ документ — фактический источник первички
        # возврата (старейший non-error LK_RECEIPT по возвратному КМ); при
        # более старом выводе удаление безвредно (ревью: ложный 409)
        in_return = db.query(Item).filter(
            Item.km.in_(kms),
            Item.state.in_(("PENDING_RETURN", "RETURNED"))).all()
        oldest: dict[str, int] = {}
        for d in db.query(MtDoc).filter(MtDoc.type == "LK_RECEIPT",
                                        MtDoc.status != "error") \
                .order_by(MtDoc.id).all():
            for pr in d.payload.get("products", []):
                oldest.setdefault(pr.get("cis"), d.id)
        blocking = [it.km for it in in_return if oldest.get(it.km) == doc_id]
        if blocking:
            raise HTTPException(
                409, f"{len(blocking)} КМ документа — источник первички "
                     f"LP_RETURN, удаление сломает возврат")
    source_state = "WITHDRAWN" if doc.type == "LK_RECEIPT" else "RETURNED"
    target = "PENDING_WITHDRAW" if doc.type == "LK_RECEIPT" else "PENDING_RETURN"
    reverted = skipped = 0
    for km in kms:
        it = db.get(Item, km)
        # откатываем только то, что поставил этот батч; 'wb' у выведенного
        # кода — реальность ЧЗ, её не переписываем
        if (it is None or it.state != source_state
                or (doc.type == "LK_RECEIPT" and it.withdrawn_by == "wb")):
            skipped += 1
            continue
        it.state = target
        if doc.type == "LK_RECEIPT" and it.withdrawn_by == "us":
            it.withdrawn_by = ""
        log_action(db, source="manual", source_event_id=f"docdel:{doc_id}:{km}",
                   kind="revert", km=km, srid="",
                   payload={"doc_id": doc_id, "from": source_state, "to": target})
        reverted += 1
    db.delete(doc)
    db.commit()
    audit(db, tok.principal_id, "doc.delete",
          {"doc_id": doc_id, "type": doc.type, "reverted": reverted, "skipped": skipped})
    return {"deleted": True, "reverted": reverted, "skipped": skipped}
