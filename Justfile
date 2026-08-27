set shell := ["bash", "-euo", "pipefail", "-c"]
export PATH := env_var("HOME") + "/.local/bin:" + env_var("PATH")

default:
    @just --list

# One-command setup (idempotent). See setup.sh for the plain-language version.
setup:
    ./setup.sh

# Start compose services (core only; add profiles with `just up search s3 notify`)
up *profiles:
    docker compose $(for p in {{profiles}}; do printf -- "--profile %s " "$p"; done) up -d --wait

down:
    docker compose down

# Re-check everything and explain failures in plain language
doctor:
    uv run content-factory doctor

# Regenerate cross-language contracts (Pydantic -> JSON Schema -> TS -> Ajv)
schemas:
    uv run python scripts/export_schemas.py
    pnpm --filter @content-factory/content-schema-ts run generate

# Fail if generated contracts are out of date
schemas-check:
    uv run python scripts/export_schemas.py --check
    pnpm --filter @content-factory/content-schema-ts run generate
    git diff --exit-code -- packages/content-schema-ts/generated

fmt:
    uv run ruff format .
    uv run ruff check --fix .

lint:
    uv run ruff format --check .
    uv run ruff check .
    pnpm -r --if-present run lint

typecheck:
    uv run pyright
    pnpm -r --if-present run typecheck

# Core tests: no internet, API keys, GPU, or live accounts required
test:
    uv run pytest -q -m "not integration and not gpu and not live"
    pnpm -r --if-present run test

# Integration tests against compose services
test-integration:
    set -a; . ./.env; set +a; uv run pytest -q -m integration

# Offline smoke render (Remotion clip + ffprobe assertions)
render-smoke:
    pnpm --filter @content-factory/renderer run render:spike

# Offline demo (grows per phase)
demo quality="smoke":
    uv run content-factory demo --quality {{quality}}

# Run API (127.0.0.1:8000) and web dev server (127.0.0.1:3000) together
dev:
    set -a; . ./.env; set +a; \
    (uv run content-factory serve --reload & pnpm --filter @content-factory/web run dev & wait)

dev-api:
    set -a; . ./.env; set +a; uv run content-factory serve --reload

dev-web:
    pnpm --filter @content-factory/web run dev

# Apply database migrations (dev + test databases)
migrate:
    set -a; . ./.env; set +a; uv run alembic upgrade head; \
    if [ -n "${DATABASE_URL_TEST:-}" ]; then ALEMBIC_DATABASE_URL="$DATABASE_URL_TEST" uv run alembic upgrade head; fi

# Create owner account + demo workspaces (idempotent)
bootstrap:
    set -a; . ./.env; set +a; uv run content-factory bootstrap
