from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from marko.api.deps import get_db, require_scope
from marko.platform.models import PlatformPrincipal, PlatformToken

router = APIRouter(prefix="/v1")

@router.get("/me")
def me(tok: PlatformToken = Depends(require_scope("read")), db: Session = Depends(get_db)):
    p = db.get(PlatformPrincipal, tok.principal_id)
    return {"name": p.name, "scopes": tok.scopes.split(",")}
