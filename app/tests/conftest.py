import json
from pathlib import Path

import pytest
from sqlalchemy import text

import mpmt.journal.models  # noqa: F401  (регистрация journal-моделей в Base.metadata)
import mpmt.mt.models
import mpmt.nkmt.models  # noqa: F401  (регистрация nkmt-моделей в Base.metadata)
import mpmt.sign.models  # noqa  # noqa


@pytest.fixture
def db():
    from mpmt.db import Base, engine, SessionLocal

    schemas = {t.schema for t in Base.metadata.tables.values() if t.schema}
    with engine.begin() as conn:
        for schema in schemas:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.rollback()
    s.close()


@pytest.fixture
def model(monkeypatch):
    """Справочники НК без сети: атрибутная модель — фикстура 6109100000,
    бренд/категория — константы (как в test_nk_validate)."""
    from mpmt.nkmt import validate
    fix = json.loads((Path(__file__).parent / "fixtures" / "nk_attrs_6109100000.json")
                     .read_text(encoding="utf-8"))
    monkeypatch.setattr(validate.dicts, "attrs_model", lambda *a, **k: fix)
    monkeypatch.setattr(validate.dicts, "resolve_brand", lambda *a, **k: 2102811)
    monkeypatch.setattr(validate.dicts, "resolve_category", lambda *a, **k: "214943")
