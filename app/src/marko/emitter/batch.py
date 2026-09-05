import base64
import json
import logging
from datetime import date

from sqlalchemy.orm import Session, attributes

from marko.journal import log_action
from marko.journal.models import Item
from marko.mt.models import MtDoc
from marko.platform.models import PlatformKV

log = logging.getLogger("marko.emitter")

# прод (live 04.09): document_type=OTHER без primary_document_custom_name → CHECKED_NOT_OK
FALLBACK_CUSTOM_NAME = "Чек дистанционной продажи Wildberries (FBS)"


def _emitter_defaults(db: Session) -> dict:
    kv = db.get(PlatformKV, "emitter_defaults")
    return kv.value if kv else {}


def _payload_json(doc: MtDoc) -> None:
    """product_document_b64 = base64(JSON(payload)); звать ПОСЛЕ финальных мутаций payload."""
    doc.product_document_b64 = base64.b64encode(
        json.dumps(doc.payload, ensure_ascii=False).encode()).decode()


def withdraw_batch(db: Session, inn: str, limit: int = 100) -> int:
    """PENDING_WITHDRAW → один draft LK_RECEIPT на группу has_fiscal (смешанный батч = 2 документа).

    Возвращает id первого созданного документа (0 — нечего выводить).
    """
    defaults = _emitter_defaults(db)
    fias = defaults.get("fias_id") or ""
    items = db.query(Item).filter_by(state="PENDING_WITHDRAW").limit(limit).all()
    if not items:
        return 0
    groups: dict[bool, list[Item]] = {}   # has_fiscal → items
    for it in items:
        p = it.last_event or {}
        groups.setdefault(bool(p.get("fiscal_doc_number")), []).append(it)
    today = date.today().isoformat()
    first_doc_id = 0
    for has_fiscal, grp in groups.items():
        ev = grp[0].last_event or {}
        payload = {
            "inn": inn, "action": "DISTANCE",
            "action_date": ev.get("fiscal_dt") or today,
            "document_type": "RECEIPT" if has_fiscal else "OTHER",
            "document_number": ev.get("fiscal_doc_number") or "",   # WB-<id> подставим после flush
            "document_date": ev.get("fiscal_dt") or today,
            "products": [{"cis": it.km,
                          "product_cost": int((it.last_event or {}).get("price") or 0) * 100}
                         for it in grp],
        }
        if fias:
            payload["fias_id"] = fias   # МОД места отгрузки: прод отклоняет DISTANCE без него (live 04.09)
        if not has_fiscal:
            payload["primary_document_custom_name"] = (
                defaults.get("primary_custom_name") or FALLBACK_CUSTOM_NAME)
        doc = MtDoc(type="LK_RECEIPT", status="draft", payload=payload)
        db.add(doc)
        db.flush()
        if not payload["document_number"]:
            payload["document_number"] = f"WB-{doc.id}"
            attributes.flag_modified(doc, "payload")   # JSON-колонка не видит in-place мутацию
        _payload_json(doc)
        for it in grp:
            log_action(db, source="emitter",
                       source_event_id=f"withdraw:{doc.id}:{it.km}",
                       kind="withdraw", km=it.km, srid="", payload={"doc_id": doc.id})
            it.state = "WITHDRAWN"   # ponytail: фаза 1 — ручная подача; фаза 2 = после CHECKED_OK
        if not first_doc_id:
            first_doc_id = doc.id
    if len(groups) > 1:
        log.warning("withdraw_batch: смешанный батч разбит на %d документа (fiscal/без fiscal)", len(groups))
    db.commit()
    return first_doc_id


def return_batch(db: Session, inn: str, limit: int = 100) -> tuple[int, int]:
    """PENDING_RETURN → draft LP_RETURN; возвращает (создано_документов, blocked_no_receipt).

    Первичка: последний вывод КМ ищется по payload.products[].cis во всех LK_RECEIPT
    (status != 'error', первое вхождение выигрывает). КМ без вывода остаётся PENDING_RETURN.
    """
    items = db.query(Item).filter_by(state="PENDING_RETURN").limit(limit).all()
    if not items:
        return 0, 0
    receipts: dict[str, tuple[str, str]] = {}   # km -> (document_number, document_date)
    for d in db.query(MtDoc).filter(MtDoc.type == "LK_RECEIPT",
                                    MtDoc.status != "error") \
            .order_by(MtDoc.id).all():   # первое вхождение КМ (старейший вывод) выигрывает
        for pr in d.payload.get("products", []):
            receipts.setdefault(pr["cis"],
                                (d.payload.get("document_number", ""),
                                 d.payload.get("document_date", "")))
    products, blocked = [], 0
    for it in items:
        rc = receipts.get(it.km)
        if not rc:
            blocked += 1
            continue
        products.append({"ki": it.km,
                         "primary_document_type": "RECEIPT",
                         "primary_document_number": rc[0],
                         "primary_document_date": rc[1]})
    if not products:
        if blocked:
            log.warning("return_batch: %d КМ без вывода остались PENDING_RETURN", blocked)
        return 0, blocked
    payload = {"trade_participant_inn": inn, "return_type": "REMOTE_SALE_RETURN",
               "paid": True, "primary_document_type": "RECEIPT",
               "primary_document_number": products[0]["primary_document_number"],
               "primary_document_date": products[0]["primary_document_date"],
               "products_list": products}
    doc = MtDoc(type="LP_RETURN", status="draft", payload=payload)
    db.add(doc)
    db.flush()
    _payload_json(doc)
    ret_kms = {p["ki"] for p in products}
    for it in items:
        if it.km in ret_kms:
            log_action(db, source="emitter",
                       source_event_id=f"return:{doc.id}:{it.km}",
                       kind="return_apply", km=it.km, srid="", payload={"doc_id": doc.id})
            it.state = "RETURNED"   # ponytail: фаза 1 — ручная подача; фаза 2 = после CHECKED_OK
    if blocked:
        log.warning("return_batch: %d КМ без вывода остались PENDING_RETURN", blocked)
    db.commit()
    return 1, blocked


def to_csv(db: Session, doc_id: int) -> str:
    """CSV-выгрузка для ручной подачи (шапка cis;product_cost для RECEIPT; ki для RETURN)."""
    doc = db.get(MtDoc, doc_id)
    if doc.type == "LK_RECEIPT":
        lines = ["cis;product_cost"] + [f"{p['cis']};{p['product_cost']}" for p in doc.payload["products"]]
    else:
        lines = ["ki"] + [p["ki"] for p in doc.payload["products_list"]]
    return "\n".join(lines)
