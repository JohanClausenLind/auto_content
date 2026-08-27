# Setup

Tested on Ubuntu 24.04 with Docker 29, Node 24, Python 3.12, FFmpeg 6.1, RTX 3090 (optional).

## One command
```bash
./setup.sh
```
It is idempotent. It checks prerequisites with plain-language remediation, creates `.env` from
`.env.example` (and generates `VAULT_MASTER_KEY` — back it up), installs Python (`uv sync
--frozen`) and Node (`pnpm install --frozen-lockfile`) dependencies, starts Postgres 18 and the
Temporal dev server on loopback, exports contracts, applies migrations to the dev and test
databases, bootstraps the owner account (`operator`, password printed **once**) and two demo
workspaces, and downloads Chrome Headless Shell for the renderer (~150 MB, once).

Optional services: `CF_WITH_SEARCH=1` (SearXNG on 127.0.0.1:8083), `CF_WITH_S3=1` (SeaweedFS S3 on
127.0.0.1:8333), `CF_WITH_NTFY=1` (ntfy on 127.0.0.1:8092). Ports are chosen to avoid 8080/8082/8091
which other local stacks commonly use; change them in `.env`.

## Run
```bash
just dev          # API on 127.0.0.1:8000, web on 127.0.0.1:3000 (proxying /v1)
just doctor       # health check
just test         # offline suites
just test-integration
```
Sign in with `operator` and the printed password, or `content-factory login`.

## ComfyUI (optional, later phases)
Not installed automatically (multi-gigabyte). Either point `comfyui.workspace` at an existing
comfy-cli workspace (this machine: `~/git/ComfyUI`) or run
`comfy --workspace=./.comfy install --nvidia` then `comfy launch --background`.

## Tailscale
See README "Tailscale access". The app never binds beyond loopback; `tailscale serve` provides
tailnet HTTPS. Funnel requires password auth + MFA (enforced at config validation).

## Resetting
`docker compose down -v` deletes Postgres and Temporal data. `rm -rf data/` deletes artifacts.
Both are local-only; nothing external is touched.
