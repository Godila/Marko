#!/bin/sh
set -e
F="/backups/mpmt-$(date +%Y%m%d-%H%M%S).sql.gz"
pg_dump | gzip > "$F"
ls -1t /backups/mpmt-*.sql.gz | tail -n +15 | xargs -r rm -f
echo "backup done: $F"
