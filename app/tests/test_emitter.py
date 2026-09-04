from mpmt.journal import apply_event
from mpmt.emitter.batch import withdraw_batch, return_batch
from mpmt.mt.models import MtDoc

INN = "090201471350"
SALE = {"excise_short": "0104630520676025215DDDDDDD", "srid": "s1", "operation_type_id": 1,
        "price": 1793, "nm_id": 1, "fiscal_dt": "2026-06-01", "fiscal_doc_number": "42"}

def _sale(db, km, **over):
    row = dict(SALE, excise_short=km, **over)
    apply_event(db, source="wb_excise", source_event_id=f"{row['srid']}:{km}:1",
                kind="sale", km=km, srid=row["srid"], payload=row)

def test_withdraw_batch(db):
    _sale(db, "0104630520676025215DDDDDDD")
    doc_id = withdraw_batch(db, INN)
    doc = db.get(MtDoc, doc_id)
    assert doc.type == "LK_RECEIPT"
    p = doc.payload
    assert p["action"] == "DISTANCE" and p["document_type"] == "RECEIPT"
    assert p["products"][0] == {"cis": "0104630520676025215DDDDDDD", "product_cost": 179300}
    from mpmt.journal.models import Item
    assert db.get(Item, "0104630520676025215DDDDDDD").state == "WITHDRAWN"

def test_withdraw_fias_and_custom_name(db):
    """kv emitter_defaults: fias_id в payload (задан — всегда), custom_name — только при OTHER."""
    from mpmt.platform.models import PlatformKV
    db.add(PlatformKV(key="emitter_defaults", value={
        "fias_id": "b944722c-3080-4a72-b9a5-57e11533083c",
        "primary_custom_name": "Чек дистанционной продажи Wildberries"}))
    db.commit()
    _sale(db, "0104630520676025215FISC0001")            # фискальная → RECEIPT
    d1 = withdraw_batch(db, INN)
    p1 = db.get(MtDoc, d1).payload
    assert p1["fias_id"] == "b944722c-3080-4a72-b9a5-57e11533083c"
    assert "primary_document_custom_name" not in p1     # при RECEIPT поле строго отсутствует
    apply_event(db, source="wb_excise", source_event_id="nf2:1", kind="sale",
                km="0104630520676025215NOFI0002", srid="nf2", payload={"price": 100})
    d2 = withdraw_batch(db, INN)
    p2 = db.get(MtDoc, d2).payload
    assert p2["document_type"] == "OTHER"
    assert p2["fias_id"] == "b944722c-3080-4a72-b9a5-57e11533083c"
    assert p2["primary_document_custom_name"] == "Чек дистанционной продажи Wildberries"


def test_withdraw_without_kv_defaults(db):
    """Без kv: fias_id не подставляется; при OTHER custom_name — непустой фолбэк."""
    apply_event(db, source="wb_excise", source_event_id="nf3:1", kind="sale",
                km="0104630520676025215NOFI0003", srid="nf3", payload={"price": 100})
    d = withdraw_batch(db, INN)
    p = db.get(MtDoc, d).payload
    assert "fias_id" not in p
    assert p["primary_document_custom_name"]            # прод требует непустое при OTHER


def test_return_batch_blocked_without_receipt(db):
    km = "0104630520676025215EEEEEEE"
    apply_event(db, source="wb_excise", source_event_id="x:1", kind="sale", km=km, srid="y1",
                payload={"price": 100})   # продажа была до системы — withdraw_batch не вызывался
    apply_event(db, source="wb_excise", source_event_id="x:2", kind="return", km=km, srid="y2",
                payload={"price": 100})   # возврат без нашего вывода (нет LK_RECEIPT в mt.docs)
    created, blocked = return_batch(db, INN)
    assert (created, blocked) == (0, 1)
    from mpmt.journal.models import Item
    assert db.get(Item, km).state == "PENDING_RETURN"   # остался ждать

def test_return_batch_with_receipt(db):
    km = "0104630520676025215FFFFAAA"
    apply_event(db, source="wb_excise", source_event_id="z:1", kind="sale", km=km, srid="z",
                payload={"price": 100, "fiscal_dt": "2026-06-01", "fiscal_doc_number": "7"})
    withdraw_batch(db, INN)
    apply_event(db, source="wb_excise", source_event_id="z:2", kind="return", km=km, srid="z2", payload={"price": 100})
    n = return_batch(db, INN)
    assert n == (1, 0)
    from mpmt.journal.models import Item
    assert db.get(Item, km).state == "RETURNED"

def test_withdraw_nonfiscal_wb_number_persists(db):
    """Non-fiscal ветка: OTHER + WB-<id>; перечитываем из НОВОЙ сессии —
    проверяем, что flag_modified действительно сохранил подставленный номер."""
    import base64, json as _json
    from mpmt.db import SessionLocal
    km = "0104630520676025215NOFISCAL"
    apply_event(db, source="wb_excise", source_event_id="nf:1", kind="sale", km=km,
                srid="nf", payload={"price": 500})   # без fiscal_doc_number
    doc_id = withdraw_batch(db, INN)
    s2 = SessionLocal()
    try:
        doc = s2.get(MtDoc, doc_id)
        assert doc.payload["document_type"] == "OTHER"
        assert doc.payload["document_number"] == f"WB-{doc_id}"
        assert doc.payload["document_number"]  # не пустая строка после reload
        assert base64.b64decode(doc.product_document_b64) == \
            _json.dumps(doc.payload, ensure_ascii=False).encode()
        lines = __import__("mpmt.emitter.batch", fromlist=["to_csv"]).to_csv(s2, doc_id)
        assert km in lines and "50000" in lines
    finally:
        s2.close()
