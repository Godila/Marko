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
                    client=None) -> dict:
    """Проверить КИЗ в ЧЗ и обновить журнал; kms=None → все позиции.

    RETIRED + PENDING_WITHDRAW + нет активной претензии → «выведен (WB)»
    с событием cz_retired:{km} (идемпотентно: гвард по состоянию + uq).
    Остальные случаи — только колонки: WITHDRAWN/'us' не перепомечаем (наш
    вывод мог дойти между синками), PENDING_RETURN с retired — норма (код
    ждёт возврата в оборот). Поэлементные ошибки ЧЗ (HTTP 200 +
    errorMessage) позицию не трогают — неудачная попытка не наблюдение.
    Ответ: {checked, translated, errors, statuses}.
    """
    q = db.query(Item)
    items = q.filter(Item.km.in_(kms)).all() if kms else q.all()
    res = {"checked": 0, "translated": 0, "errors": 0, "statuses": {}}
    if not items:
        return res
    infos = manager.cises_info(db, [it.km for it in items], client=client)
    if len(infos) != len(items):
        # соответствие запрос↔ответ — рассинхрон длин означает потерю элементов
        raise RuntimeError(f"cises/info: {len(infos)} ответов на {len(items)} КМ")
    # ЧЗ эхает запрошенный код (cisInfo.cis/requestedCis) — матчим по эху,
    # позиция только фолбэк: перестановка ответа не должна молча путать
    # статусы (ревью: единственный механизм «тихой порчи» журнала)
    by_cis: dict[str, dict] = {}
    for e in infos:
        info = e.get("cisInfo") or {}
        echo = info.get("cis") or e.get("cis") or info.get("requestedCis")
        if echo:
            by_cis[str(echo)] = e
    claims = active_claims(db)
    now = datetime.now()
    for pos, it in enumerate(items):
        entry = by_cis.get(it.km) or infos[pos]
        info = entry.get("cisInfo") or {}
        status = str(info.get("status") or "").lower()
        if entry.get("errorMessage") or not status:
            res["errors"] += 1
            continue
        it.cis_status = status[:32]
        it.cis_product_name = str(info.get("productName") or "")[:256]
        it.cis_checked_at = now
        res["statuses"][status] = res["statuses"].get(status, 0) + 1
        res["checked"] += 1
        if (status == "retired" and it.state == "PENDING_WITHDRAW"
                and it.km not in claims):
            # state ДО log_action (паттерн emitter): каждый commit —
            # консистентный снапшот
            it.state = "WITHDRAWN"
            it.withdrawn_by = "wb"
            log_action(db, source="cz", source_event_id=f"cz_retired:{it.km}",
                       kind="withdraw", km=it.km, srid="",
                       payload={"by": "wb_kkt", "via": "cises_info",
                                "withdrawReason": info.get("withdrawReason")})
            res["translated"] += 1
            continue
        db.commit()
    return res
