# Task 1 brief

## Global Constraints (binding)


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


## Task 1:
 Git-каркас репозитория

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

