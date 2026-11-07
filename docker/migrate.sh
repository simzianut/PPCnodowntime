#!/bin/sh
# Applies db/migrations/*.sql once each, tracked in schema_migrations (same as `make migrate`).
set -e
export PGPASSWORD="$POSTGRES_PASSWORD"
PSQL="psql -h ppc-db -U ppc -d ppc -v ON_ERROR_STOP=1 -q"
export PGOPTIONS=--client-min-messages=warning
$PSQL -c "CREATE TABLE IF NOT EXISTS schema_migrations (filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
for f in $(ls /migrations/*.sql 2>/dev/null | sort); do
  name=$(basename "$f")
  done=$($PSQL -tAc "SELECT 1 FROM schema_migrations WHERE filename = '$name'")
  if [ -z "$done" ]; then
    echo "applying $name"
    { cat "$f"; echo; echo "INSERT INTO schema_migrations (filename) VALUES ('$name');"; } | $PSQL -1
  fi
done
echo "migrations up to date"
