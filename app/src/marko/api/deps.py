from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session
from marko.db import SessionLocal
from marko.platform import auth
from marko.platform.models import PlatformToken, PlatformAudit, hash_token
from marko.settings import settings

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@dataclass(frozen=True)
class AuthCtx:
    """Duck-typed замена PlatformToken: 40+ хендлеров читают только
    .principal_id (audit) и .scopes; .id — паритет с tok.id (routes_sign)."""
    id: int
    principal_id: int
    scopes: str

def _session_ctx(db: Session, raw: str, response: Response) -> AuthCtx:
    sess, renewed = auth.resolve_session(db, raw)
    if sess is None:
        raise HTTPException(401, "session expired")
    if renewed:
        # скользящий TTL доходит и до браузера: пере-выдаём cookie с полным max_age
        response.set_cookie(auth.SESSION_COOKIE, raw,
                            max_age=int(auth.SESSION_TTL.total_seconds()),
                            httponly=True, secure=settings.session_cookie_secure,
                            samesite="lax", path="/")
    return AuthCtx(id=sess.id, principal_id=sess.principal_id, scopes=sess.scopes)

def require_scope(scope: str):
    """Dual-auth: непустой Bearer — приоритет и fail-closed (signer, скрипты,
    pytest-фикстуры); иначе cookie-сессия консоли. 401 нет/неизвестного,
    403 без scope — контракт прежний."""
    def dep(request: Request, response: Response, db: Session = Depends(get_db)) -> AuthCtx:
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer ") and header[7:].strip():
            row = db.query(PlatformToken).filter_by(token_hash=hash_token(header[7:].strip())).first()
            if not row:
                raise HTTPException(401, "unknown token")
            ctx = AuthCtx(id=row.id, principal_id=row.principal_id, scopes=row.scopes)
        else:
            raw = request.cookies.get(auth.SESSION_COOKIE)
            if not raw:
                raise HTTPException(401, "missing token")
            ctx = _session_ctx(db, raw, response)
        if scope not in ctx.scopes.split(","):
            raise HTTPException(403, f"scope {scope} required")
        return ctx
    return dep

def audit(db: Session, principal_id: int, action: str, detail: dict):
    db.add(PlatformAudit(principal_id=principal_id, action=action, detail=detail))
    db.commit()
