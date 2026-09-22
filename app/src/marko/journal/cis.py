"""Синхронизация статусов КИЗ с Честным ЗНАКом (True API /cises/info).

ЧЗ — источник правды о фактическом состоянии кода; журнал — очередь наших
обязательств. Синк обновляет колонки cis_* и переводит коды, которые ЧЗ уже
считает выведенными («вывел WB» по чеку ККТ), — но только при отсутствии
нашей активной претензии: RETIRED может быть следствием НАШЕГО раннего
вывода при незакрытом цикле перепродажи (вывод → возврат не подан →
повторная продажа), и пометка 'wb' там была бы ложной (ревью фичи, P0).
"""
from datetime import datetime

from sqlalchemy.orm import Session

from marko.connector_mt import manager
from marko.journal import log_action
from marko.journal.models import Item
from marko.journal.state import TRANSLATE_ON_RETIRED
from marko.mt.models import MtDoc

SUBMITTED = ("submitted", "checked_ok")


def active_claims(db: Session) -> set[str]:
    """КМ с нашей активной претензией на вывод: поданный LK_RECEIPT, после
    которого не было поданного LP_RETURN (цикл вывод→возврат не закрыт)."""
    claims: set[str] = set()
    for d in db.query(MtDoc).filter(MtDoc.status.in_(SUBMITTED)).order_by(MtDoc.id).all():
        if d.type == "LK_RECEIPT":
            claims.update(pr.get("cis") for pr in d.payload.get("products", []))
        elif d.type == "LP_RETURN":
            for pr in d.payload.get("products_list", []):
                claims.discard(pr.get("ki"))
    return claims


def sync_cis_status(db: Session, kms: list[str] | None = None,
                    client=None, snapshot: bool = False) -> dict:
    """Проверить КИЗ в ЧЗ и обновить журнал; kms=None → все позиции.

    RETIRED + состояние из TRANSLATE_ON_RETIRED (штатные «к выводу» и
    легаси-аномалии перепродажи) + нет активной претензии → «выведен (WB)»
    с событием cz_retired:{km} (идемпотентно: гвард по состоянию + uq).
    Остальные случаи — только колонки: WITHDRAWN/'us' не перепомечаем (наш
    вывод мог дойти между синками), PENDING_RETURN с retired — норма (код
    ждёт возврата в оборот). Поэлементные ошибки ЧЗ (HTTP 200 +
    errorMessage) позицию не трогают — неудачная попытка не наблюдение.
    Явные kms с кодами ВНЕ журнала — разовый срез в res["infos"] без
    записи позиций: проверка «чужого» кода из трассировки (empty-state).
    snapshot=True (только с явными kms) — res["cis"][km] с ПОЛНЫМ cisInfo
    (даты эмиссии/ввода, производитель, декларация) для трассировки.
    Ответ: {checked, translated, errors, statuses[, infos][, cis]}.
    """
    q = db.query(Item)
    items = q.filter(Item.km.in_(kms)).all() if kms else q.all()
    res = {"checked": 0, "translated": 0, "errors": 0, "statuses": {}}
    known = [it.km for it in items]
    missing = [k for k in (kms or []) if k not in set(known)]
    if not known and not missing:
        return res
    ask = known + missing
    infos = manager.cises_info(db, ask, client=client)
    if len(infos) != len(ask):
        # соответствие запрос↔ответ — рассинхрон длин означает потерю элементов
        raise RuntimeError(f"cises/info: {len(infos)} ответов на {len(ask)} КМ")
    # ЧЗ эхает запрошенный код (cisInfo.cis/requestedCis) — матчим по эху,
    # позиция только фолбэк: перестановка ответа не должна молча путать
    # статусы (ревью: единственный механизм «тихой порчи» журнала)
    by_cis: dict[str, dict] = {}
    for e in infos:
        info = e.get("cisInfo") or {}
        echo = info.get("cis") or e.get("cis") or info.get("requestedCis")
        if echo:
            by_cis[str(echo)] = e
    pos_map = {k: i for i, k in enumerate(ask)}

    def entry_of(km: str) -> dict:
        return by_cis.get(km) or infos[pos_map[km]]

    claims = active_claims(db)
    now = datetime.now()
    for it in items:
        entry = entry_of(it.km)
        info = (entry or {}).get("cisInfo") or {}
        status = str(info.get("status") or "").lower()
        if (entry or {}).get("errorMessage") or not status:
            res["errors"] += 1
            continue
        it.cis_status = status[:32]
        it.cis_product_name = str(info.get("productName") or "")[:256]
        it.cis_checked_at = now
        res["statuses"][status] = res["statuses"].get(status, 0) + 1
        res["checked"] += 1
        if (status == "retired" and it.state in TRANSLATE_ON_RETIRED
                and it.km not in claims):
            # state ДО log_action (паттерн emitter): каждый commit —
            # консистентный снапшот; from — след, из чего вычистили
            from_state = it.state
            it.state = "WITHDRAWN"
            it.withdrawn_by = "wb"
            log_action(db, source="cz", source_event_id=f"cz_retired:{it.km}",
                       kind="withdraw", km=it.km, srid="",
                       payload={"by": "wb_kkt", "via": "cises_info", "from": from_state,
                                "withdrawReason": info.get("withdrawReason")})
            res["translated"] += 1
            continue
        db.commit()
    if missing:
        # срез по кодам без позиции: только наблюдение, журнал не трогаем
        out = []
        for km in missing:
            entry = entry_of(km)
            info = (entry or {}).get("cisInfo") or {}
            status = str(info.get("status") or "").lower()
            if (entry or {}).get("errorMessage"):
                out.append({"km": km, "error": str(entry["errorMessage"])[:256]})
            elif status:
                out.append({"km": km, "status": status[:32],
                            "product_name": str(info.get("productName") or "")[:256]})
            else:
                out.append({"km": km, "error": "пустой ответ ЧЗ"})
        res["infos"] = out
    if snapshot and kms:
        res["cis"] = {}
        for km in ask:
            entry = entry_of(km)
            if (entry or {}).get("errorMessage"):
                res["cis"][km] = {"error": str(entry["errorMessage"])[:256]}
            else:
                res["cis"][km] = (entry or {}).get("cisInfo") or {"error": "пустой ответ ЧЗ"}
    return res
