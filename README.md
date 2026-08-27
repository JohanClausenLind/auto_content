# Content Factory

Self-hosted, local-first, single-operator content production and distribution platform.
Private project — no billing, no SaaS surfaces. See `STATUS.md` for the current phase.

## What works now (phase 0 — spikes verified on this machine)
- Typed contracts: Pydantic → JSON Schema 2020-12 → TypeScript + Ajv, with drift check.
- EditorCore: typed edit operations applied and undone to identical revision hashes.
- Remotion renderer: 3 s 1080p clip with a pinned local font, verified by ffprobe.
- Temporal: durable workflow parks on an approval signal (no worker consumed), completes,
  and its recorded history replays offline; drifted code is detected.
- ComfyUI adapter: pinned API-format workflow validated against `/object_info`, executed against
  a fixture server, cancelled mid-run, outputs imported with provenance.
- Revision Box mapper: complaint → FixPlan on exactly one unit; ambiguity → one question;
  "remove the citation" → refusal; factual change → evidence gate.
- Deterministic control-image compiler: two-keyframe MotionPlan → 8 byte-identical skeleton and
  layout frames.
- Local auth primitives: Argon2id, TOTP with replay protection, passkey registration and
  authentication round trip.
- `content-factory doctor`, typed configuration, docker compose (Postgres 18, Temporal dev server).

## Not implemented yet
Everything in phases 1–13 of the program: database schema, web app, production pipeline,
research/claims, TTS, distribution, analytics, personas, engagement. Nothing publishes anywhere.

## Prerequisites
Linux (tested on Ubuntu 24.04), Docker Engine + Compose plugin, Node 22/24 LTS, Python 3.12,
FFmpeg 6+, optional NVIDIA GPU (tested: RTX 3090, driver 595). `uv`, `pnpm`, `just` are
installed by `./setup.sh` if missing.

## Quick start
```bash
./setup.sh          # prerequisites, .env, deps, compose services, contracts, headless browser
just doctor         # plain-language health check
just test           # offline suites (no internet, keys, GPU, or accounts)
just render-smoke   # apps/renderer/out/smoke-title.mp4 + ffprobe assertions
```

## Tailscale access
The app binds loopback. This machine already serves another app on 443, so use a separate port:
```bash
sudo tailscale set --operator=$USER          # once
tailscale serve --bg --https=8443 8000       # https://<host>.<tailnet>.ts.net:8443
tailscale serve --https=8443 off             # remove
```
Funnel (public) refuses to start unless password auth + MFA are enabled.

## Documentation
`docs/adr/` (10 decisions), `docs/research/` (dated official-doc research), `docs/licensing.md`,
`docs/scale-later.md`.
