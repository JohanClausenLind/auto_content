# Content Factory

Self-hosted, local-first, single-operator content production and distribution platform.
Private project — no billing, no SaaS surfaces. See `STATUS.md` for the current phase.

## What works now (phases 0–8 and 11–12 GREEN; 9, 10, 13 gated on the operator — verified on this machine)

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
- `./setup.sh` → running API + web shell; `content-factory doctor` green; docker compose
  (Postgres 18, Temporal dev server); Alembic migrations.
- Local auth: password (Argon2id), TOTP, passkeys, step-up, sessions; deny-by-default RBAC;
  two workspaces provably isolated through the API.
- Web shell (PWA): themes (9 presets, customizer, import/export, per-account sync), command
  palette (⌘K), all areas as designed empty states, login with password/TOTP/passkey.
- CLI: `login`, `whoami`, `workspaces list|use`, `bootstrap`, `serve`, `worker`, `doctor`.
- ArtifactStore (filesystem + S3), signed skill registry, execution-policy routing with
  explainable decisions, hardware probe.
- Content contracts → pruned DAG → deterministic renders: `content-factory demo` produces a
  data-card PNG and a narrated 16 s vertical MP4 (mock voice, −14 LUFS, captions, claim-gated
  citations) that pass QC and are byte-identical on rerun.
- A durable Temporal pipeline: preflight approval bound to exact revisions, worker-kill resume
  without duplicate work, per-card rebuild caching, Revision Box (feedback → typed FixPlan →
  apply → targeted rebuild → undo), human task slots that park the workflow and gate publishing.
- Research/claims machinery (SSRF-safe fetch, claim verification, citations), originality engine,
  image-sequence engine (anchor/lock/hub-and-spoke/drift QC/flipbook PDF), persona firewall,
  style experiments, cost ledger + zero-egress local routing, token vault + OAuth broker,
  Tier-1 publishing adapters (Bluesky/Mastodon/Discord) with exactly-once intents — mocks only
  until the operator authorizes a test account.
- Web app: create flow with an honest deliverable matrix, run view with a React Flow pipeline
  canvas, approvals with step-up, Action Center, operations, themes, PWA + Web Push.
- MCP server (`content-factory mcp`) as the only external-agent surface.
- Workflow catalogue: 16 lanes in `workflows/*.yaml`, one file per lane, one command to run one.
  Definitions are validated against the contract *and* the generated node catalogue, so a typo'd
  widget key fails loudly instead of falling back to a stage default.
- Node-graph Workspace: an editable ComfyUI-style canvas (`packages/node-graph`, `@xyflow/react`)
  with typed slots, widgets, groups, undo/redo, a node library and search. A run can be laid over
  it: each step's output hangs off the node that produced it, and a node's own count opens every
  frame, voice line and film that step made, with the stage's facts beside them.
- Local AI stack, laid out ComfyUI-style under `external/` + `models/`: image (HiDream-O1),
  video (LTX-2.5 image-to-video, Krea2), text (local copywriter via qwen38-ridge), each behind
  a resource lease with OOM quarantine and measured VRAM eviction.
- Audio chain end to end: Qwen3-TTS narration (15 Kokoro + 17 Breeze voices available),
  speech restoration between take and mix, MiniMax-Music3 for non-verbal score, and a
  Stable Audio 3 Small-SFX library (mostly real recordings).
- Blender scene-control layer (ADR 0012), phases 0-8: deterministic control images from a
  posed rig, driven by a queryable reference library of real human interaction and CMU mocap.
- Run observability: run history (searchable, grouped by day, each run named by what it was
  rendering), per-stage durations and ETA, per-node output attribution recorded by the run itself,
  and frame review (a recorded verdict on a generated frame becomes a question or a refusal, not a
  silent edit).
- A vision model as the frame gate's third reviewer: it is shown the story, each frame's intent and
  the whole set in one call, and answers what the measurements cannot — whether these are the same
  subject in the same world, and which frame left the others behind. Its output is an opinion stored
  beside the batch and bound to the images' digests; it pre-marks the operator's selection and can
  never record a verdict.

## Not done yet

Three gates remain, and each needs the operator rather than more code
(`STATUS.md` → "Resume instruction"):

- **Nothing has ever been published.** Phase 9 is green through the idempotent publisher,
  exactly-once intents and the kill switch, all proven against mocks and chaos tests. The live
  gate is still shut: put a designated TEST account's `BLUESKY_HANDLE` / `BLUESKY_APP_PASSWORD`
  in `.env`, then `uv run pytest -m live tests/live -q`.
- **Tier 2/3 platform adapters are package-only.** Constraints are researched
  (`docs/research/tier2-tier3-platform-constraints.md`); each needs its own developer app and a
  live draft test. Article/newsletter destinations (WordPress, Ghost, Listmonk) are green offline
  as drafts only.
- **External security review outstanding** (phase 13). `docs/requirements-traceability.md` maps
  capability → code → proof for a reviewer.

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
just stop           # stop everything: the run, the worker, API/web, the GPU servers, compose
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
