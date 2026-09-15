"""Вход в консоль: логин+пароль → серверная сессия в HttpOnly-cookie.

Единственные публичные двери вместе с /healthz: login (гейт throttling до
scrypt, универсальный 401 без перечисления, что именно неверно) и идемпотентный
logout. Побочно login отвечает тем же shape, что /v1/me, — UI не делает второй
запрос. Bearer-клиенты эти ручки не используют.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from marko.api.deps import audit, get_db
from marko.platform import auth
from marko.platform.models import PlatformPrincipal
from marko.settings import settings

router = APIRouter(prefix="/v1/auth")


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/login")
def login(body: LoginBody, response: Response, db: Session = Depends(get_db)):
    try:
        user = auth.verify_login(db, body.username, body.password)
    except auth.LoginThrottled as e:
        raise HTTPException(429, f"too many attempts, retry after {e.retry_after}s",
                            headers={"Retry-After": str(e.retry_after)})
    except auth.InvalidCredentials:
        raise HTTPException(401, "invalid credentials")
    principal = db.get(PlatformPrincipal, user.principal_id)
    raw = auth.start_session(db, user.principal_id, user.scopes)
    auth.purge_expired(db)
    audit(db, user.principal_id, "auth.login", {"username": user.username})
    response.set_cookie(
        auth.SESSION_COOKIE, raw, max_age=int(auth.SESSION_TTL.total_seconds()),
        httponly=True, secure=settings.session_cookie_secure, samesite="lax", path="/")
    return {"name": principal.name, "scopes": user.scopes.split(",")}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    raw = request.cookies.get(auth.SESSION_COOKIE)
    if raw:
        sess = auth.close_session(db, raw)
        if sess is not None:
            audit(db, sess.principal_id, "auth.logout", {})
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    return {"ok": True}
