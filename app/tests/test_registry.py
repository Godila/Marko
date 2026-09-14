"""Реестр wb.orders: нормализация order_doc (суффикс-дрейф инцидента 09.2026),
апсерт снапшота заказов с дедупом позиций, срезы документов для классификации."""
from marko.connector_wb.models import WbOrder
from marko.connector_wb.registry import non_fbs_docs, order_doc, registry_docs, upsert_orders


def test_order_doc_strips_position_tail():
    # ядро инцидента: эксайз и orders дают разные хвосты '.n.m' одному документу
    a = order_doc("eAL.rfa7f255e5c1646ef97d7f483176a2908.3.0")
    b = order_doc("eAL.rfa7f255e5c1646ef97d7f483176a2908.2.0")
    assert a == b == "eAL.rfa7f255e5c1646ef97d7f483176a2908"


def test_order_doc_keeps_legacy_and_bare_values():
    # в фикстуре прода есть голые uuid и числовые rid — они не «режутся»
    assert order_doc("a65d4843837f4e20ae3d1d95067101e5") == "a65d4843837f4e20ae3d1d95067101e5"
    assert order_doc("12345") == "12345"
    assert order_doc("") == ""
    x = order_doc("eP.r3e845557a4344d4989228529be6b9fe7.19.0")
    assert order_doc(x) == x  # идемпотентность


def _row(rid, dt="fbs", nm=1, created="2026-09-01T00:00:00Z"):
    return {"rid": rid, "deliveryType": dt, "nmId": nm, "createdAt": created}


def test_upsert_orders_dedups_positions_and_updates(db):
    # два rid одного заказа (.1.0/.2.0) в одной партии = одна строка реестра
    n = upsert_orders(db, [_row("eAL.rabc.1.0"), _row("eAL.rabc.2.0"), _row("eB.rdef.0.0")])
    assert n == 2
    assert db.query(WbOrder).count() == 2
    # повторный апсерт: тип доставки обновился, строка не задублировалась
    upsert_orders(db, [_row("eAL.rabc.0.0", dt="fbo")])
    assert db.get(WbOrder, "eAL.rabc").delivery_type == "fbo"
    assert db.query(WbOrder).count() == 2


def test_registry_slices(db):
    upsert_orders(db, [_row("eA.ra.0.0", dt="fbs"), _row("eB.rb.0.0", dt="fbo"),
                       _row("eC.rc.0.0", dt="")])
    assert registry_docs(db) == {"eA.ra", "eB.rb", "eC.rc"}
    # не-FBS = только явные другие схемы; пустой тип (поле пропало из API) не skip
    assert non_fbs_docs(db) == {"eB.rb"}
