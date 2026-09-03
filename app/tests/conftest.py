import pytest
from sqlalchemy import text

import mpmt.journal.models  # noqa: F401  (регистрация journal-моделей в Base.metadata)
import mpmt.mt.models
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
