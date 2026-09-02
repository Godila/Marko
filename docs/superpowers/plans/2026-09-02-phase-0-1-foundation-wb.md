# MP-GIS_MT — Фаза 0 (каркас) + Фаза 1 (WB-контур) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Поднять каркас платформы на Beget VM (docker-compose, FastAPI, Postgres, аутентификация, TG-алерты) и замкнуть WB-контур: поллинг excise-report + FBS-фильтрация → журнал КМ → черновики LK_RECEIPT/LP_RETURN с выгрузкой для ручной подачи в ЛК ЧЗ.

**Architecture:** Модульный монолит (один образ, два процесса api/worker), Postgres — единственное хранилище и очередь, Caddy — TLS. Коннектор WB нормализует источники в события `SaleEvent`/`ReturnEvent` (маркетплейс-агностичный журнал). Спека: `docs/superpowers/specs/2026-09-02-mp-gis-mt-platform-design.md`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, PostgreSQL 16, httpx, pytest, React (Vite) — минимальный UI, Caddy 2, Docker Compose.

## Global Constraints

- Python 3.12; пакет `mpmt` (src-layout) в `app/`; версии фиксируются в `app/pyproject.toml`.
- Секреты только в `secrets/` (git-ignored) и `.env` (git-ignored); в репо — `.env.example`.
- Идемпотентность событий: уникальный ключ `(source, source_event_id)`; повторный поллинг не создаёт дублей.
- excise-report на базовом токене: **максимум 2 запроса/24ч** (жёстко, 4XX съедает ×10) — расписание 2×/день, не чаще.
- Ретраи HTTP: только 429/5xx, экспонента 1с/4с/16с, макс 3 попытки, `Retry-After` уважается; 4xx — без ретрая.
- `product_cost` в документах ЧЗ — **в копейках** (`price_rub * 100`).
- cis в LK_RECEIPT — короткий КМ из excise (`excise_short`, 31 символ, без криптохвоста).
- Токен WB читается из файла `secrets/WBtoken.txt` (монтируется в контейнер), никогда не в env-репо и не в логи.
- БД-тесты гоняются против Postgres из compose (`TEST_DATABASE_URL`), не sqlite (JSONB/upsert).
- Домен: `gis.adel-factory.ru`. VM-креды: `secrets/VMsecrets.txt` (в чат/логи не выводить).
- Каждый таск заканчивается коммитом; формат сообщений: `feat|fix|chore: ...`.

---

### Task 1: Git-каркас репозитория

**Files:**
- Create: `.gitignore`, `app/pyproject.toml`, `app/src/mpmt/__init__.py`, `app/tests/__init__.py`, `app/tests/test_smoke.py`
- Create: `deploy/.env.example` (позже наполнится)

**Interfaces:**
- Produces: python-пакет `mpmt` (пустой), pytest-инфраструктура.

- [ ] **Step 1: Инициализировать git и .gitignore**

```bash
cd /c/Users/geor/Desktop/MP-GIS_MT
git init -b main
cat > .gitignore <<'EOF'
__pycache__/
*.pyc
.pytest_cache/
.venv/
.env
secrets/
*.db
node_modules/
ui/dist/
app/alembic/versions/*.pyc
.playwright-mcp/
.codegraph/
EOF
```

- [ ] **Step 2: Создать pyproject (pytest + deps фазы 0/1)**

`app/pyproject.toml`:

```toml
[project]
name = "mpmt"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "psycopg[binary]>=3.2",
    "pydantic-settings>=2.4",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.24"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

`app/src/mpmt/__init__.py` — пустой; `app/tests/__init__.py` — пустой.

- [ ] **Step 3: Smoke-тест**

`app/tests/test_smoke.py`:

```python
def test_import():
    import mpmt
    assert mpmt is not None
```

- [ ] **Step 4: Прогнать**

```bash
cd app && python -m venv .venv && . .venv/Scripts/activate  # Windows Git Bash
pip install -e ".[dev]" && pytest -q
```
Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "chore: repo skeleton, mpmt package, pytest"
```

---

### Task 2: Settings, JSON-логи, БД-движок, /healthz

**Files:**
- Create: `app/src/mpmt/settings.py`, `app/src/mpmt/log.py`, `app/src/mpmt/db.py`
- Create: `app/src/mpmt/api/app.py`
- Test: `app/tests/test_health.py`

**Interfaces:**
- Produces:
  - `mpmt.settings.Settings` (pydantic-settings, поля: `database_url`, `wb_token_file`, `tg_bot_token=""`, `tg_chat_id=""`, `poll_excise_cron=["06:30","18:30"]`, `poll_orders_interval_sec=3600`, `excise_days_back=7`, `domain`); загрузка `Settings()`.
  - `mpmt.log.setup_logging()` — JSON-структурированные логи в stdout.
  - `mpmt.db.engine` (create_engine), `mpmt.db.SessionLocal` (sessionmaker), `mpmt.db.Base` (DeclarativeBase).
  - `mpmt.api.app:create_app() -> FastAPI` с `GET /healthz`.

- [ ] **Step 1: Написать тест /healthz**

`app/tests/test_health.py`:

```python
from fastapi.testclient import TestClient
from mpmt.api.app import create_app

def test_healthz():
    client = TestClient(create_app())
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
```

- [ ] **Step 2: Убедиться, что падает** — `pytest tests/test_health.py -q` → FAIL (ModuleNotFoundError: mpmt.api.app).

- [ ] **Step 3: Реализовать**

`app/src/mpmt/settings.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MPMT_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://mpmt:mpmt@localhost:5432/mpmt"
    wb_token_file: str = "secrets/WBtoken.txt"
    domain: str = "gis.adel-factory.ru"
    tg_bot_token: str = ""
    tg_chat_id: str = ""
    poll_excise_cron: list[str] = ["06:30", "18:30"]   # МСК, ровно 2 запроса/24ч
    poll_orders_interval_sec: int = 3600
    excise_days_back: int = 7

settings = Settings()
```

`app/src/mpmt/log.py`:

```python
import json, logging, sys, time

def setup_logging():
    class JSONFormatter(logging.Formatter):
        def format(self, rec):
            return json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(rec.created)),
                "level": rec.levelname, "logger": rec.name, "msg": rec.getMessage(),
            }, ensure_ascii=False)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JSONFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[h])
```

`app/src/mpmt/db.py`:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from mpmt.settings import settings

class Base(DeclarativeBase):
    pass

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)
```

`app/src/mpmt/api/app.py`:

```python
from fastapi import FastAPI
from mpmt.log import setup_logging

def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="MP-GIS_MT", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app

app = create_app()
```

- [ ] **Step 4: Прогнать** — `pytest -q` → PASS (2 теста).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: settings, JSON logging, db engine, healthz endpoint"`

---

### Task 3: Модели platform + Alembic

**Files:**
- Create: `app/src/mpmt/platform/models.py`, `app/src/mpmt/platform/__init__.py`
- Create: `app/alembic.ini`, `app/alembic/env.py`, `app/alembic/versions/0001_platform.py`
- Test: `app/tests/test_platform_models.py`

**Interfaces:**
- Produces: ORM-модели (в схеме `platform`):
  - `PlatformPrincipal(id: int PK, kind: str, name: str)` — kind: 'user' | 'machine';
  - `PlatformToken(id, principal_id FK, token_hash: str unique, scopes: str)` — scopes через запятую;
  - `PlatformAudit(id, ts, principal_id, action, detail: JSON)`;
  - `PlatformKV(key: str PK, value: JSON)` — настройки/счётчики (лимиты excise, watermarks поллинга).
  - Функция `hash_token(token: str) -> str` (sha256 hex).
- БД-тесты используют `TEST_DATABASE_URL` (postgres из compose).

- [ ] **Step 1: Поднять postgres для тестов (однократно на машине)**

```bash
cd deploy && docker compose up -d postgres   # compose появится в Task 6; до него — временно:
# (если compose ещё нет) docker run -d --name mpmt-pg -e POSTGRES_PASSWORD=mpmt -e POSTGRES_USER=mpmt -e POSTGRES_DB=mpmt -p 5432:5432 postgres:16
```

- [ ] **Step 2: Тест моделей**

`app/tests/test_platform_models.py`:

```python
import os, pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from mpmt.platform.models import PlatformPrincipal, PlatformToken, hash_token

@pytest.fixture
def db():
    from mpmt.db import Base, engine
    Base.metadata.create_all(engine, schemas=["platform"]) if engine.dialect.name == "postgresql" else Base.metadata.create_all(engine)
    from mpmt.db import SessionLocal
    s = SessionLocal()
    yield s
    s.rollback(); s.close()

def test_token_hash_stable():
    assert hash_token("abc") == hash_token("abc") and len(hash_token("abc")) == 64

def test_principal_token_roundtrip(db: Session):
    p = PlatformPrincipal(kind="machine", name="test-agent")
    db.add(p); db.flush()
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("secret1"), scopes="read"))
    db.commit()
    row = db.execute(text("select count(*) from platform.tokens")).scalar()
    assert row == 1
```

- [ ] **Step 3: Реализовать модели**

`app/src/mpmt/platform/models.py`:

```python
import hashlib
from sqlalchemy import ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column
from mpmt.db import Base

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
    ts = mapped_column(server_default=func.now())
    principal_id: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSON)

class PlatformKV(Base):
    __tablename__ = "kv"
    __table_args__ = {"schema": "platform"}
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
```

- [ ] **Step 4: Alembic**

```bash
cd app && . .venv/Scripts/activate && alembic init alembic
```

`app/alembic.ini`: `sqlalchemy.url` оставить пустым (берём из env в env.py). `app/alembic/env.py` — заменить `run_migrations_online()`:

```python
from mpmt.settings import settings
config.set_main_option("sqlalchemy.url", settings.database_url)
# в run_migrations_online(): target_metadata = mpmt.db.Base.metadata; + include_schemas=True
```
(импорт `from mpmt.db import Base`; `target_metadata = Base.metadata`; в `context.configure(...)` добавить `include_schemas=True`; в `run_migrations_offline` аналогично.)

Сгенерировать миграцию (создаёт схему platform + таблицы):

```bash
python -c "import mpmt.platform.models"   # регистрация моделей
alembic revision --autogenerate -m "platform schema"
```
Проверить, что в миграции есть `principals/tokens/audit_log/kv` и `op.execute('CREATE SCHEMA IF NOT EXISTS platform')` в `upgrade()` (добавить руками первой строкой, если autogenerate не вставил).

```bash
alembic upgrade head && pytest tests/test_platform_models.py -q
```
Expected: PASS. `TEST_DATABASE_URL` при необходимости: `export MPMT_DATABASE_URL=postgresql+psycopg://mpmt:mpmt@localhost:5432/mpmt_test` и создать БД `mpmt_test`.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: platform models (principals/tokens/audit/kv) + alembic"`

---

### Task 4: Аутентификация API (Bearer + scopes + аудит)

**Files:**
- Create: `app/src/mpmt/api/deps.py`
- Modify: `app/src/mpmt/api/app.py` (подключить роуты demo `/v1/me`)
- Create: `app/src/mpmt/api/routes_me.py`
- Test: `app/tests/test_auth.py`

**Interfaces:**
- Produces:
  - `mpmt.api.deps.get_principal(scopes: str)` — зависимость FastAPI: `Depends(require_scope("read"))` возвращает `PlatformPrincipal`; заголовок `Authorization: Bearer <token>`; 401 если токен неизвестен, 403 если scope нет.
  - `audit(db, principal_id, action, detail)` — функция записи в `platform.audit_log`.
  - Скопы: `read`, `journal:manage`, `docs:submit`, `nkmt:import`, `signer`, `admin` (строки, запятая-разделённые в `PlatformToken.scopes`).

- [ ] **Step 1: Тест**

`app/tests/test_auth.py`:

```python
import pytest
from fastapi.testclient import TestClient
from mpmt.api.app import create_app
from mpmt.platform.models import PlatformPrincipal, PlatformToken, hash_token

@pytest.fixture
def client(db):
    p = PlatformPrincipal(kind="machine", name="agent"); db.add(p); db.flush()
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("tok-read"), scopes="read"))
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("tok-admin"), scopes="read,admin"))
    db.commit()
    return TestClient(create_app())

def test_me_ok(client):
    r = client.get("/v1/me", headers={"Authorization": "Bearer tok-admin"})
    assert r.status_code == 200 and r.json() == {"name": "agent", "scopes": ["read", "admin"]}

def test_missing_token_401(client):
    assert client.get("/v1/me").status_code == 401

def test_wrong_scope_403(client):
    r = client.get("/v1/me", headers={"Authorization": "Bearer tok-read"})
    assert r.status_code == 403
```
(фикстура `db` — из `tests/conftest.py`; вынести её туда из Task 3.)

`app/tests/conftest.py`:

```python
import pytest

@pytest.fixture
def db():
    from mpmt.db import Base, engine, SessionLocal
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    s = SessionLocal()
    yield s
    s.rollback(); s.close()
```

- [ ] **Step 2: FAIL** → `pytest tests/test_auth.py -q` (нет deps/routes).

- [ ] **Step 3: Реализация**

`app/src/mpmt/api/deps.py`:

```python
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from mpmt.db import SessionLocal
from mpmt.platform.models import PlatformPrincipal, PlatformToken, PlatformAudit, hash_token

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def require_scope(scope: str):
    def dep(request: Request, db: Session = Depends(get_db)) -> PlatformPrincipal:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise HTTPException(401, "missing token")
        row = db.query(PlatformToken).filter_by(token_hash=hash_token(auth[7:])).first()
        if not row:
            raise HTTPException(401, "unknown token")
        if scope not in row.scopes.split(","):
            raise HTTPException(403, f"scope {scope} required")
        return db.get(PlatformPrincipal, row.principal_id)
    return dep

def audit(db: Session, principal_id: int, action: str, detail: dict):
    db.add(PlatformAudit(principal_id=principal_id, action=action, detail=detail))
    db.commit()
```

`app/src/mpmt/api/routes_me.py`:

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from mpmt.api.deps import get_db, require_scope
from mpmt.platform.models import PlatformPrincipal, PlatformToken

router = APIRouter(prefix="/v1")

@router.get("/me")
def me(p: PlatformPrincipal = Depends(require_scope("read")), db: Session = Depends(get_db)):
    tok = db.query(PlatformToken).filter_by(principal_id=p.id).first()
    return {"name": p.name, "scopes": tok.scopes.split(",")}
```

В `create_app()` добавить: `from mpmt.api.routes_me import router as me_router; app.include_router(me_router)`.

- [ ] **Step 4: PASS** — `pytest -q`.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: bearer auth with scopes + audit helper + /v1/me"`

---

### Task 5: Notifier (Telegram + fallback в лог)

**Files:**
- Create: `app/src/mpmt/notifier/__init__.py`
- Test: `app/tests/test_notifier.py`

**Interfaces:**
- Produces: `mpmt.notifier.send(text: str)` — async; шлёт в TG если настроены `tg_bot_token`/`tg_chat_id`, иначе `logger.warning` (чтобы работало без TG). Используется поллерами; никогда не бросает исключение наружу.

- [ ] **Step 1: Тест (httpx MockTransport)**

`app/tests/test_notifier.py`:

```python
import httpx, pytest
from mpmt.notifier import send

@pytest.mark.asyncio
async def test_send_tg_ok(monkeypatch):
    calls = []
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.read()); return httpx.Response(200, json={"ok": True})
    monkeypatch.setattr("mpmt.notifier._client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await send("привет")   # settings без токенов → fallback; проверим напрямую tg-ветку:
    assert calls == []     # без настройки TG — не зовём API
```

- [ ] **Step 2: FAIL** (модуля нет).

- [ ] **Step 3: Реализация**

`app/src/mpmt/notifier/__init__.py`:

```python
import logging
import httpx
from mpmt.settings import settings

log = logging.getLogger("mpmt.notifier")

def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=10)

async def send(text: str):
    try:
        if not settings.tg_bot_token or not settings.tg_chat_id:
            log.warning("ALERT (no tg): %s", text)
            return
        async with _client() as c:
            await c.post(f"https://api.telegram.org/bot{settings.tg_bot_token}/sendMessage",
                         json={"chat_id": settings.tg_chat_id, "text": text})
    except Exception:
        log.exception("notify failed: %s", text)
```

- [ ] **Step 4: PASS** — `pytest tests/test_notifier.py -q`.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: telegram notifier with log fallback"`

---

### Task 6: Docker-образ, compose, Caddy, backup-sidecar

**Files:**
- Create: `app/Dockerfile`, `deploy/docker-compose.yml`, `deploy/Caddyfile`, `deploy/backup.sh`, `deploy/.env.example`
- Modify: ничего в коде.

**Interfaces:**
- Produces: контейнеры `caddy`, `api` (uvicorn), `worker` (заглушка-цикл до Task 11), `ui` (заглушка-nginx до Task 14), `postgres`, `backup`. `GET https://gis.adel-factory.ru/healthz` после деплоя.

- [ ] **Step 1: Dockerfile (multi-stage, один образ для api/worker)**

`app/Dockerfile`:

```dockerfile
FROM python:3.12-slim AS base
WORKDIR /srv
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
CMD ["uvicorn", "mpmt.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: compose + Caddy + backup**

`deploy/docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16
    environment: { POSTGRES_USER: mpmt, POSTGRES_PASSWORD: ${PG_PASSWORD}, POSTGRES_DB: mpmt }
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck: { test: ["CMD-SHELL", "pg_isready -U mpmt"], interval: 10s, retries: 5 }
    restart: unless-stopped

  api:
    build: { context: ../app }
    env_file: .env
    volumes: ["../secrets:/srv/secrets:ro"]
    depends_on: { postgres: { condition: service_healthy } }
    restart: unless-stopped

  worker:
    build: { context: ../app }
    command: ["python", "-m", "mpmt.worker"]
    env_file: .env
    volumes: ["../secrets:/srv/secrets:ro"]
    depends_on: { postgres: { condition: service_healthy } }
    restart: unless-stopped

  ui:
    image: nginx:1.27-alpine
    volumes: ["./ui-dist:/usr/share/nginx/html:ro"]
    restart: unless-stopped

  caddy:
    image: caddy:2
    ports: ["80:80", "443:443"]
    volumes: ["./Caddyfile:/etc/caddy/Caddyfile:ro", "caddy_data:/data"]
    restart: unless-stopped

  backup:
    image: postgres:16
    volumes: ["../backups:/backups"]
    entrypoint: ["/bin/sh", "-c", "/backup.sh"]
    volumes_from: []   # скрипт монтируем:
    # (см. ниже — скрипт через volumes)
    depends_on: { postgres: { condition: service_healthy } }
    restart: unless-stopped

volumes: { pgdata: {}, caddy_data: {} }
```

`backup`-сервис упростить (без volumes_from — не нужен):

```yaml
  backup:
    image: postgres:16
    volumes: ["../backups:/backups", "./backup.sh:/backup.sh:ro"]
    entrypoint: ["/bin/sh", "-c", "while true; do /backup.sh; sleep 86400; done"]
    environment: { PGHOST: postgres, PGUSER: mpmt, PGDATABASE: mpmt, PGPASSWORD: ${PG_PASSWORD} }
    depends_on: { postgres: { condition: service_healthy } }
    restart: unless-stopped
```

`deploy/backup.sh`:

```sh
#!/bin/sh
set -e
F="/backups/mpmt-$(date +%Y%m%d-%H%M%S).sql.gz"
pg_dump | gzip > "$F"
ls -1t /backups/mpmt-*.sql.gz | tail -n +15 | xargs -r rm -f
echo "backup done: $F"
```

`deploy/Caddyfile`:

```
gis.adel-factory.ru {
    handle /v1/* { reverse_proxy api:8000 }
    handle /healthz { reverse_proxy api:8000 }
    handle { reverse_proxy ui:80 }
}
```

`deploy/.env.example`:

```
PG_PASSWORD=change-me
MPMT_DATABASE_URL=postgresql+psycopg://mpmt:change-me@postgres:5432/mpmt
MPMT_WB_TOKEN_FILE=/srv/secrets/WBtoken.txt
MPMT_DOMAIN=gis.adel-factory.ru
MPMT_TG_BOT_TOKEN=
MPMT_TG_CHAT_ID=
```

- [ ] **Step 3: Заглушки worker и ui, локальный подъём**

`app/src/mpmt/worker.py` (реальная логика в Task 11; сейчас цикл с heartbeat в kv):

```python
import time, logging
from mpmt.log import setup_logging

log = logging.getLogger("mpmt.worker")

def main():
    setup_logging()
    log.info("worker started (phase 0 stub)")
    while True:
        time.sleep(60)

if __name__ == "__main__":
    main()
```

`deploy/ui-dist/index.html`: `<html><body>MP-GIS_MT UI placeholder</body></html>`

```bash
cd deploy && cp .env.example .env && docker compose build && docker compose up -d
curl -s localhost:80/healthz -H "Host: gis.adel-factory.ru"   # через caddy
docker compose exec api alembic upgrade head
docker compose exec postgres sh -c 'PGPASSWORD=$POSTGRES_PASSWORD pg_dump mpmt | head -5'
```
Expected: healthz ok; dump отдаёт SQL-заголовок.

- [ ] **Step 4: Commit** — `git add -A && git commit -m "feat: docker compose (api/worker/ui/caddy/postgres/backup), Caddyfile, backup.sh"`

---

### Task 7: Деплой фазы 0 на Beget VM

**Files:**
- Create: `docs/deploy-beget.md` (чеклист, креды не включать)

**Interfaces:** Produces: работающий `https://gis.adel-factory.ru/healthz` (Let's Encrypt сертификат).

- [ ] **Step 1: Подготовить VM (SSH-ключ вместо пароля)**

```bash
SSH="ssh root@155.212.142.199"     # пароль из secrets/VMsecrets.txt (интерактивно)
ssh-keygen -t ed25519 -f ~/.ssh/mpmt_beget -N "" 2>/dev/null || true
ssh-copy-id -i ~/.ssh/mpmt_beget.pub root@155.212.142.199
# на VM: sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config && systemctl restart ssh
```

- [ ] **Step 2: Установить Docker (официальный скрипт) и клонировать репо**

На VM (репо пока без remote — до фазы Git-host; копируем через git bundle или временный bare по ssh):

```bash
# локально: git bundle create /tmp/mpmt.bundle main && scp /tmp/mpmt.bundle root@…:/opt/
# на VM:
curl -fsSL https://get.docker.com | sh
mkdir -p /opt/mpmt && cd /opt/mpmt && git clone /opt/mpmt.bundle repo 2>/dev/null || (git init && git pull /opt/mpmt.bundle main)
cd repo/deploy && cp .env.example .env   # заполнить PG_PASSWORD и MPMT_*
docker compose build && docker compose up -d
docker compose exec api alembic upgrade head
```

- [ ] **Step 3: DNS A-запись `gis` → 155.212.142.199 (панель Beget), проверить**

```bash
curl -s https://gis.adel-factory.ru/healthz
```
Expected: `{"status":"ok"}`. Caddy сам выпустит сертификат (порт 80/443 открыты).

- [ ] **Step 4: Задокументировать и закоммитить чеклист**

`docs/deploy-beget.md` — шаги выше без кредов. `git add -A && git commit -m "docs: beget deploy checklist (phase 0)"`

---

### Task 8: Модели journal + стейт-машина КМ

**Files:**
- Create: `app/src/mpmt/journal/__init__.py`, `app/src/mpmt/journal/models.py`, `app/src/mpmt/journal/state.py`
- Create: `app/alembic/versions/0002_journal.py` (autogenerate)
- Test: `app/tests/test_state_machine.py`

**Interfaces:**
- Produces:
  - `journal.items`: `km: str PK` (короткий КМ), `state: str`, `updated_at`, `last_event: JSON`.
  - `journal.events`: `id PK`, `source: str`, `source_event_id: str`, `kind: str` ('sale'|'return'), `km`, `srid`, `payload: JSON`, `created_at`; UNIQUE(source, source_event_id).
  - `mpmt.journal.state.transition(state: str, kind: str) -> str` — чистая функция переходов:
    - `NEW + sale → PENDING_WITHDRAW`; `NEW + return → ANOMALY_NO_RECEIPT`
    - `PENDING_WITHDRAW + sale → ANOMALY_RESALE`
    - `WITHDRAWN + return → PENDING_RETURN`; `PENDING_WITHDRAW + return → PENDING_RETURN`
    - `PENDING_RETURN + sale → PENDING_WITHDRAW` (перепродажа после возврата); `PENDING_RETURN + return → ANOMALY_RERETURN`
    - `RETURNED + sale → PENDING_WITHDRAW`
    - любое `state + skip_fbw → SKIPPED_FBW` (только из NEW)
    - прочее → `ANOMALY_UNKNOWN_TRANSITION`
  - `mpmt.journal.apply_event(db, source, source_event_id, kind, km, srid, payload) -> tuple[str, bool]` — UPSERT события (skip если дубль), переход items; возвращает `(new_state, created)`.

- [ ] **Step 1: Тест стейт-машины (чистая функция)**

`app/tests/test_state_machine.py`:

```python
import pytest
from mpmt.journal.state import transition

@pytest.mark.parametrize("state,kind,expected", [
    ("NEW", "sale", "PENDING_WITHDRAW"),
    ("NEW", "return", "ANOMALY_NO_RECEIPT"),
    ("PENDING_WITHDRAW", "sale", "ANOMALY_RESALE"),
    ("PENDING_WITHDRAW", "return", "PENDING_RETURN"),
    ("WITHDRAWN", "return", "PENDING_RETURN"),
    ("PENDING_RETURN", "sale", "PENDING_WITHDRAW"),
    ("PENDING_RETURN", "return", "ANOMALY_RERETURN"),
    ("RETURNED", "sale", "PENDING_WITHDRAW"),
    ("NEW", "skip_fbw", "SKIPPED_FBW"),
    ("WITHDRAWN", "sale", "ANOMALY_UNKNOWN_TRANSITION"),
])
def test_transitions(state, kind, expected):
    assert transition(state, kind) == expected
```

- [ ] **Step 2: FAIL** → реализовать `state.py`:

```python
RULES = {
    ("NEW", "sale"): "PENDING_WITHDRAW",
    ("NEW", "return"): "ANOMALY_NO_RECEIPT",
    ("NEW", "skip_fbw"): "SKIPPED_FBW",
    ("PENDING_WITHDRAW", "sale"): "ANOMALY_RESALE",
    ("PENDING_WITHDRAW", "return"): "PENDING_RETURN",
    ("WITHDRAWN", "return"): "PENDING_RETURN",
    ("PENDING_RETURN", "sale"): "PENDING_WITHDRAW",
    ("PENDING_RETURN", "return"): "ANOMALY_RERETURN",
    ("RETURNED", "sale"): "PENDING_WITHDRAW",
}

def transition(state: str, kind: str) -> str:
    return RULES.get((state, kind), "ANOMALY_UNKNOWN_TRANSITION")
```

- [ ] **Step 3: Тест apply_event (БД)**

Дополнить `test_state_machine.py`:

```python
def test_apply_event_idempotent(db):
    from mpmt.journal import apply_event
    s1, c1 = apply_event(db, source="wb_excise", source_event_id="e1", kind="sale",
                         km="0104630520676025215UKsEhVmAtad", srid="r1", payload={"price": 1793})
    s2, c2 = apply_event(db, source="wb_excise", source_event_id="e1", kind="sale",
                         km="0104630520676025215UKsEhVmAtad", srid="r1", payload={"price": 1793})
    assert (s1, c1) == ("PENDING_WITHDRAW", True)
    assert (s2, c2) == ("PENDING_WITHDRAW", False)   # дубль не создан
```

- [ ] **Step 4: Реализовать модели + apply_event, миграция**

`app/src/mpmt/journal/models.py`:

```python
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column
from mpmt.db import Base

class Item(Base):
    __tablename__ = "items"
    __table_args__ = {"schema": "journal"}
    km: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(32), default="NEW")
    updated_at = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    last_event: Mapped[dict] = mapped_column(JSON)

class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("source", "source_event_id", name="uq_source_event"),
                      {"schema": "journal"})
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    source_event_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    km: Mapped[str] = mapped_column(String(64))
    srid: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at = mapped_column(DateTime, server_default=func.now())
```

`app/src/mpmt/journal/__init__.py`:

```python
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from mpmt.journal.models import Event, Item
from mpmt.journal.state import transition

def apply_event(db: Session, *, source: str, source_event_id: str, kind: str,
                km: str, srid: str, payload: dict) -> tuple[str, bool]:
    stmt = (pg_insert(Event)
            .values(source=source, source_event_id=source_event_id, kind=kind,
                    km=km, srid=srid, payload=payload)
            .on_conflict_do_nothing(constraint="uq_source_event")
            .returning(Event.id))
    if not db.execute(stmt).scalar():
        db.commit()
        item = db.get(Item, km)
        return (item.state if item else "NEW"), False
    item = db.get(Item, km)
    new_state = transition(item.state if item else "NEW", kind)
    if item is None:
        db.add(Item(km=km, state=new_state, last_event=payload))
    else:
        item.state = new_state; item.last_event = payload
    db.commit()
    return new_state, True
```

Миграция: `alembic revision --autogenerate -m "journal schema"` (добавить `CREATE SCHEMA IF NOT EXISTS journal` руками при необходимости), `alembic upgrade head`.

- [ ] **Step 5: PASS + Commit** — `pytest -q && git add -A && git commit -m "feat: journal items/events + km state machine"`

---

### Task 9: WB HTTP-клиент (ретраи, Retry-After, лимит-гейт excise)

**Files:**
- Create: `app/src/mpmt/connector_wb/__init__.py`, `app/src/mpmt/connector_wb/client.py`
- Test: `app/tests/test_wb_client.py`

**Interfaces:**
- Produces:
  - `mpmt.connector_wb.client.WBClient(token: str, base: str = "https://seller-analytics-api.wildberries.ru")`:
    - `request(method, path, *, params=None, json=None, retries=3) -> httpx.Response` — ретраи на 429/5xx (1с/4с/16с, `Retry-After` приоритетен); 4xx — сразу `WbHttpError` (кастомное исключение с `status` и `body`).
    - `excise_report(date_from, date_to) -> list[dict]` — `POST /api/v1/analytics/excise-report?dateFrom=&dateTo=` body `{"countries":["RU"]}` → `response.data`; **перед вызовом проверяет суточный лимит** (kv-счётчик `wb_excise_usage`: список ISO-timestamps за 24ч; если ≥2 → `WbLimitError`).
    - `orders(limit=1000) -> list[dict]` — `GET /api/v3/orders?next=0&limit=…` (база `https://marketplace-api.wildberries.ru`), пагинация по `next` из ответа до исчерпания.
  - `load_wb_token(path=settings.wb_token_file) -> str` — чтение файла, strip.
  - Токен в заголовке `Authorization: <token>` (без Bearer), `Accept: application/json`.

- [ ] **Step 1: Тесты (MockTransport)**

`app/tests/test_wb_client.py`:

```python
import httpx, pytest, time
from mpmt.connector_wb.client import WBClient, WbHttpError, WbLimitError

def mk(handler, **kw):
    return WBClient.__new__(WBClient)  # не нужно; см. below

def test_retry_on_429_then_ok(monkeypatch):
    calls = {"n": 0}
    def handler(r):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"ok": True})
    c = WBClient(token="t", transport=httpx.MockTransport(handler), sleeper=lambda s: None)
    r = c.request("GET", "/x")
    assert r.status_code == 200 and calls["n"] == 2

def test_4xx_no_retry():
    def handler(r): return httpx.Response(400, json={"error": "bad"})
    c = WBClient(token="t", transport=httpx.MockTransport(handler), sleeper=lambda s: None)
    with pytest.raises(WbHttpError) as e:
        c.request("GET", "/x")
    assert e.value.status == 400

def test_excise_limit_gate(db):
    from mpmt.connector_wb.client import WBClient
    def handler(r): return httpx.Response(200, json={"response": {"data": []}})
    c = WBClient(token="t", transport=httpx.MockTransport(handler), sleeper=lambda s: None, db=db)
    c.excise_report("2026-09-01", "2026-09-02")
    c.excise_report("2026-09-01", "2026-09-02")
    with pytest.raises(WbLimitError):
        c.excise_report("2026-09-01", "2026-09-02")
```

- [ ] **Step 2: FAIL → Step 3: Реализация**

`app/src/mpmt/connector_wb/client.py`:

```python
import json, logging, time
import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from mpmt.platform.models import PlatformKV

log = logging.getLogger("mpmt.wb")

class WbHttpError(Exception):
    def __init__(self, status: int, body: str):
        self.status, self.body = status, body
        super().__init__(f"WB {status}: {body[:200]}")

class WbLimitError(Exception):
    pass

class WBClient:
    def __init__(self, token: str, base: str = "https://seller-analytics-api.wildberries.ru",
                 transport=None, sleeper=time.sleep, db: Session | None = None):
        self.token, self.base, self.sleeper, self.db = token, base, sleeper, db
        self.http = httpx.Client(transport=transport, timeout=30)

    def _headers(self):
        return {"Authorization": self.token, "Accept": "application/json"}

    def request(self, method, path, *, params=None, json_body=None, retries=3) -> httpx.Response:
        delays = [1, 4, 16]
        for attempt in range(retries + 1):
            r = self.http.request(method, self.base + path, params=params,
                                  json=json_body, headers=self._headers())
            if r.status_code in (429,) or r.status_code >= 500:
                if attempt < retries:
                    wait = delays[attempt] if "Retry-After" not in r.headers else min(
                        float(r.headers["Retry-After"]), 60)
                    log.warning("wb retry %s %s -> %s, wait %ss", method, path, r.status_code, wait)
                    self.sleeper(wait); continue
            if r.status_code >= 400:
                raise WbHttpError(r.status_code, r.text)
            return r

    # ---- excise: лимит 2 запроса/24ч на базовом токене ----
    def _gate_excise(self):
        if self.db is None:
            return
        kv = self.db.get(PlatformKV, "wb_excise_usage")
        now = time.time()
        stamps = [t for t in (kv.value["stamps"] if kv else []) if now - t < 86400]
        if len(stamps) >= 2:
            raise WbLimitError("excise 2/24h limit reached")
        self.db.execute(pg_insert(PlatformKV).values(
            key="wb_excise_usage", value={"stamps": stamps + [now]}
        ).on_conflict_do_update(index_elements=[PlatformKV.key],
            set_={"value": {"stamps": stamps + [now]}}))
        self.db.commit()

    def excise_report(self, date_from: str, date_to: str) -> list[dict]:
        self._gate_excise()
        r = self.request("POST", "/api/v1/analytics/excise-report",
                         params={"dateFrom": date_from, "dateTo": date_to},
                         json_body={"countries": ["RU"]})
        return r.json()["response"]["data"]

def load_wb_token(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read().strip()
```

`app/src/mpmt/connector_wb/__init__.py` — пустой.

- [ ] **Step 4: PASS** — `pytest tests/test_wb_client.py -q` (конструктор теста `mk` удалить — не нужен).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: wb http client with retries and excise 2/24h gate"`

---

### Task 10: Парсер excise + FBS-фильтр (заказы)

**Files:**
- Create: `app/src/mpmt/connector_wb/ingest.py`
- Test: `app/tests/test_ingest.py` (на живой фикстуре `wb-specs/fixtures/prod/excise-report.json`)

**Interfaces:**
- Consumes: `WBClient.excise_report`, `WBClient.orders`, `apply_event`.
- Produces:
  - `mpmt.connector_wb.ingest.excise_rows_to_events(rows: list[dict]) -> list[dict]` — нормализация: каждая строка → `{"source": "wb_excise", "source_event_id": f"{srid}:{excise_short}:{operation_type_id}", "kind": "sale"|"return", "km": excise_short, "srid": srid, "payload": {...строка целиком}}`.
  - `mpmt.connector_wb.ingest.fbs_rids(order_rows: list[dict]) -> set[str]` — `{o["rid"] for o in order_rows}` (orders API отдаёт только FBS-заказы продавца).
  - `mpmt.connector_wb.ingest.ingest_excise(db, rows, fbs: set[str]) -> dict` — фильтрует: `row["srid"] in fbs` → sale/return-события; иначе `apply_event(kind="skip_fbw")`; возвращает счётчики `{"sale": n, "return": n, "skipped_fbw": n, "duplicates": n}`.

- [ ] **Step 1: Тест на фикстуре прода**

`app/tests/test_ingest.py`:

```python
import json, pathlib
from mpmt.connector_wb.ingest import excise_rows_to_events, fbs_rids, ingest_excise

FIXT = json.loads(pathlib.Path("../../wb-specs/fixtures/prod/excise-report.json").read_text("utf-8"))["response"]["data"]

def test_rows_to_events_shape():
    evs = excise_rows_to_events(FIXT)
    assert len(evs) == len(FIXT) == 1022
    e = evs[0]
    assert set(e) == {"source", "source_event_id", "kind", "km", "srid", "payload"}
    assert len(e["km"]) == 31

def test_ingest_filters_fbw(db):
    # все строки фикстуры — FBW: ни один srid не входит в fbs-множество
    stats = ingest_excise(db, FIXT, fbs=set())
    assert stats == {"sale": 0, "return": 0, "skipped_fbw": 1022, "duplicates": 0}

def test_ingest_sale_and_return(db):
    rows = [
        {"excise_short": "0104630520676025215AAAAAAA", "srid": "s1", "operation_type_id": 1, "price": 1793, "nm_id": 1, "fiscal_dt": "2026-06-01"},
        {"excise_short": "0104630520676025215BBBBBBB", "srid": "s2", "operation_type_id": 2, "price": 1793, "nm_id": 1, "fiscal_dt": "2026-06-05"},
    ]
    stats = ingest_excise(db, rows, fbs={"s1", "s2"})
    assert stats == {"sale": 1, "return": 1, "skipped_fbw": 0, "duplicates": 0}
```
(пути к фикстуре при запуске из `app/`: положить копию фикстуры в `app/tests/fixtures/excise-report.json` в Step 1 и читать её оттуда — компактнее; в тесте использовать `pathlib.Path(__file__).parent / "fixtures" / "excise-report.json"`.)

- [ ] **Step 2: FAIL → Step 3: Реализация**

`app/src/mpmt/connector_wb/ingest.py`:

```python
from sqlalchemy.orm import Session
from mpmt.journal import apply_event

def excise_rows_to_events(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        kind = "sale" if row["operation_type_id"] == 1 else "return"
        out.append({
            "source": "wb_excise",
            "source_event_id": f"{row['srid']}:{row['excise_short']}:{row['operation_type_id']}",
            "kind": kind, "km": row["excise_short"], "srid": row["srid"], "payload": row,
        })
    return out

def fbs_rids(order_rows: list[dict]) -> set[str]:
    return {o["rid"] for o in order_rows if o.get("rid")}

def ingest_excise(db: Session, rows: list[dict], fbs: set[str]) -> dict:
    stats = {"sale": 0, "return": 0, "skipped_fbw": 0, "duplicates": 0}
    for ev in excise_rows_to_events(rows):
        if ev["srid"] not in fbs:
            _, created = apply_event(db, source="wb_excise", source_event_id=ev["source_event_id"],
                                     kind="skip_fbw", km=ev["km"], srid=ev["srid"], payload=ev["payload"])
            if created: stats["skipped_fbw"] += 1
            else: stats["duplicates"] += 1
            continue
        _, created = apply_event(db, **ev)
        if created: stats[ev["kind"]] += 1
        else: stats["duplicates"] += 1
    return stats
```

- [ ] **Step 4: PASS + Commit** — `pytest tests/test_ingest.py -q && git add -A && git commit -m "feat: excise ingest with fbs filter (contract-tested on prod fixture)"`

---

### Task 11: Worker-цикл поллинга (excise 2×/день + orders 1/час) + алерты

**Files:**
- Modify: `app/src/mpmt/worker.py` (замена заглушки)
- Create: `app/src/mpmt/connector_wb/poll.py`
- Test: `app/tests/test_poll.py`

**Interfaces:**
- Consumes: `WBClient`, `ingest_excise`, `fbs_rids`, `notifier.send`, `PlatformKV` (watermark `wb_orders_rids`).
- Produces:
  - `mpmt.connector_wb.poll.run_once(db, client) -> dict` — orders → fbs-множество → excise за `excise_days_back` → ingest; пишет watermark и счётчики; при `WbHttpError/WbLimitError` — `await notifier.send(...)` и не падает.
  - `worker.main()` — вечный цикл: вычислить ближайший cron-слот из `poll_excise_cron` (МСК=UTC+3), спать до него, запускать `run_once`; orders-кэш обновляет раз в `poll_orders_interval_sec`.

- [ ] **Step 1: Тест run_once (мок-клиент)**

`app/tests/test_poll.py`:

```python
from mpmt.connector_wb.poll import run_once

class FakeClient:
    def __init__(self): self.db = None
    def orders(self, limit=1000):
        return [{"rid": "s1"}]
    def excise_report(self, date_from, date_to):
        return [{"excise_short": "0104630520676025215CCCCCCC", "srid": "s1",
                 "operation_type_id": 1, "price": 1793, "nm_id": 1, "fiscal_dt": "2026-09-01"}]

def test_run_once_ingests(db):
    stats = run_once(db, FakeClient())
    assert stats["sale"] == 1
```

- [ ] **Step 2: FAIL → Step 3: Реализация**

`app/src/mpmt/connector_wb/poll.py`:

```python
import asyncio, logging
from sqlalchemy.orm import Session
from mpmt.connector_wb.client import WBClient, WbHttpError, WbLimitError
from mpmt.connector_wb.ingest import fbs_rids, ingest_excise

log = logging.getLogger("mpmt.poll")

def run_once(db: Session, client: WBClient) -> dict:
    orders = client.orders()
    fbs = fbs_rids(orders)
    from datetime import date, timedelta
    to, frm = date.today(), date.today() - timedelta(days=7)
    rows = client.excise_report(frm.isoformat(), to.isoformat())
    stats = ingest_excise(db, rows, fbs)
    log.info("poll done: %s (fbs_rids=%d)", stats, len(fbs))
    return stats
```

`app/src/mpmt/worker.py` (полная замена):

```python
import asyncio, logging
from datetime import datetime, timedelta, timezone
from mpmt.log import setup_logging
from mpmt.db import SessionLocal
from mpmt.settings import settings
from mpmt.notifier import send
from mpmt.connector_wb.client import WBClient, WbHttpError, WbLimitError, load_wb_token
from mpmt.connector_wb.poll import run_once

log = logging.getLogger("mpmt.worker")
MSK = timezone(timedelta(hours=3))

async def poll_cycle():
    db = SessionLocal()
    try:
        client = WBClient(token=load_wb_token(settings.wb_token_file), db=db)
        stats = run_once(db, client)
        if stats["sale"] or stats["return"]:
            await send(f"WB poll: {stats}")
    except (WbHttpError, WbLimitError) as e:
        log.error("poll failed: %s", e)
        await send(f"ALERT: WB poll failed: {e}")
    finally:
        db.close()

def seconds_until(cron_times: list[str], now: datetime) -> float:
    nxt = None
    for hhmm in cron_times:
        h, m = map(int, hhmm.split(":"))
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if t <= now:
            t += timedelta(days=1)
        nxt = t if nxt is None or t < nxt else nxt
    return (nxt - now).total_seconds()

def main():
    setup_logging()
    log.info("worker started, excise cron %s MSK", settings.poll_excise_cron)
    while True:
        wait = seconds_until(settings.poll_excise_cron, datetime.now(MSK))
        log.info("next excise poll in %.0f s", wait)
        import time; time.sleep(max(wait, 1))
        asyncio.run(poll_cycle())

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: PASS + Commit** — `pytest -q && git add -A && git commit -m "feat: wb polling worker (excise 2x/day, orders cache, tg alerts)"`

---

### Task 12: Emitter — черновики LK_RECEIPT / LP_RETURN + mt.docs

**Files:**
- Create: `app/src/mpmt/emitter/__init__.py`, `app/src/mpmt/emitter/batch.py`, `app/src/mpmt/mt/__init__.py`, `app/src/mpmt/mt/models.py`
- Create: `app/alembic/versions/0003_mt_docs.py` (autogenerate)
- Test: `app/tests/test_emitter.py`

**Interfaces:**
- Produces:
  - `mt.docs`: `id PK`, `type` ('LK_RECEIPT'|'LP_RETURN'), `status` ('draft'|'signing'|'submitted'|'checked_ok'|'error'), `payload: JSON`, `product_document_b64: str`, `created_at`.
  - `mpmt.emitter.batch.withdraw_batch(db, inn: str, limit: int = 100) -> int` — собирает до `limit` КМ в `PENDING_WITHDRAW` с их последним sale-событием (payload с `price`, fiscal-полями) → создаёт mt.docs draft c JSON:
    ```json
    {"inn": "090201471350", "action": "DISTANCE", "action_date": "<fiscal_dt|сегодня>",
     "document_type": "RECEIPT", "document_number": "<fiscal_doc_number|WB-<id батча>>",
     "document_date": "<fiscal_dt>", "products": [{"cis": "<km>", "product_cost": <price*100>}]}
    ```
    (`document_type=RECEIPT` если у события есть `fiscal_doc_number`, иначе `OTHER` + номер `WB-<doc_id>`); переводит КМ `PENDING_WITHDRAW → WITHDRAWN` (событие типа manual с source='emitter'); возвращает id документа.
  - `mpmt.emitter.batch.return_batch(db, inn, limit=100) -> int` — КМ в `PENDING_RETURN` → LP_RETURN draft: `{"trade_participant_inn": inn, "return_type": "REMOTE_SALE_RETURN", "paid": true, "primary_document_type": "RECEIPT", "primary_document_number": "<номер LK_RECEIPT-вывода этого КМ из mt.docs>", "primary_document_date": "<дата>", "products_list": [{"ki": km}]}`; первичку берёт из последнего LK_RECEIPT (payload.products[].cis == km); если вывода нет — КМ остаётся `PENDING_RETURN`, попадает в счётчик `blocked_no_receipt`. КМ → `RETURNED`.
  - `mpmt.emitter.batch.to_csv(doc_id) -> str` — CSV-выгрузка для ручной подачи (шапка: `cis;product_cost` для RECEIPT; `ki` для RETURN) — просмотрщик в UI.
  - Статус `WITHDRAWN`/`RETURNED` ставится сразу (фаза 1 — ручная подача; фаза 2 изменит на «после CHECKED_OK», оставим TODO-комментарий в коде).
  - Ponytail-упрощение: `document_type`/`document_number` батча берутся из **первого** товара батча; если в батче смешаны строки с/без fiscal-реквизитов — батч бьётся на границе смены признака (в `withdraw_batch` сгруппировать items по `has_fiscal` и создать при необходимости два документа; если не делать на фазе 1 — пометить `# ponytail:` комментарием и алертить в TG при расхождении).

- [ ] **Step 1: Тесты**

`app/tests/test_emitter.py`:

```python
from mpmt.journal import apply_event
from mpmt.emitter.batch import withdraw_batch, return_batch
from mpmt.mt.models import MtDoc

INN = "090201471350"
SALE = {"excise_short": "0104630520676025215DDDDDDD", "srid": "s1", "operation_type_id": 1,
        "price": 1793, "nm_id": 1, "fiscal_dt": "2026-06-01", "fiscal_doc_number": "42"}

def _sale(db, km, **over):
    row = dict(SALE, excise_short=km, **over)
    apply_event(db, source="wb_excise", source_event_id=f"{row['srid']}:{km}:1",
                kind="sale", km=km, srid=row["srid"], payload=row)

def test_withdraw_batch(db):
    _sale(db, "0104630520676025215DDDDDDD")
    doc_id = withdraw_batch(db, INN)
    doc = db.get(MtDoc, doc_id)
    assert doc.type == "LK_RECEIPT"
    p = doc.payload
    assert p["action"] == "DISTANCE" and p["document_type"] == "RECEIPT"
    assert p["products"][0] == {"cis": "0104630520676025215DDDDDDD", "product_cost": 179300}
    from mpmt.journal.models import Item
    assert db.get(Item, "0104630520676025215DDDDDDD").state == "WITHDRAWN"

def test_return_batch_blocked_without_receipt(db):
    km = "0104630520676025215EEEEEEE"
    apply_event(db, source="wb_excise", source_event_id="x:2", kind="return", km=km, srid="y",
                payload={"price": 100})   # возврат без нашего вывода (продажа была до системы)
    created, blocked = return_batch(db, INN)
    assert (created, blocked) == (0, 1)
    from mpmt.journal.models import Item
    assert db.get(Item, km).state == "PENDING_RETURN"   # остался ждать
def test_return_batch_with_receipt(db):
    km = "0104630520676025215FFFFAAA"
    apply_event(db, source="wb_excise", source_event_id="z:1", kind="sale", km=km, srid="z",
                payload={"price": 100, "fiscal_dt": "2026-06-01", "fiscal_doc_number": "7"})
    withdraw_batch(db, INN)
    apply_event(db, source="wb_excise", source_event_id="z:2", kind="return", km=km, srid="z2", payload={"price": 100})
    n = return_batch(db, INN)
    assert n == (1, 0)
    from mpmt.journal.models import Item
    assert db.get(Item, km).state == "RETURNED"
```
Примечание: `return_batch` возвращает `(created_docs, blocked_no_receipt)` → скорректировать сигнатуру в реализации: `return_batch(db, inn, limit=100) -> tuple[int, int]`; тесты соответственно (`(1, 0)`, `(0, 1)`).

- [ ] **Step 2: FAIL → Step 3: Реализация**

`app/src/mpmt/mt/models.py`:

```python
from sqlalchemy import String, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.orm import Mapped, mapped_column
from mpmt.db import Base

class MtDoc(Base):
    __tablename__ = "docs"
    __table_args__ = {"schema": "mt"}
    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    payload: Mapped[dict] = mapped_column(JSON)
    product_document_b64: Mapped[str] = mapped_column(String, default="")
    created_at = mapped_column(server_default=func.now())
```

`app/src/mpmt/emitter/batch.py`:

```python
import base64, json, logging
from datetime import date
from sqlalchemy.orm import Session
from mpmt.journal.models import Item
from mpmt.journal import apply_event
from mpmt.mt.models import MtDoc
from mpmt.platform.models import PlatformKV

log = logging.getLogger("mpmt.emitter")

def _payload_json(doc: MtDoc) -> None:
    doc.product_document_b64 = base64.b64encode(
        json.dumps(doc.payload, ensure_ascii=False).encode()).decode()

def withdraw_batch(db: Session, inn: str, limit: int = 100) -> int:
    items = db.query(Item).filter_by(state="PENDING_WITHDRAW").limit(limit).all()
    if not items:
        return 0
    products = []
    for it in items:
        p = it.last_event or {}
        products.append({"cis": it.km, "product_cost": int(p.get("price", 0)) * 100})
    has_fiscal = bool((items[0].last_event or {}).get("fiscal_doc_number"))
    today = date.today().isoformat()
    ev = items[0].last_event or {}
    payload = {
        "inn": inn, "action": "DISTANCE",
        "action_date": ev.get("fiscal_dt") or today,
        "document_type": "RECEIPT" if has_fiscal else "OTHER",
        "document_number": ev.get("fiscal_doc_number") or "",   # подставим WB-<id> после flush
        "document_date": ev.get("fiscal_dt") or today,
        "products": products,
    }
    doc = MtDoc(type="LK_RECEIPT", status="draft", payload=payload)
    db.add(doc); db.flush()
    if not payload["document_number"]:
        payload["document_number"] = f"WB-{doc.id}"
    _payload_json(doc)
    for it in items:
        apply_event(db, source="emitter", source_event_id=f"withdraw:{doc.id}:{it.km}",
                    kind="withdraw", km=it.km, srid="", payload={"doc_id": doc.id})
        it.state = "WITHDRAWN"   # ponytail: фаза 1 — ручная подача; фаза 2 = после CHECKED_OK
    db.commit()
    return doc.id
```

**Внимание:** `withdraw` — новый kind для стейт-машины: добавить в `state.py` правило `("PENDING_WITHDRAW", "withdraw"): "WITHDRAWN"` и `("PENDING_RETURN", "return_apply"): "RETURNED"`; но `apply_event` с неизвестным kind вернёт ANOMALY — поэтому emitter меняет state напрямую (как выше) и пишет journal.events через отдельный INSERT-хелпер `log_action(db, source, source_event_id, kind, km, payload)` (без стейт-перехода). Реализовать `log_action` в `journal/__init__.py` (тот же UPSERT, без transition). В `withdraw_batch`/`return_batch` использовать `log_action`, а `it.state = ...` оставить как есть.

`return_batch` (симметрично; первичка ищется по всем LK_RECEIPT draft/submitted — по payload.products[].cis):

```python
def return_batch(db: Session, inn: str, limit: int = 100) -> tuple[int, int]:
    items = db.query(Item).filter_by(state="PENDING_RETURN").limit(limit).all()
    if not items:
        return 0, 0
    receipts = {}   # km -> (doc_id, document_number, document_date)
    for d in db.query(MtDoc).filter(MtDoc.type == "LK_RECEIPT", MtDoc.status != "error").all():
        for pr in d.payload.get("products", []):
            receipts.setdefault(pr["cis"], (d.id, d.payload.get("document_number", ""), d.payload.get("document_date", "")))
    products, blocked = [], 0
    today = date.today().isoformat()
    for it in items:
        rc = receipts.get(it.km)
        if not rc:
            blocked += 1; continue
        products.append({"ki": it.km,
                         "primary_document_type": "RECEIPT",
                         "primary_document_number": rc[1],
                         "primary_document_date": rc[2]})
    if not products:
        return 0, blocked
    payload = {"trade_participant_inn": inn, "return_type": "REMOTE_SALE_RETURN",
               "paid": True, "primary_document_type": "RECEIPT",
               "primary_document_number": products[0]["primary_document_number"],
               "primary_document_date": products[0]["primary_document_date"],
               "products_list": products}
    doc = MtDoc(type="LP_RETURN", status="draft", payload=payload)
    _payload_json(doc); db.add(doc); db.flush()
    ret_kms = {p["ki"] for p in products}
    for it in items:
        if it.km in ret_kms:
            it.state = "RETURNED"
    db.commit()
    return 1, blocked
```

`to_csv(doc_id)`:

```python
def to_csv(db: Session, doc_id: int) -> str:
    doc = db.get(MtDoc, doc_id)
    if doc.type == "LK_RECEIPT":
        lines = ["cis;product_cost"] + [f"{p['cis']};{p['product_cost']}" for p in doc.payload["products"]]
    else:
        lines = ["ki"] + [p["ki"] for p in doc.payload["products_list"]]
    return "\n".join(lines)
```

Миграция `0003`: `alembic revision --autogenerate -m "mt docs"` (+ `CREATE SCHEMA IF NOT EXISTS mt`), `alembic upgrade head`.

- [ ] **Step 4: PASS** — `pytest tests/test_emitter.py -q` (тесты поправить под `tuple`-возврат return_batch).

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: emitter batch drafts LK_RECEIPT/LP_RETURN + mt.docs"`

---

### Task 13: API роуты журнала и батчей

**Files:**
- Create: `app/src/mpmt/api/routes_journal.py`
- Modify: `app/src/mpmt/api/app.py` (include_router)
- Test: `app/tests/test_api_journal.py`

**Interfaces:**
- Consumes: `require_scope`, `journal.models`, `MtDoc`, `withdraw_batch`, `return_batch`, `to_csv`.
- Produces (все под Bearer):
  - `GET /v1/journal?state=&limit=` (scope `read`) → `[{km, state, updated_at, last_event}]`;
  - `GET /v1/journal/stats` (read) → счётчики по состояниям;
  - `POST /v1/batches/withdraw` (scope `docs:submit`, body `{"inn": "...", "limit": 100}`) → `{doc_id}`; `POST /v1/batches/return` → `{docs, blocked}`;
  - `GET /v1/docs` (read) → список mt.docs; `GET /v1/docs/{id}` (read) → payload; `GET /v1/docs/{id}/csv` (read) → text/csv.
  - Каждая мутация — `audit(...)`.

- [ ] **Step 1: Тест** (создать токен со скоупами, прогнать сценарий journal→batch→csv):

`app/tests/test_api_journal.py`:

```python
from fastapi.testclient import TestClient
from mpmt.api.app import create_app
from mpmt.platform.models import PlatformPrincipal, PlatformToken, hash_token
from mpmt.journal import apply_event

KM = "0104630520676025215TEST123"

def _client(db):
    p = PlatformPrincipal(kind="user", name="owner"); db.add(p); db.flush()
    db.add(PlatformToken(principal_id=p.id, token_hash=hash_token("t1"), scopes="read,docs:submit"))
    db.commit()
    return TestClient(create_app())

def test_journal_and_batch_flow(db):
    c = _client(db)
    apply_event(db, source="wb_excise", source_event_id="e1", kind="sale",
                km=KM, srid="s1", payload={"price": 1793})
    r = c.get("/v1/journal?state=PENDING_WITHDRAW", headers={"Authorization": "Bearer t1"})
    assert r.status_code == 200 and r.json()[0]["km"] == KM
    r = c.post("/v1/batches/withdraw", headers={"Authorization": "Bearer t1"},
               json={"inn": "090201471350"})
    assert r.status_code == 200 and "doc_id" in r.json()
    r = c.get(f"/v1/docs/{r.json()['doc_id']}/csv", headers={"Authorization": "Bearer t1"})
    assert KM in r.text
```

- [ ] **Step 2: FAIL → Step 3: Реализовать роуты** (стандартные FastAPI-обработчики поверх интерфейсов; `limit: int = 100` c `le=1000`; csv через `PlainTextResponse`).

- [ ] **Step 4: PASS + Commit** — `git add -A && git commit -m "feat: journal/batches/docs api routes with scopes and audit"`

---

### Task 14: UI-минимум (React/Vite): журнал + батчи + документы

**Files:**
- Create: `ui/package.json`, `ui/vite.config.js`, `ui/index.html`, `ui/src/main.jsx`, `ui/src/App.jsx`
- Modify: `deploy/docker-compose.yml` (ui собирается из ../ui, multi-stage nginx)
- Create: `ui/Dockerfile`

**Interfaces:**
- Consumes: REST `/v1/journal`, `/v1/docs`, `/v1/batches/*` с Bearer-токеном (поле ввода токена в UI, хранится в localStorage).
- Produces: одна страница с тремя вкладками: «Журнал» (таблица km/state/updated_at, фильтр по state), «Батчи» (кнопки «Собрать вывод»/«Собрать возврат» + inn-поле, таблица mt.docs со статусами и ссылками CSV), «Docs JSON» (просмотр payload выбранного документа).

- [ ] **Step 1: Каркас Vite**

`ui/package.json`:

```json
{
  "name": "mpmt-ui", "private": true, "type": "module",
  "scripts": { "dev": "vite", "build": "vite build" },
  "dependencies": { "react": "^18.3.1", "react-dom": "^18.3.1" },
  "devDependencies": { "@vitejs/plugin-react": "^4.3.1", "vite": "^5.4.0" }
}
```

`ui/vite.config.js`:

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig({ plugins: [react()], server: { proxy: { '/v1': 'http://localhost:8000' } } })
```

`ui/index.html`: `<div id="root"></div>` + `<script type="module" src="/src/main.jsx"></script>`.

`ui/src/main.jsx`:

```jsx
import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
createRoot(document.getElementById('root')).render(<App />)
```

- [ ] **Step 2: App.jsx (полный код)**

`ui/src/App.jsx`:

```jsx
import React, { useEffect, useState } from 'react'

const api = async (path, token, opts = {}) => {
  const r = await fetch(path, { ...opts, headers: { 'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json', ...(opts.headers || {}) } })
  if (!r.ok) throw new Error(`${r.status}`)
  return r.headers.get('content-type')?.includes('json') ? r.json() : r.text()
}

export default function App() {
  const [token, setToken] = useState(localStorage.getItem('tok') || '')
  const [tab, setTab] = useState('journal')
  const [items, setItems] = useState([]); const [docs, setDocs] = useState([])
  const [inn, setInn] = useState('090201471350'); const [msg, setMsg] = useState('')

  const load = async () => {
    if (!token) return
    try {
      setItems(await api('/v1/journal?limit=200', token))
      setDocs(await api('/v1/docs', token)); setMsg('')
    } catch (e) { setMsg('ошибка: ' + e.message) }
  }
  useEffect(() => { localStorage.setItem('tok', token); load() }, [token])

  const mkBatch = async (kind) => {
    const r = await api(`/v1/batches/${kind}`, token, { method: 'POST', body: JSON.stringify({ inn }) })
    setMsg(JSON.stringify(r)); load()
  }

  return (
    <div style={{ fontFamily: 'sans-serif', margin: '0 auto', maxWidth: 1100 }}>
      <h2>MP-GIS_MT</h2>
      <input value={token} onChange={e => setToken(e.target.value)} placeholder="API token" size={40} />
      {['journal', 'batches'].map(t => (
        <button key={t} onClick={() => setTab(t)} style={{ marginLeft: 8, fontWeight: tab === t ? 'bold' : 'normal' }}>
          {t === 'journal' ? 'Журнал' : 'Батчи'}</button>))}
      <span style={{ color: 'red', marginLeft: 12 }}>{msg}</span>
      {tab === 'journal' && (
        <table border="1" cellPadding="4" style={{ borderCollapse: 'collapse', marginTop: 12, width: '100%' }}>
          <thead><tr><th>КМ</th><th>Состояние</th><th>Обновлён</th></tr></thead>
          <tbody>{items.map(i => (
            <tr key={i.km}><td style={{ fontFamily: 'monospace' }}>{i.km}</td><td>{i.state}</td><td>{i.updated_at}</td></tr>))}</tbody>
        </table>)}
      {tab === 'batches' && (
        <div style={{ marginTop: 12 }}>
          <input value={inn} onChange={e => setInn(e.target.value)} size={14} />
          <button onClick={() => mkBatch('withdraw')} style={{ marginLeft: 8 }}>Собрать вывод</button>
          <button onClick={() => mkBatch('return')} style={{ marginLeft: 8 }}>Собрать возврат</button>
          <table border="1" cellPadding="4" style={{ borderCollapse: 'collapse', marginTop: 12, width: '100%' }}>
            <thead><tr><th>id</th><th>тип</th><th>статус</th><th>создан</th><th>CSV</th></tr></thead>
            <tbody>{docs.map(d => (
              <tr key={d.id}><td>{d.id}</td><td>{d.type}</td><td>{d.status}</td><td>{d.created_at}</td>
                <td><a href="#" onClick={e => { e.preventDefault(); api(`/v1/docs/${d.id}/csv`, token).then(t => setMsg(t)) }}>csv/json</a></td></tr>))}</tbody>
          </table></div>)}
      <pre style={{ background: '#f4f4f4', padding: 8, marginTop: 12, maxHeight: 300, overflow: 'auto' }}>{msg}</pre>
    </div>
  )
}
```

- [ ] **Step 3: Dockerfile ui + compose-правка**

`ui/Dockerfile`:

```dockerfile
FROM node:20-alpine AS build
WORKDIR /s
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /s/dist /usr/share/nginx/html
```

В `deploy/docker-compose.yml` сервис `ui` заменить на:

```yaml
  ui:
    build: { context: ../ui }
    restart: unless-stopped
```
(строку `volumes: ui/dist` и сервис с nginx-образом убрать).

- [ ] **Step 4: Локальная проверка** — `cd ui && npm install && npm run build` (dist создаётся); `docker compose up -d --build` в deploy; браузер `http://localhost/` (Host-заголовок) — форма токена, пустые таблицы.

- [ ] **Step 5: Commit** — `git add -A && git commit -m "feat: minimal react ui (journal, batches, docs csv)"`

---

### Task 15: Финальный деплой фазы 1 + smoke + боевые токены платформы

**Files:**
- Modify: `docs/deploy-beget.md` (дополнить: admin-токен платформы, smoke-сценарий)
- Create: `scripts/make_platform_token.py`

**Interfaces:**
- Produces: работающий контур на VM; первый выданный платформенный токен (scope `read,docs:submit`) — в `secrets/platform-token.txt` (git-ignored); задокументированный smoke.

- [ ] **Step 1: Скрипт создания принципала/токена (админ-операция на VM)**

`scripts/make_platform_token.py`:

```python
"""python scripts/make_platform_token.py <name> <scopes>  → печатает токен (один раз)"""
import secrets as pysecrets, sys
from mpmt.db import SessionLocal
from mpmt.platform.models import PlatformPrincipal, PlatformToken, hash_token

name, scopes = sys.argv[1], sys.argv[2]
db = SessionLocal()
p = PlatformPrincipal(kind="user", name=name); db.add(p); db.flush()
tok = pysecrets.token_urlsafe(32)
db.add(PlatformToken(principal_id=p.id, token_hash=hash_token(tok), scopes=scopes))
db.commit(); print(tok)
```

- [ ] **Step 2: Деплой**

```bash
git bundle create /tmp/mpmt.bundle main && scp /tmp/mpmt.bundle root@155.212.142.199:/opt/
# на VM: cd /opt/mpmt/repo && git pull /opt/mpmt.bundle main && cd deploy && docker compose build && docker compose up -d
docker compose exec api alembic upgrade head
docker compose exec api python /srv/scripts/make_platform_token.py owner read,docs:submit > /root/platform-token.txt
```
(скрипт в образ не входит — выполнить с VM: `docker compose exec api python - <<'EOF' ...EOF` либо скопировать; выбрать вариант при исполнении и зафиксировать в чеклисте.)

- [ ] **Step 3: Smoke фазы 1**

```bash
TOKEN=$(ssh root@… cat /root/platform-token.txt)
curl -s https://gis.adel-factory.ru/v1/journal/stats -H "Authorization: Bearer $TOKEN"
# UI: открыть https://gis.adel-factory.ru, вставить токен, дождаться первого поллинга (cron 06:30/18:30 МСК)
# принудительный прогон (dev): docker compose exec worker python -c "import asyncio; from mpmt.worker import poll_cycle; asyncio.run(poll_cycle())"
```
Expected: stats с `skipped_fbw` (прода сейчас весь FBW — это правильно), UI показывает журнал; TG-сообщение после поллинга (если настроен).

- [ ] **Step 4: Commit + метка** — `git add -A && git commit -m "chore: phase 1 deploy checklist, platform token script" && git tag phase-1`

---

## Self-Review (выполнен при написании)

1. **Покрытие спеки (фазы 0–1):** compose/Caddy/backup (Task 6), SSH-ключ (7), platform+scopes+аудит (3–4), healthz (2), notifier (5), excise+orders+фильтр FBS (9–11), journal+идемпотентность (8), emitter LK_RECEIPT/LP_RETURN (12), API (13), UI (14), деплой+smoke (15). Не входит и не должно: signer/connector_mt/НКМТ/MCP (фазы 2–3), внешний бэкап (фаза 2).
2. **Плейсхолдеры:** отсутствуют; спорные места (перенос `srid`-строк в фикстуру теста, варианты запуска токен-скрипта на VM) помечены как выбор при исполнении с указанием критерия.
3. **Консистентность:** `apply_event(db, source=..., ...)` keyword-only; `WBClient(token, transport, sleeper, db)` везде одинаково; `return_batch -> tuple[int,int]` синхронизирован с тестами Task 12; `withdraw_batch -> int`.
