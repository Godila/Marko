# Deploy: Beget VM (phase 0)

VM: `155.212.142.199` (root, SSH key `~/.ssh/mpmt_beget`; password auth disabled).
Domain: `gis.adel-factory.ru` → A record → VM (set in Beget panel, already done).
Stack: `deploy/docker-compose.yml` — postgres (no published ports), api, worker, ui (nginx), caddy (80/443, auto-TLS), backup (daily pg_dump).

Secrets live in `secrets/` (never commit, never print): `WBtoken.txt`, `vm-postgres.txt` (`PG_PASSWORD=...` on line 2).

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

# 3. Copy WB token (from local repo root)
scp -i ~/.ssh/mpmt_beget secrets/WBtoken.txt root@155.212.142.199:/opt/mpmt/secrets/
ssh ... 'chmod 600 /opt/mpmt/secrets/WBtoken.txt'

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

## Notes

- A dev postgres (container `mpmt-pg`) on 127.0.0.1:5432 is unrelated — do not touch.
- Compose postgres publishes no ports; only Caddy exposes 80/443.
- Backups land in `repo/backups/` (host dir bind-mounted to `/backups`), daily via `backup.sh`.
- Caddyfile must use multi-line blocks; Caddy rejects one-line `handle ... { ... }`.
