import hashlib
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column
from marko.db import Base


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class PlatformPrincipal(Base):
    __tablename__ = "principals"
    __table_args__ = {"schema": "platform"}
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))          # user | machine
    name: Mapped[str] = mapped_column(String(128))


class PlatformToken(Base):
    __tablename__ = "tokens"
    __table_args__ = {"schema": "platform"}
    id: Mapped[int] = mapped_column(primary_key=True)
    principal_id: Mapped[int] = mapped_column(ForeignKey("platform.principals.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[str] = mapped_column(Text, default="read")


class PlatformAudit(Base):
    __tablename__ = "audit_log"
    __table_args__ = {"schema": "platform"}
    id: Mapped[int] = mapped_column(primary_key=True)
    ts = mapped_column(DateTime, server_default=func.now())
    principal_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON)


class PlatformKV(Base):
    __tablename__ = "kv"
    __table_args__ = {"schema": "platform"}
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
