from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from mpmt.db import SessionLocal
from mpmt.platform.models import PlatformToken, PlatformAudit, hash_token

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def require_scope(scope: str):
    def dep(request: Request, db: Session = Depends(get_db)) -> PlatformToken:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(401, "missing token")
        row = db.query(PlatformToken).filter_by(token_hash=hash_token(auth[7:])).first()
        if not row:
            raise HTTPException(401, "unknown token")
        if scope not in row.scopes.split(","):
            raise HTTPException(403, f"scope {scope} required")
        return row
    return dep

def audit(db: Session, principal_id: int, action: str, detail: dict):
    db.add(PlatformAudit(principal_id=principal_id, action=action, detail=detail))
    db.commit()
