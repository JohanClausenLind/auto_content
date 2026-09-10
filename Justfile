set shell := ["bash", "-euo", "pipefail", "-c"]
# No global dotenv-load: `just test` must stay provably key-free (CLAUDE.md); recipes that
# need .env (test-integration, dev, migrate, bootstrap) source it explicitly themselves.
export PATH := env_var("HOME") + "/.local/bin:" + env_var("PATH")

default:
    @just --list

# One-command setup (idempotent). See setup.sh for the plain-language version.
setup:
    ./setup.sh

# Start compose services (core only; add profiles with `just up search s3 notify`)
up *profiles:
    docker compose $(for p in {{profiles}}; do printf -- "--profile %s " "$p"; done) up -d --wait

# Stop compose (wildcard profile: a plain `down` leaves `just up search|s3|notify` running)
down:
    docker compose --profile "*" down

# Stop everything: the run, the worker, API/web, the GPU servers, compose (--dry-run, --no-docker)
stop *args:
    set -a; [ -f .env ] && . ./.env; set +a; uv run content-factory stop {{args}}

# Stop the production run and nothing else (--after-stage finishes the stage in flight first)
stop-run *args:
    set -a; [ -f .env ] && . ./.env; set +a; uv run content-factory stop --runs {{args}}

# Re-check everything and explain failures in plain language
doctor:
    set -a; [ -f .env ] && . ./.env; set +a; uv run content-factory doctor

# Build the upstream envs the post chain calls into (Cutie, ProPainter, GIMM-VFI); RIFE/SeedVR2 have theirs
setup-postchain *tools:
    skills/video/postchain/setup_envs.sh {{tools}}

# Build the two speech-restoration skill envs; weights: scripts/download_video_stack_extras.sh
setup-speech-restoration:
    cd skills/audio/resemble_enhance && uv sync
    cd skills/audio/clearervoice && uv sync

# Make something: one workflow, end to end, one line per stage (see `workflows list`)
make workflow *args="":
    set -a; [ -f .env ] && . ./.env; set +a; uv run content-factory make {{workflow}} {{args}}

# Every workflow, one line each
workflows:
    uv run content-factory workflows list

# Build the reference library index from /mnt/fast/reference (needed by find_reference)
reference:
    uv run content-factory reference build

# Ask the reference library a question in words
reference-search question:
    uv run content-factory reference search "{{question}}"

# Run one workflow's stages locally, real backends from .env (HiDream/ComfyUI start themselves)
run-local workflow="hybrid-video" *args="":
    set -a; [ -f .env ] && . ./.env; set +a; uv run content-factory run-local {{workflow}} {{args}}

# What the other producers have finished (read-only; one ssh per host, no media)
remote-list *args:
    set -a; [ -f .env ] && . ./.env; set +a; uv run content-factory remote list {{args}}

# Bring finished deliverables home from the other producers (--dry-run first)
harvest *args:
    set -a; [ -f .env ] && . ./.env; set +a; uv run content-factory remote harvest {{args}}

# Regenerate cross-language contracts (Pydantic -> JSON Schema -> TS -> Ajv)
schemas:
    scripts/schemas.sh

# Fail if generated contracts are out of date
schemas-check:
    scripts/schemas.sh --check

fmt:
    uv run ruff format .
    uv run ruff check --fix .

lint:
    uv run ruff format --check .
    uv run ruff check .
    pnpm run lint

typecheck:
    uv run pyright
    pnpm -r --if-present run typecheck

# Core tests: no internet, API keys, GPU, or live accounts required
# (marker expression lives in pyproject.toml [tool.pytest.ini_options] addopts)
test:
    uv run pytest -q
    pnpm -r --if-present run test

# Blender scene skill: pure-module tests (no Blender) — the live render test is `-m blender`
test-blender-skill:
    cd skills/video/blender_scene && uv run pytest -q tests

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
