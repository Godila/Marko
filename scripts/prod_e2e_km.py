"""E2E на ПРОДЕ ГИС МТ: полный круг по одному КМ через штатный конвейер платформы.

mt.docs draft -> manager.submit_doc (прод-signer doc_sign) -> CHECKED_OK ->
LP_RETURN draft -> submit -> CHECKED_OK. Код возвращается в состояние «в обороте».

Запуск на VM (КМ одной строкой):
    cat /root/prod_e2e_km.py | docker exec -i marko-api-1 python - "$(head -1 /root/tk-km.txt)"

Пишет в прод-БД только mt.docs (штатная таблица документов) и sign.tasks.
"""
import base64
import json
import sys
import time
from datetime import datetime

sys.path.insert(0, "/app/src")

from marko.connector_mt import manager
from marko.connector_mt.client import MtClient
from marko.db import SessionLocal
from marko.mt.models import MtDoc
from marko.settings import settings

POLL_SEC = 900
POLL_INTERVAL = 10


def short_cis(km_line: str) -> str:
    km = km_line.strip()
    short = km.split("\x1d")[0]
    if len(short) > 31:
        short = short[:31]
    if len(short) != 31 or not short.startswith("01"):
        raise SystemExit(f"bad cis: {short!r} (len={len(short)})")
    return short


def new_doc(db, doc_type: str, payload: dict) -> int:
    doc = MtDoc(type=doc_type, status="draft", payload=payload)
    doc.product_document_b64 = base64.b64encode(
        json.dumps(payload, ensure_ascii=False).encode()).decode()
    db.add(doc)
    db.commit()
    return doc.id


def wait_ok(db, doc_id: int, label: str) -> dict:
    from marko.connector_mt.client import MtHttpError
    deadline = time.monotonic() + POLL_SEC
    last = None
    while time.monotonic() < deadline:
        try:
            info = manager.check_doc(db, doc_id)
        except MtHttpError as e:
            if e.status == 404:   # документ ещё индексируется после create
                time.sleep(POLL_INTERVAL)
                continue
            raise
        st = info.get("status") if isinstance(info, dict) else None
        if st != last:
            last = st
            print(f"[poll] {label}: {st}", flush=True)
        if st in ("CHECKED_OK", "REJECTED", "ERROR", "CHECKED_NOT_OK", "CANCELLED"):
            if st != "CHECKED_OK":
                print(f"[poll] {label} тело: {json.dumps(info, ensure_ascii=False)[:700]}")
            return info
        time.sleep(POLL_INTERVAL)
    raise SystemExit(f"{label}: статус {last} за {POLL_SEC}s не дошёл до финального")


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('нет КМ: python - "<КМ>" [--fias <guid>]')
    fias = ""
    if "--fias" in sys.argv:
        fias = sys.argv[sys.argv.index("--fias") + 1]
    cis = short_cis([a for a in sys.argv[1:] if a != "--fias" and (not fias or a != fias)][0])
    now = datetime.now()
    num = f"WB-PROD-E2E-{now:%Y%m%d%H%M%S}"

    receipt = {
        "inn": settings.mt_inn, "action": "DISTANCE",
        "action_date": now.date().isoformat(),
        "document_type": "OTHER",
        "primary_document_custom_name": "Чек дистанционной продажи (тест платформы)",
        "document_number": num, "document_date": now.date().isoformat(),
        "products": [{"cis": cis, "product_cost": 150000}],
    }
    if fias:
        receipt["fias_id"] = fias   # место деятельности (МОД) — GUID ФИАС из ЛК ЧЗ
    ret = {
        "trade_participant_inn": settings.mt_inn, "return_type": "REMOTE_SALE_RETURN",
        "paid": True, "primary_document_type": "RECEIPT",
        "primary_document_number": num, "primary_document_date": now.date().isoformat(),
        "products_list": [{"ki": cis, "primary_document_type": "RECEIPT",
                           "primary_document_number": num,
                           "primary_document_date": now.date().isoformat()}],
    }
    print(f"[data] cis: {cis}")
    print(f"[data] LK_RECEIPT: {json.dumps(receipt, ensure_ascii=False)}")

    with SessionLocal() as db:
        # 1) вывод из оборота (продажа)
        d1 = new_doc(db, "LK_RECEIPT", receipt)
        ext1 = manager.submit_doc(db, d1)
        print(f"[submit] LK_RECEIPT doc_id={d1} -> {ext1}", flush=True)
        i1 = wait_ok(db, d1, "LK_RECEIPT")
        if i1.get("status") != "CHECKED_OK":
            print("STOP: LK_RECEIPT не прошёл")
            return

        # 2) возврат (код обратно в оборот)
        d2 = new_doc(db, "LP_RETURN", ret)
        ext2 = manager.submit_doc(db, d2)
        print(f"[submit] LP_RETURN doc_id={d2} -> {ext2}", flush=True)
        i2 = wait_ok(db, d2, "LP_RETURN")

        print(f"\nPROD E2E RESULT: withdraw={i1.get('status')} return={i2.get('status')} "
              f"(doc_ids {d1}/{d2}, uuid {ext1}/{ext2})")


if __name__ == "__main__":
    main()
