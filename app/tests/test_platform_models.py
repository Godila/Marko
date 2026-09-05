from sqlalchemy import text
from sqlalchemy.orm import Session
from marko.platform.models import PlatformPrincipal, PlatformToken, hash_token


def test_token_hash_stable():
    assert hash_token("abc") == hash_token("abc") and len(hash_token("abc")) == 64


def test_principal_token_roundtrip(db: Session):
    p = PlatformPrincipal(kind="machine", name="test-agent")
    db.add(p); db.flush()
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("secret1"), scopes="read"))
    db.commit()
    row = db.execute(text("select count(*) from platform.tokens")).scalar()
    assert row == 1
