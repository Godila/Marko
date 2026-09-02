from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from mpmt.api.deps import get_db, require_scope
from mpmt.platform.models import PlatformPrincipal, PlatformToken, hash_token

router = APIRouter(prefix="/v1")

@router.get("/me")
def me(request: Request, p: PlatformPrincipal = Depends(require_scope("admin")), db: Session = Depends(get_db)):
    auth = request.headers.get("Authorization", "")
    tok = db.query(PlatformToken).filter_by(token_hash=hash_token(auth[7:])).first()
    return {"name": p.name, "scopes": tok.scopes.split(",")}
