"""Клиентские возвраты (sales R-строки): детектор + правила журнала.

Доктрина (discovery 23.09): сентябрьский FBS-контур — WB-чеки без КМ, вывод
только наш LK_RECEIPT; возврат покупателя виден ТОЛЬКО в финансах (R-строка).
Правила: R по непроведённому выводу — снять обязательство (код в обороте,
товар на фулфилменте, продажа отменена); R после НАШЕГО вывода — к возврату
в ЧЗ (LP_RETURN); вывод WB ('wb') — зона WB, только наблюдение.
"""
from marko.connector_wb.client_returns import ingest_client_returns
from marko.connector_wb.models import WbClientReturn
from marko.journal import apply_event
from marko.journal.models import Event, Item

KM = "0104630520675967215ClRet01T"
KM2 = "0104630520675967215ClRet02T"


def _sale(db, km=KM, ev="e1", srid="eAW.aaa111.0.0", fiscal="2026-09-16"):
    apply_event(db, source="wb_excise", source_event_id=ev, kind="sale", km=km,
                srid=srid, payload={"price": 3123, "fiscal_dt": fiscal})


def _r(sale_id="R1", srid="eAW.aaa111.0.0", wh="Крыловская", date="2026-09-20T10:00:00"):
    return {"saleID": sale_id, "srid": srid, "date": date,
            "warehouseName": wh, "nmID": 412477053, "subject": "Худи",
            "price": 3123, "finishedPrice": -3123}


def test_return_before_our_withdraw_releases_obligation(db):
    """R-возврат по непроведённому выводу: продажа отменена, код в обороте —
    обязательство снимается (RETURNED), событие финансового возврата в журнале."""
    _sale(db)
    stats = ingest_client_returns(db, [_r()])
    assert stats["applied"] == 1
    it = db.get(Item, KM)
    assert it.state == "RETURNED"
    ev = db.query(Event).filter_by(kind="client_return").one()
    assert ev.source == "wb_sales" and ev.source_event_id == "R1:eAW.aaa111.0.0"
    assert ev.payload["warehouse"] == "Крыловская"
    assert db.get(WbClientReturn, "eAW.aaa111.0.0").km == KM


def test_idempotent_reringest(db):
    _sale(db)
    s1 = ingest_client_returns(db, [_r()])
    s2 = ingest_client_returns(db, [_r()])
    assert s1["applied"] == 1 and s2["applied"] == 0
    assert db.query(Event).filter_by(kind="client_return").count() == 1


def test_r_wait_for_lagging_sale(db):
    """R приезжает раньше эксайз-строки продажи: стейджинг без км; когда
    продажа доезжает — повторный проход применяет возврат."""
    stats = ingest_client_returns(db, [_r()])
    assert stats["applied"] == 0 and stats["staged"] == 1
    row = db.get(WbClientReturn, "eAW.aaa111.0.0")
    assert row.km is None and row.applied_at is None
    _sale(db)                                   # эксайз доехал позже
    s2 = ingest_client_returns(db, [])          # новых R нет — только матчинг
    assert s2["applied"] == 1
    assert db.get(Item, KM).state == "RETURNED"


def test_return_after_our_withdraw_goes_pending_return(db):
    """R после НАШЕГО вывода: код retired, товар на фулфилменте — возврат
    в ЧЗ (LP_RETURN), withdrawn_by сохраняется для первички return_batch."""
    _sale(db, km=KM2, ev="w1", srid="eAW.bbb222.0.0")
    it = db.get(Item, KM2)
    it.state, it.withdrawn_by = "WITHDRAWN", "us"
    db.commit()
    ingest_client_returns(db, [_r(sale_id="R2", srid="eAW.bbb222.0.0")])
    it = db.get(Item, KM2)
    assert it.state == "PENDING_RETURN" and it.withdrawn_by == "us"


def test_wb_withdrawn_return_only_observed(db):
    """Вывод сделал WB ('wb', августовские чеки / FBO-зона): R не двигает
    состояние — код в зоне ответственности WB, пишем только след."""
    _sale(db, km=KM2, ev="w2", srid="eAW.ccc333.0.0")
    it = db.get(Item, KM2)
    it.state, it.withdrawn_by = "WITHDRAWN", "wb"
    db.commit()
    ingest_client_returns(db, [_r(sale_id="R3", srid="eAW.ccc333.0.0")])
    assert db.get(Item, KM2).state == "WITHDRAWN"
    ev = db.query(Event).filter_by(kind="client_return").one()
    assert ev.payload["observed_only"] is True


def test_fbo_warehouse_pending_withdraw_also_released(db):
    """Хвост FBO (склад WB РФ) в очереди вывода: R снимает ложное
    обязательство так же — живые 4 кода очереди 23.09."""
    _sale(db, ev="f1", srid="eAW.ddd444.0.0")
    ingest_client_returns(db, [_r(sale_id="R4", srid="eAW.ddd444.0.0",
                                  wh="Склад WB РФ")])
    assert db.get(Item, KM).state == "RETURNED"


def test_resale_cycle_closes(db):
    """Возврат → повторная продажа: цикл замыкается RETURNED→к выводу."""
    _sale(db)
    ingest_client_returns(db, [_r()])
    apply_event(db, source="wb_excise", source_event_id="e2", kind="sale", km=KM,
                srid="eAW.eee555.0.0", payload={"price": 2500})
    assert db.get(Item, KM).state == "PENDING_WITHDRAW"


def test_multi_position_order_partial_return(db):
    """Многопозиционный заказ (две позиции = два КМ, один order_doc): одна
    R-строка забирает СВОЙ КМ — детерминированно, чужое обязательство не
    снимается (P1 ревью 23.09: «последний выиграл» без порядка)."""
    _sale(db, ev="m1", srid="eAW.mm.1.0")
    _sale(db, km=KM2, ev="m2", srid="eAW.mm.2.0")
    ingest_client_returns(db, [_r(sale_id="R7", srid="eAW.mm.1.0")])
    assert db.get(Item, KM).state == "RETURNED"        # возвращённая позиция
    assert db.get(Item, KM2).state == "PENDING_WITHDRAW"  # проданная — нет
    # вторая позиция вернулась позже — свой КМ, свой ключ события
    ingest_client_returns(db, [_r(sale_id="R8", srid="eAW.mm.2.0")])
    assert db.get(Item, KM2).state == "RETURNED"
    assert db.query(Event).filter_by(kind="client_return").count() == 2
