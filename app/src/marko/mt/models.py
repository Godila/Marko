from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column
from marko.db import Base


class MtDoc(Base):
    __tablename__ = "docs"
    __table_args__ = {"schema": "mt"}
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32))          # 'LK_RECEIPT' | 'LP_RETURN'
    status: Mapped[str] = mapped_column(String(16), default="draft")   # draft|signing|submitted|checked_ok|error
    external_id: Mapped[str] = mapped_column(String(64), default="")   # uuid документа в ЧЗ
    payload: Mapped[dict] = mapped_column(JSON)
    product_document_b64: Mapped[str] = mapped_column(String, default="")
    created_at = mapped_column(DateTime, server_default=func.now())
