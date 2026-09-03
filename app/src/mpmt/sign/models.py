from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column
from mpmt.db import Base


class SignTask(Base):
    __tablename__ = "tasks"
    __table_args__ = {"schema": "sign"}

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4
    type: Mapped[str] = mapped_column(String(16))                  # auth_sign | doc_sign
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|leased|done|error
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    lease_until = mapped_column(DateTime, nullable=True)
    lease_owner: Mapped[int] = mapped_column(String(64), default="")
    result: Mapped[dict] = mapped_column(JSON, nullable=True)
    created_at = mapped_column(DateTime, server_default=func.now())
