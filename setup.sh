#!/usr/bin/env bash
# Content Factory one-command setup (idempotent, re-runnable). Plain-language remediation on failure.
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✘\033[0m %s\n' "$*"; exit 1; }

say "1/7 Prerequisites"
command -v docker >/dev/null || die "Docker is missing. Install Docker Engine: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "Docker Compose plugin is missing (docker compose version failed)."
docker info >/dev/null 2>&1 || die "Docker daemon not reachable. Start Docker or add your user to the 'docker' group."
command -v node >/dev/null || die "Node.js is missing. Install Node 22 or 24 LTS: https://nodejs.org"
node -e 'const [maj]=process.versions.node.split(".").map(Number); if (maj<22||maj>=25) process.exit(1)' || die "Node $(node --version) unsupported; use 22.x or 24.x LTS."
command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || die "FFmpeg/ffprobe missing: sudo apt install ffmpeg"
command -v uv >/dev/null || { warn "uv missing — installing to ~/.local/bin"; curl -LsSf https://astral.sh/uv/install.sh | sh; }
command -v pnpm >/dev/null || { warn "pnpm missing — enabling via corepack into ~/.local/bin"; corepack enable --install-directory "$HOME/.local/bin"; }
command -v just >/dev/null || { warn "just missing — installing via uv tool"; uv tool install rust-just >/dev/null; }
if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then ok "GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -1)"; else warn "No NVIDIA GPU detected — CPU-only mode (GPU skills unavailable)."; fi
ok "docker $(docker --version | awk '{print $3}' | tr -d ,), node $(node --version), ffmpeg $(ffmpeg -version | head -1 | awk '{print $3}'), uv $(uv --version | awk '{print $2}'), pnpm $(pnpm --version)"

say "2/7 Environment file"
if [ ! -f .env ]; then cp .env.example .env; ok "created .env from .env.example (edit ports/secrets there; it is git-ignored)"; else ok ".env exists — left untouched"; fi
if ! grep -Eq '^VAULT_MASTER_KEY=[A-Za-z0-9+/=]{40,}$' .env; then
  key=$(python3 -c 'import base64,os;print(base64.b64encode(os.urandom(32)).decode())')
  sed -i "s|^VAULT_MASTER_KEY=.*|VAULT_MASTER_KEY=$key|" .env
  ok "generated VAULT_MASTER_KEY (token vault envelope key). Back it up: losing it means reconnecting every account."
fi
set -a; . ./.env; set +a

say "3/7 Dependencies"
uv sync --frozen >/dev/null && ok "python: uv sync --frozen"
pnpm install --frozen-lockfile >/dev/null && ok "node: pnpm install --frozen-lockfile"

say "4/7 Services (loopback only)"
profiles=()
[ "${CF_WITH_SEARCH:-0}" = "1" ] && profiles+=(--profile search)
[ "${CF_WITH_S3:-0}" = "1" ] && profiles+=(--profile s3)
[ "${CF_WITH_NTFY:-0}" = "1" ] && profiles+=(--profile notify)
docker compose "${profiles[@]}" up -d --wait >/dev/null && ok "postgres 127.0.0.1:${CF_POSTGRES_PORT:-5432}, temporal 127.0.0.1:${CF_TEMPORAL_PORT:-7233} (UI http://127.0.0.1:${CF_TEMPORAL_UI_PORT:-8233})"
[ ${#profiles[@]} -eq 0 ] && warn "optional services skipped (set CF_WITH_SEARCH=1 / CF_WITH_S3=1 / CF_WITH_NTFY=1 to enable SearXNG / SeaweedFS / ntfy)"

say "5/7 Contracts and database"
uv run python scripts/export_schemas.py >/dev/null && pnpm --filter @content-factory/content-schema-ts run generate >/dev/null && ok "schemas exported and TypeScript types generated"
uv run alembic upgrade head >/dev/null && ok "migrations applied (development database)"
if [ -n "${DATABASE_URL_TEST:-}" ]; then ALEMBIC_DATABASE_URL="$DATABASE_URL_TEST" uv run alembic upgrade head >/dev/null && ok "migrations applied (test database)"; fi
uv run content-factory bootstrap --owner "${CF_OWNER_USERNAME:-operator}" ${CF_OWNER_PASSWORD:+--password "$CF_OWNER_PASSWORD"} | sed 's/^/  /'


say "6/7 Renderer"
if [ "${CF_SKIP_BROWSER:-0}" != "1" ]; then
  warn "Remotion downloads Chrome Headless Shell (~150 MB) on first use into node_modules/.remotion"
  (cd apps/renderer && node -e 'import("@remotion/renderer").then(m=>m.ensureBrowser()).then(()=>console.log("  ✔ headless browser ready"))')
else
  warn "skipped browser download (CF_SKIP_BROWSER=1)"
fi

say "7/7 Optional: ComfyUI sidecar"
if command -v comfy >/dev/null; then
  ok "comfy-cli $(comfy --version 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["version"])' 2>/dev/null || echo present). Not installed automatically (multi-GB). When needed: comfy --workspace=./.comfy install --nvidia"
else
  warn "comfy-cli not installed. Optional: uv tool install comfy-cli"
fi

say "Done. What is running:"
docker compose ps --format '  {{.Name}}  {{.Status}}  {{.Ports}}'
echo
echo "Next: 'just doctor' re-checks everything; 'just test' runs the offline suites; 'just render-smoke' renders the smoke clip."
