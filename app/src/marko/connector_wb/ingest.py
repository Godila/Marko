from sqlalchemy.orm import Session

from marko.connector_wb.registry import order_doc
from marko.journal import apply_event, log_action


def excise_rows_to_events(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        kind = "sale" if row["operation_type_id"] == 1 else "return"
        out.append({
            "source": "wb_excise",
            "source_event_id": f"{row['srid']}:{row['excise_short']}:{row['operation_type_id']}",
            "kind": kind,
            "km": row["excise_short"],
            "srid": row["srid"],
            "payload": row,
        })
    return out


# Контракт классификации (инцидент 09.2026): skip_fbw — ТОЛЬКО когда order_doc(srid)
# в реестре wb.orders явно не-FBS. Прежняя схема «srid ∉ текущего снапшота orders()»
# отправляла наши FBS-продажи в skip_fbw: суффикс позиции '.n.m' расходится между
# эксайзом и orders (0/131 совпадений), а выкупленный заказ исчезает из снапшота
# раньше приезда эксайз-строки (128/131 вне снапшота). Документ вне реестра = наш
# FBS (счётчик fbs_unknown — трипваер); реестр прогревается upsert_orders ДО ingest
# в poll и ежечасно в воркере. Настоящая FBW-строка, попавшая в вывод, поглощается
# wb_withdraw_guard («уже выбыл» → withdrawn_by='wb').
def ingest_excise(db: Session, rows: list[dict], *, fbw_docs: set[str],
                  known_docs: set[str]) -> dict:
    stats = {"sale": 0, "return": 0, "skipped_fbw": 0, "duplicates": 0, "fbs_unknown": 0}
    events = excise_rows_to_events(rows)
    # порядок применения — по fiscal_dt: WB выдаёт строки не по датам (возвраты
    # отстают от продаж на 0–2 дня), применение в порядке выдачи порождает
    # ложные ANOMALY_NO_RECEIPT/UNKNOWN; сортировка восстанавливает хронологию.
    # Бездатовые строки — в конец (как NULLS LAST в repair)
    events.sort(key=lambda ev: str(ev["payload"].get("fiscal_dt") or "9999-12-31"))
    for ev in events:
        if order_doc(ev["srid"]) in fbw_docs:
            # FBW — вне контура FBS: только аудит-событие, позиция в журнале
            # НЕ создаётся (журнал = жизненный цикл наших КМ)
            created = log_action(db, source="wb_excise",
                                 source_event_id=ev["source_event_id"],
                                 kind="skip_fbw", km=ev["km"], srid=ev["srid"],
                                 payload=ev["payload"])
            if created:
                stats["skipped_fbw"] += 1
            else:
                stats["duplicates"] += 1
            continue
        _, created = apply_event(db, **ev)
        if created:
            stats[ev["kind"]] += 1
            if order_doc(ev["srid"]) not in known_docs:
                stats["fbs_unknown"] += 1
        else:
            stats["duplicates"] += 1
    return stats
