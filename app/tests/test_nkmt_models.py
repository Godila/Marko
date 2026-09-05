from marko.nkmt.models import Batch, Card, Declaration, BrandCache


def test_batch_card_persist(db):
    b = Batch(status="new", source_filename="x.xlsx")
    db.add(b); db.flush()
    c = Card(batch_id=b.id, article="A-1", tnved="6109100000", name="Футболка",
             attributes={"2478": "Футболка"})
    db.add(c); db.commit()
    assert db.query(Card).filter_by(article="A-1").one().batch_id == b.id


def test_declaration_unique_pair(db):
    from sqlalchemy.exc import IntegrityError
    db.add(Declaration(doc_number="ЕАЭС №RU Д-RU.АБ12.В.12345", doc_date="2026-01-01"))
    db.commit()
    db.add(Declaration(doc_number="ЕАЭС №RU Д-RU.АБ12.В.12345", doc_date="2026-01-01"))
    try:
        db.commit(); assert False
    except IntegrityError:
        db.rollback()


def test_brand_cache(db):
    db.add(BrandCache(name="YCPB", brand_id=2102811)); db.commit()
    assert db.query(BrandCache).filter_by(name="YCPB").one().brand_id == 2102811
