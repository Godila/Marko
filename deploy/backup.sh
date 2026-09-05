#!/bin/sh
set -e
TMP="$(mktemp /backups/.tmp-XXXXXX).gz"
if pg_dump | gzip > "$TMP" && [ "$(wc -c < "$TMP")" -gt 1000 ]; then
  F="/backups/marko-$(date +%Y%m%d-%H%M%S).sql.gz"
  mv "$TMP" "$F"
  ls -1t /backups/marko-*.sql.gz | tail -n +15 | xargs -r rm -f
  echo "backup done: $F"
else
  echo "backup FAILED (empty dump)" >&2
  rm -f "$TMP"
  exit 1
fi
