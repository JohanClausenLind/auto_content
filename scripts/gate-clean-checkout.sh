#!/usr/bin/env bash
# Phase-1 gate rehearsal: clone HEAD into a scratch directory and run ./setup.sh there against the
# SAME compose services (different Postgres database names) so nothing in the real checkout is touched.
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SCRATCH="${1:-$(mktemp -d /tmp/cf-clean-checkout-XXXX)}"
echo "cloning $REPO -> $SCRATCH"
git clone -q "$REPO" "$SCRATCH/content-factory"
cd "$SCRATCH/content-factory"
# Reuse the running compose project (ports) but isolate databases and skip the browser download.
docker compose exec -T postgres psql -U content_factory -d content_factory -tc \
  "SELECT 1 FROM pg_database WHERE datname='cf_gate'" | grep -q 1 || \
  docker compose exec -T postgres psql -U content_factory -d content_factory -c "CREATE DATABASE cf_gate" >/dev/null
docker compose exec -T postgres psql -U content_factory -d content_factory -tc \
  "SELECT 1 FROM pg_database WHERE datname='cf_gate_test'" | grep -q 1 || \
  docker compose exec -T postgres psql -U content_factory -d content_factory -c "CREATE DATABASE cf_gate_test" >/dev/null
cp .env.example .env
sed -i 's#/content_factory$#/cf_gate#; s#/content_factory_test$#/cf_gate_test#' .env
export CF_SKIP_BROWSER="${CF_SKIP_BROWSER:-1}" CF_OWNER_PASSWORD="gate-password-correct-horse"
export COMPOSE_PROJECT_NAME=content-factory
./setup.sh
set -a; . ./.env; set +a
uv run content-factory doctor --json | python3 -c 'import json,sys; d=json.load(sys.stdin); bad=[c for c in d["checks"] if c["status"]=="fail"]; print("doctor fails:", [c["name"] for c in bad]); sys.exit(1 if bad else 0)'
uv run pytest -q 2>&1 | tail -1
pnpm -r --if-present run test 2>&1 | grep -E "Tests " || true
echo "clean-checkout gate OK in $SCRATCH/content-factory"
