import pytest
from fastapi.testclient import TestClient
from marko.api.app import create_app
from marko.platform import auth
from marko.platform.models import (
    PlatformPrincipal, PlatformToken, PlatformUser, hash_password, hash_token, verify_password,
)

PW = "correct horse battery"
AUTH = {"Authorization": "Bearer t1"}                 # read + docs:submit (легаси-путь)


@pytest.fixture
def client(db):
    p = PlatformPrincipal(kind="user", name="operator")
    db.add(p)
    db.flush()
    db.add(PlatformUser(principal_id=p.id, username="operator",
                        password_hash=hash_password(PW)))
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t1"),
                         scopes="read,docs:submit,admin"))
    db.commit()
    return TestClient(create_app(), base_url="https://testserver")


def _login(client, username="operator", password=PW):
    return client.post("/v1/auth/login", json={"username": username, "password": password})


def test_password_hash_roundtrip():
    h = hash_password(PW)
    assert h.startswith("scrypt$") and verify_password(PW, h)
    assert not verify_password("wrong", h)
    assert not verify_password(PW, "scrypt$999$8$1$zz$zz")   # битый формат → False


def test_login_ok_sets_session_cookie(db, client):
    r = _login(client)
    assert r.status_code == 200
    assert r.json()["name"] == "operator"
    assert "docs:submit" in r.json()["scopes"]
    set_cookie = r.headers["set-cookie"]
    assert "httponly" in set_cookie.lower() and "samesite=lax" in set_cookie.lower()
    assert "secure" in set_cookie.lower() and "marko_session=" in set_cookie
    assert db.query(auth.PlatformSession).count() == 1


def test_me_via_cookie_without_bearer(db, client):
    _login(client)
    r = client.get("/v1/me")            # TestClient сам несёт cookie из jar
    assert r.status_code == 200 and r.json()["name"] == "operator"


def test_wrong_password_and_unknown_user_same_401(db, client):
    assert _login(client, password="nope").status_code == 401
    r_unknown = _login(client, username="ghost")
    assert r_unknown.status_code == 401 and r_unknown.json()["detail"] == "invalid credentials"


def test_lockout_after_failures_429(db, client):
    import time
    for _ in range(2):
        assert _login(client, password="nope").status_code == 401   # без блокировки
    assert _login(client, password="nope").status_code == 401       # 3-я: замок 2с
    time.sleep(2.2)
    assert _login(client, password="nope").status_code == 401       # 4-я: замок 4с
    time.sleep(4.2)
    assert _login(client, password="nope").status_code == 401       # 5-я: замок 8с
    r = _login(client)                                   # даже верный пароль — 429
    assert r.status_code == 429 and "retry after" in r.json()["detail"]
    assert int(r.headers["Retry-After"]) > 0


def test_logout_invalidates_session(db, client):
    _login(client)
    assert client.post("/v1/auth/logout").status_code == 200
    assert client.get("/v1/me").status_code == 401
    assert db.query(auth.PlatformSession).count() == 0
    assert client.post("/v1/auth/logout").status_code == 200    # идемпотентен


def test_expired_session_401_and_purged(db, client):
    from marko.platform.models import _utcnow
    _login(client)
    sess = db.query(auth.PlatformSession).one()
    sess.expires_at = _utcnow().replace(year=2020)
    db.commit()
    assert client.get("/v1/me").status_code == 401
    assert db.query(auth.PlatformSession).count() == 0


def test_sliding_renewal_extends_expiry(db, client):
    from datetime import timedelta
    from marko.platform.models import _utcnow
    _login(client)
    sess = db.query(auth.PlatformSession).one()
    sess.expires_at = _utcnow() + timedelta(hours=1)            # остаток < RENEW_AFTER
    db.commit()
    assert client.get("/v1/me").status_code == 200
    db.expire_all()                                              # identity-map кэш
    sess = db.query(auth.PlatformSession).one()
    assert sess.expires_at - _utcnow() > timedelta(days=6)      # продлена до ~7 дней


def test_session_scope_403_and_no_signer(db, client):
    _login(client)
    assert client.get("/v1/sign/ping").status_code == 403       # нет scope signer
    assert client.post("/v1/batches/withdraw",
                       json={"inn": "090201471350"}).status_code == 200   # docs:submit есть


def test_bearer_priority_fail_closed(db, client):
    _login(client)                                              # cookie в jar
    r = client.get("/v1/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401 and r.json()["detail"] == "unknown token"
    r = client.get("/v1/me", headers=AUTH)                      # легаси-Bearer жив
    assert r.status_code == 200


def test_healthz_still_public(client):
    assert client.get("/healthz").status_code == 200
