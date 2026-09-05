from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column
from marko.db import Base


class Batch(Base):
    __tablename__ = "batches"
    __table_args__ = {"schema": "nkmt"}
    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="new")  # new|partial|feeding|moderation|signing|published|error
    source_filename: Mapped[str] = mapped_column(String(256))
    feed_id: Mapped[str] = mapped_column(String(64), default="")
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at = mapped_column(DateTime, server_default=func.now())


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = {"schema": "nkmt"}
    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("nkmt.batches.id"))
    article: Mapped[str] = mapped_column(String(64), unique=True)
    gtin: Mapped[str] = mapped_column(String(32), default="")
    good_id: Mapped[str] = mapped_column(String(64), default="")
    tnved: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String)
    cat_id: Mapped[str] = mapped_column(String(64), default="")
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|fed|moderation|notsigned|signing|published|error|errors|error_sign
    error_text: Mapped[str] = mapped_column(String, default="")
    created_at = mapped_column(DateTime, server_default=func.now())
    updated_at = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Declaration(Base):
    __tablename__ = "declarations"
    __table_args__ = (
        UniqueConstraint("doc_number", "doc_date", name="uq_doc_pair"),
        {"schema": "nkmt"},
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_number: Mapped[str] = mapped_column(String)
    doc_date: Mapped[str] = mapped_column(String(10))  # ISO 'YYYY-MM-DD'
    doc_type: Mapped[str] = mapped_column(String(16), default="declaration")  # declaration|certificate
    title: Mapped[str] = mapped_column(String, default="")


class BrandCache(Base):
    __tablename__ = "brand_cache"
    __table_args__ = {"schema": "nkmt"}
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    brand_id: Mapped[int] = mapped_column(Integer)
    updated_at = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
