"""E2E в тест-контуре (песочнице) ЧЗ: auth -> LK_RECEIPT -> CHECKED_OK -> LP_RETURN -> CHECKED_OK.

Запуск на VM (КМ целиком из файла, одной строкой):
    cat /root/tk_e2e.py | docker exec -i deploy-api-1 python - "$(cat /root/tk-km.txt)" [--full] [--dry]

- КМ из argv[1]: полный (01..\\x1d21..\\x1d91..\\x1d92..) или короткий (01+GTIN+21+serial);
  по умолчанию в документ идёт КОРОТКИЙ cis (31 симв, как из WB excise-report) —
  это и есть проверяемая гипотеза; --full подставляет весь КМ целиком (диагностика).
- --dry: только парсинг КМ и печать payload'ов, без сети и задач подписи.
- Скрипт НЕ пишет в прод-БД ничего, кроме задач подписи (sign.tasks, безвредно);
  токен ЧЗ живёт в памяти процесса, kv не трогаем.
"""
import base64
import json
import sys
import time
from datetime import datetime

sys.path.insert(0, "/app/src")

from mpmt.connector_mt.client import MtClient
from mpmt.connector_mt.manager import _sign_via_gateway
from mpmt.db import SessionLocal
from mpmt.settings import settings

TK3 = "https://markirovka.sandbox.crptech.ru/api/v3/true-api"
TK4 = "https://markirovka.sandbox.crptech.ru/api/v4/true-api"
POLL_SEC = 900          # CHECKED_OK в ТК обычно минуты
POLL_INTERVAL = 10


def short_cis(km_line: str) -> str:
    km = km_line.strip()
    short = km.split("\x1d")[0]
    if len(short) > 31:
        short = short[:31]
    if len(short) != 31 or not short.startswith("01"):
        raise SystemExit(f"bad cis: {short!r} (len={len(short)}), ожидается 01+GTIN(14)+21+serial(13)")
    return short


def tk_token(c: MtClient) -> str:
    pair = c.auth_key()
    with SessionLocal() as db:
        sig = _sign_via_gateway(db, "auth_sign", {"data": pair["data"]})
    resp = c.sign_in(pair["uuid"], sig, settings.mt_inn)
    token = resp.get("token") or resp.get("uuidToken")
    if not token:
        raise SystemExit(f"no token: {json.dumps(resp)[:300]}")
    print(f"[auth] OK, expire={resp.get('expireDate')}")
    return token


def submit(c: MtClient, token: str, doc_type: str, payload: dict) -> str:
    b64 = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode()
    with SessionLocal() as db:
        sig = _sign_via_gateway(db, "doc_sign", {"data_b64": b64})
    uuid = c.create_doc_signed(token, doc_type, b64, sig)
    print(f"[submit] {doc_type} -> {uuid}")
    return uuid


def wait_ok(c: MtClient, token: str, doc_uuid: str, label: str) -> dict:
    deadline = time.monotonic() + POLL_SEC
    last = None
    while time.monotonic() < deadline:
        info = c.doc_info(token, doc_uuid)
        if info.get("status") != last:
            last = info.get("status")
            print(f"[poll] {label}: {last}")
        if last in ("CHECKED_OK", "REJECTED", "ERROR", "CANCELLED"):
            if last != "CHECKED_OK":
                print(f"[poll] {label} тело: {json.dumps(info, ensure_ascii=False)[:600]}")
            return info
        time.sleep(POLL_INTERVAL)
    raise SystemExit(f"{label}: статус {last} за {POLL_SEC}s не дошёл до финального")


def main() -> None:
    args = [a for a in sys.argv[1:]]
    full = "--full" in args
    dry = "--dry" in args
    km_lines = [a for a in args if not a.startswith("--")]
    if not km_lines:
        raise SystemExit("нет КМ: python - \"<КМ>\" [--full] [--dry]")
    # делим строго по \n: splitlines() рвёт и по GS (\x1d) внутри полного КМ
    kms = [l.strip() for line in km_lines
           for l in line.replace("\r\n", "\n").split("\n") if l.strip()]
    cis_list = [k if full else short_cis(k) for k in kms]

    now = datetime.now()
    receipt = {
        "inn": settings.mt_inn, "action": "DISTANCE",
        "action_date": now.date().isoformat(),
        "document_type": "OTHER",
        "document_number": f"WB-TK-{now:%Y%m%d%H%M%S}",
        "document_date": now.date().isoformat(),
        "products": [{"cis": cis, "product_cost": 150000} for cis in cis_list],
    }
    ret = {
        "trade_participant_inn": settings.mt_inn, "return_type": "REMOTE_SALE_RETURN",
        "paid": True, "primary_document_type": "RECEIPT",
        "primary_document_number": receipt["document_number"],
        "primary_document_date": receipt["document_date"],
        "products_list": [{"ki": cis, "primary_document_type": "RECEIPT",
                           "primary_document_number": receipt["document_number"],
                           "primary_document_date": receipt["document_date"]}
                          for cis in cis_list],
    }
    print(f"[data] cis: {cis_list}")
    print(f"[data] LK_RECEIPT payload: {json.dumps(receipt, ensure_ascii=False)}")
    print(f"[data] LP_RETURN payload: {json.dumps(ret, ensure_ascii=False)}")
    if dry:
        print("[dry] сеть не трогаем, выход")
        return

    c = MtClient(base_v3=TK3, base_v4=TK4)
    token = tk_token(c)
    uuid1 = submit(c, token, "LK_RECEIPT", receipt)
    wait_ok(c, token, uuid1, "LK_RECEIPT")
    uuid2 = submit(c, token, "LP_RETURN", ret)
    wait_ok(c, token, uuid2, "LP_RETURN")
    print(f"\nE2E OK: withdraw={uuid1} return={uuid2}")


if __name__ == "__main__":
    main()
