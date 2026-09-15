import hashlib
import hmac
import os
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column
from marko.db import Base

USER_SCOPES = "read,docs:submit,nkmt:import"   # права консольного оператора (без admin/signer)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _utcnow() -> datetime:
    # наивный UTC: колонки проекта наивные, сравнения идут в Python
    return datetime.now(timezone.utc).replace(tzinfo=None)


SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    """scrypt с солью; параметры зашиты в строку — апгрейд без миграции данных."""
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=64)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, h = stored.split("$")
        if algo != "scrypt":
            return False
        cand = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt),
                              n=int(n), r=int(r), p=int(p), dklen=64)
        return hmac.compare_digest(cand.hex(), h)
    except (ValueError, TypeError):
        return False


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


class PlatformUser(Base):
    """Консольный вход: логин+пароль поверх principal(kind='user').

    scopes — тот же comma-формат, что у токенов (копируются в сессию при логине).
    fail_count/fail_until — детерминированный throttle: 429, пока now < fail_until.
    """
    __tablename__ = "users"
    __table_args__ = {"schema": "platform"}
    id: Mapped[int] = mapped_column(primary_key=True)
    principal_id: Mapped[int] = mapped_column(ForeignKey("platform.principals.id"), unique=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    scopes: Mapped[str] = mapped_column(Text, default=USER_SCOPES)
    fail_count: Mapped[int] = mapped_column(Integer, default=0)
    fail_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PlatformSession(Base):
    """Серверная сессия консоли; сырой секрет живёт только в HttpOnly-cookie."""
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_expires_at", "expires_at"),
                      {"schema": "platform"})
    id: Mapped[int] = mapped_column(primary_key=True)
    principal_id: Mapped[int] = mapped_column(ForeignKey("platform.principals.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    scopes: Mapped[str] = mapped_column(Text, default=USER_SCOPES)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
