"""Сервис входа в консоль: пароли (scrypt), серверные сессии, throttle логина.

Не знает про FastAPI/Request — только домен и БД (юнит-тестится на db-фикстуре).
HTTP-обвязка (cookie set/clear, коды ошибок) — в api/routes_auth.py; извлечение
носителя (Bearer/cookie) — в api/deps.py.

Инварианты: сессии без scope signer/admin — в signer-ручки и admin-ручки не
попадают; сырой секрет сессии существует только в cookie, в БД — sha256
(тот же hash_token, что у токенов).
"""
import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from marko.platform.models import (
    PlatformPrincipal, PlatformSession, PlatformUser,
    _utcnow, hash_password, hash_token, verify_password,
)

SESSION_COOKIE = "marko_session"
SESSION_TTL = timedelta(days=7)
RENEW_AFTER = SESSION_TTL / 2          # продление не чаще раза в ~3.5 дня активности
USER_SCOPES = "read,docs:submit,nkmt:import"
LOCKOUT_MAX_S = 30                     # потолок экспоненциальной блокировки

# холостой scrypt при неизвестном username — выравнивание времени ответа
_DUMMY_HASH = hash_password(secrets.token_hex(16))


class AuthError(Exception):
    pass


class InvalidCredentials(AuthError):
    pass


class LoginThrottled(AuthError):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"retry after {retry_after}s")


def start_session(db: Session, principal_id: int, scopes: str) -> str:
    """Создать сессию, вернуть сырой секрет (уходит только в Set-Cookie)."""
    raw = secrets.token_urlsafe(32)
    now = _utcnow()
    db.add(PlatformSession(principal_id=principal_id, token_hash=hash_token(raw),
                           scopes=scopes, created_at=now, last_seen_at=now,
                           expires_at=now + SESSION_TTL))
    db.commit()
    return raw


def resolve_session(db: Session, raw: str) -> PlatformSession | None:
    """Живая сессия или None; просроченная удаляется (ленивая чистка),
    при остатке < RENEW_AFTER — скользящее продление expires_at."""
    sess = db.query(PlatformSession).filter_by(token_hash=hash_token(raw)).first()
    if sess is None:
        return None
    now = _utcnow()
    if sess.expires_at < now:
        db.delete(sess)
        db.commit()
        return None
    if SESSION_TTL - (sess.expires_at - now) > RENEW_AFTER:
        sess.last_seen_at, sess.expires_at = now, now + SESSION_TTL
        db.commit()
    return sess


def close_session(db: Session, raw: str) -> PlatformSession | None:
    sess = db.query(PlatformSession).filter_by(token_hash=hash_token(raw)).first()
    if sess is not None:
        db.delete(sess)
        db.commit()
    return sess


def purge_expired(db: Session) -> int:
    n = db.query(PlatformSession).filter(
        PlatformSession.expires_at < _utcnow()).delete()
    db.commit()
    return n


def verify_login(db: Session, username: str, password: str) -> PlatformUser:
    """Логин+пароль → user. Гейт блокировки — ДО scrypt; при неизвестном
    username — холостой scrypt (анти-timing); неудача наращивает fail_until
    экспоненциально (2**N сек, потолок 30с)."""
    user = db.query(PlatformUser).filter_by(username=username).first()
    now = _utcnow()
    if user is not None and user.fail_until and user.fail_until > now:
        raise LoginThrottled(int((user.fail_until - now).total_seconds()) + 1)
    if user is None:
        verify_password(password, _DUMMY_HASH)   # холостой scrypt — анти-timing
        ok = False
    else:
        ok = verify_password(password, user.password_hash)
    if not ok:
        if user is not None:
            user.fail_count = user.fail_count + 1
            # первые две неудачи — человеческие опечатки, без блокировки;
            # дальше экспонента 2**(N-2) сек с потолком 30с
            if user.fail_count >= 3:
                user.fail_until = now + timedelta(
                    seconds=min(2 ** (user.fail_count - 2), LOCKOUT_MAX_S))
            db.commit()
        raise InvalidCredentials(username)
    user.fail_count, user.fail_until = 0, None
    db.commit()
    return user


def ensure_user(db: Session, username: str, password: str,
                scopes: str = USER_SCOPES) -> PlatformUser:
    """Upsert учётки оператора (seeding скриптом; смена пароля = повторный запуск)."""
    principal = db.query(PlatformPrincipal).filter_by(kind="user", name=username).first()
    if principal is None:
        principal = PlatformPrincipal(kind="user", name=username)
        db.add(principal)
        db.flush()
    user = db.query(PlatformUser).filter_by(username=username).first()
    if user is None:
        user = PlatformUser(principal_id=principal.id, username=username)
        db.add(user)
    user.password_hash = hash_password(password)
    user.scopes = scopes
    user.fail_count, user.fail_until = 0, None
    db.commit()
    return user
