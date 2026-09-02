# Deploy: Beget VM

VM: `155.212.142.199` (root, SSH key `~/.ssh/mpmt_beget`; password auth disabled).
Domain: `gis.adel-factory.ru` → A record → VM (set in Beget panel, already done).
Stack: `deploy/docker-compose.yml` — postgres (no published ports), api, worker, ui (nginx), caddy (80/443, auto-TLS), backup (daily pg_dump).

Secrets live in `secrets/` (never commit, never print): `WBtoken.txt`, `vm-postgres.txt` (`PG_PASSWORD=...` on line 2), `platform-token.txt`.

IMPORTANT (learned in phase 1 deploy): compose mounts `../secrets` → the token file read by
containers must be at **`/opt/mpmt/repo/secrets/WBtoken.txt`** (the repo-local, git-ignored
`secrets/` dir), NOT `/opt/mpmt/secrets/`. Phase 0 put it in `/opt/mpmt/secrets/`; if that
dir exists, copy the token into `repo/secrets/` (docker auto-creates an empty dir for a
missing bind source, so the failure only shows at first poll as
`FileNotFoundError: /srv/secrets/WBtoken.txt`).

## First deploy

```bash
# 1. Bundle and copy repo to VM
git bundle create /tmp/mpmt.bundle main
scp -i ~/.ssh/mpmt_beget /tmp/mpmt.bundle root@155.212.142.199:/opt/

# 2. Clone on VM (bundle HEAD has no ref -> checkout main manually if needed)
ssh -i ~/.ssh/mpmt_beget root@155.212.142.199
mkdir -p /opt/mpmt/secrets && chmod 700 /opt/mpmt/secrets
cd /opt/mpmt && git clone /opt/mpmt.bundle repo
cd repo && git checkout main   # only if clone warns "nonexistent ref"

# 3. Copy WB token (from local repo root) into the repo-local secrets dir (bind-mounted)
ssh -i ~/.ssh/mpmt_beget root@155.212.142.199 'mkdir -p /opt/mpmt/repo/secrets && chmod 700 /opt/mpmt/repo/secrets'
scp -i ~/.ssh/mpmt_beget secrets/WBtoken.txt root@155.212.142.199:/opt/mpmt/repo/secrets/
ssh ... 'chmod 600 /opt/mpmt/repo/secrets/WBtoken.txt'

# 4. Create /opt/mpmt/repo/deploy/.env (mode 600) — password from secrets/vm-postgres.txt
#    PG_PASSWORD=<from secrets/vm-postgres.txt line 2>
#    MPMT_DATABASE_URL=postgresql+psycopg://mpmt:<same password>@postgres:5432/mpmt
#    MPMT_WB_TOKEN_FILE=/srv/secrets/WBtoken.txt
#    MPMT_DOMAIN=gis.adel-factory.ru
#    MPMT_TG_BOT_TOKEN=
#    MPMT_TG_CHAT_ID=
#    See deploy/.env.example for the template.

# 5. Build and start
cd /opt/mpmt/repo/deploy && docker compose build && docker compose up -d

# 6. Migrate
docker compose exec api alembic upgrade head

# 7. Verify (wait ~60s for Caddy TLS issuance)
curl -s https://gis.adel-factory.ru/healthz          # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}' https://gis.adel-factory.ru/   # 200
# If unreachable: check firewall — ufw allow 80,443/tcp

# 8. Backup smoke
docker compose exec -T backup sh -c \
  'PGPASSWORD=$PGPASSWORD pg_dump -h postgres -U mpmt mpmt | gzip > /backups/mpmt-smoke.sql.gz && ls -la /backups'
```

## Update procedure

```bash
# Local
git bundle create /tmp/mpmt.bundle main
scp -i ~/.ssh/mpmt_beget /tmp/mpmt.bundle root@155.212.142.199:/opt/
# VM
ssh -i ~/.ssh/mpmt_beget root@155.212.142.199
cd /opt/mpmt/repo && git pull /opt/mpmt.bundle main
cd deploy && docker compose build && docker compose up -d
docker compose exec api alembic upgrade head
```

## Phase 1: owner platform token (admin op, on VM)

The token script does not ship in the image — create the principal/token with a stdin
one-liner. Token is printed once, straight into `/root/platform-token.txt` (mode 600),
then copied to local `secrets/platform-token.txt` (git-ignored). Never echo it elsewhere.

```bash
cd /opt/mpmt/repo/deploy
docker compose exec -T api python - > /root/platform-token.txt <<'PYEOF'
import secrets
from mpmt.db import SessionLocal
from mpmt.platform.models import PlatformPrincipal, PlatformToken, hash_token

db = SessionLocal()
p = PlatformPrincipal(kind="user", name="owner")
db.add(p)
db.flush()
tok = secrets.token_urlsafe(32)
db.add(PlatformToken(principal_id=p.id, token_hash=hash_token(tok), scopes="read,docs:submit"))
db.commit()
print(tok, end="")
db.close()
PYEOF
chmod 600 /root/platform-token.txt   # 43 bytes, no trailing newline
# local: scp -i ~/.ssh/mpmt_beget root@155.212.142.199:/root/platform-token.txt secrets/platform-token.txt
```

## Phase 1 smoke (verified 2026-09-02)

```bash
curl -s https://gis.adel-factory.ru/healthz                                  # {"status":"ok"}
TOKEN=$(cat /root/platform-token.txt)
curl -s https://gis.adel-factory.ru/v1/me -H "Authorization: Bearer $TOKEN"  # {"name":"owner","scopes":["read","docs:submit"]}
curl -s -o /dev/null -w '%{http_code}' https://gis.adel-factory.ru/v1/me     # 401 without token
curl -s https://gis.adel-factory.ru/v1/journal/stats -H "Authorization: Bearer $TOKEN"  # {} before first poll
curl -s -o /dev/null -w '%{http_code}' https://gis.adel-factory.ru/          # 200
curl -s https://gis.adel-factory.ru/ | head -c 300   # <div id="root"> + /assets/index-*.js bundle (not the old placeholder)
# Forced poll (dev smoke — consumes ONE excise call of the 2/24h budget):
docker compose exec -T worker python -c "import asyncio; from mpmt.worker import poll_cycle; asyncio.run(poll_cycle())"
curl -s https://gis.adel-factory.ru/v1/journal/stats -H "Authorization: Bearer $TOKEN"  # {"SKIPPED_FBW":N} (prod is all-FBW)
docker compose logs --tail 20 worker        # no exceptions
```

## Notes

- A dev postgres (container `mpmt-pg`) on 127.0.0.1:5432 is unrelated — do not touch.
- Compose postgres publishes no ports; only Caddy exposes 80/443.
- Backups land in `repo/backups/` (host dir bind-mounted to `/backups`), daily via `backup.sh`.
- Caddyfile must use multi-line blocks; Caddy rejects one-line `handle ... { ... }`.
