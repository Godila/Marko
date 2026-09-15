from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column
from marko.db import Base


class Item(Base):
    __tablename__ = "items"
    __table_args__ = {"schema": "journal"}
    km: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), default="NEW")
    withdrawn_by: Mapped[str] = mapped_column(String(16), default="", server_default="")
    updated_at = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    last_event: Mapped[dict] = mapped_column(JSON)
    # факт последней проверки кода в Честном ЗНАКе (cises/info):
    # статус КИ, наименование из карточки ЧЗ, момент наблюдения
    cis_status: Mapped[str] = mapped_column(String(32), default="", server_default="")
    cis_product_name: Mapped[str] = mapped_column(String(256), default="", server_default="")
    cis_checked_at = mapped_column(DateTime, nullable=True)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("source", "source_event_id", name="uq_source_event"),
        {"schema": "journal"},
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    source_event_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    km: Mapped[str] = mapped_column(String(64))
    srid: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at = mapped_column(DateTime, server_default=func.now())
