### Task 1: Зависимость openpyxl + модели nkmt + миграция 0006

**Files:**
- Modify: `app/pyproject.toml` (dependencies += openpyxl)
- Create: `app/src/mpmt/nkmt/__init__.py` (пустой), `app/src/mpmt/nkmt/models.py`
- Modify: `app/tests/conftest.py` (+ импорт моделей nkmt)
- Create: `app/src/mpmt/alembic/versions/0006_nkmt.py` (autogenerate по циклу из ledger: drop nkmt-схему + DELETE FROM alembic_version WHERE version_num='94526be42e46' → upgrade → autogenerate)
- Test: `app/tests/test_nkmt_models.py`

**Interfaces (Produces):**
- `nkmt.models.Batch`: id, status(str: new|partial|feeding|moderation|signing|published|error), source_filename(str), feed_id(str), stats(JSON dict), created_at
- `nkmt.models.Card`: id, batch_id(FK nkmt.batches.id), article(str, UNIQUE), gtin(str, default ""), good_id(str, default ""), tnved(str10), name(str), cat_id(str, default ""), attributes(JSON dict), status(str: ok|fed|moderation|notsigned|signing|published|error|errors|error_sign), error_text(str), created_at, updated_at
- `nkmt.models.Declaration`: id, doc_number, doc_date, doc_type(declaration|certificate), title; UNIQUE(doc_number, doc_date)
- `nkmt.models.BrandCache`: id, name(UNIQUE), brand_id(int), updated_at

- [ ] **Step 1: failing test** (`test_nkmt_models.py`):

```python
from mpmt.nkmt.models import Batch, Card, Declaration, BrandCache


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
```

- [ ] **Step 2:** `pytest tests/test_nkmt_models.py -v` → FAIL (no module nkmt)
- [ ] **Step 3:** models.py по Interfaces (паттерн `MtDoc` из mt/models.py: `__table_args__={"schema":"nkmt"}`, JSON из sqlalchemy.dialects.postgresql, `created_at = mapped_column(DateTime, server_default=func.now())`, `updated_at` с `onupdate=func.now()`). В conftest добавить `import mpmt.nkmt.models  # noqa`. pyproject: строка `"openpyxl>=3.1",` в dependencies (по алфавиту после httpx).
- [ ] **Step 4:** `pytest tests/test_nkmt_models.py -v` → PASS (3)
- [ ] **Step 5:** миграция 0006 (цикл: drop schema nkmt + downgrade alembic_version → upgrade → autogenerate; проверить в файле op.create_table ×4 + uq-констрейнты article и (doc_number,doc_date))
- [ ] **Step 6:** полный сьют `pytest -q` → все зелёные
- [ ] **Step 7:** commit `feat(nkmt): models + migration 0006 (batches/cards/declarations/brand_cache)`

