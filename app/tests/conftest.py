import pytest
from sqlalchemy import text


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
