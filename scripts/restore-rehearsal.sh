#!/usr/bin/env bash
# Restore rehearsal (phase 13): restore the dump into a scratch database and verify row counts.
set -euo pipefail
cd "$(dirname "$0")/.."
DUMP="${1:?usage: restore-rehearsal.sh <path/to/content_factory.dump>}"
docker compose exec -T postgres psql -U content_factory -d content_factory -c "DROP DATABASE IF EXISTS cf_restore_rehearsal" >/dev/null
docker compose exec -T postgres psql -U content_factory -d content_factory -c "CREATE DATABASE cf_restore_rehearsal" >/dev/null
docker compose exec -T postgres pg_restore -U content_factory -d cf_restore_rehearsal --no-owner < "$DUMP"
docker compose exec -T postgres psql -U content_factory -d cf_restore_rehearsal -tc \
  "SELECT 'workspaces=' || count(*) FROM workspaces UNION ALL SELECT 'runs=' || count(*) FROM production_runs UNION ALL SELECT 'audit=' || count(*) FROM audit_events"
docker compose exec -T postgres psql -U content_factory -d content_factory -c "DROP DATABASE cf_restore_rehearsal" >/dev/null
echo "restore rehearsal OK"
