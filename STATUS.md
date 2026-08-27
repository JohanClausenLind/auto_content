# STATUS

Last updated: 2026-08-27 (session 1). Machine: vegaserv (Ubuntu 24.04, i9-12900K, 31 GB RAM,
RTX 3090 24 GB, driver 595.84, Docker 29.7.2, FFmpeg 6.1.1, Python 3.12.3, Node 24.19.0).

## Phase checklist
- [x] **Phase 0 — research and spikes** (gate: all spikes pass; no license blocker) — **GREEN**
      except one manual check that needs the operator (Tailscale operator rights, see below).
- [~] **Phase 1 — foundation** — backend GREEN, web shell in progress.
      Done: monorepo, lockfiles, CI (`.github/workflows/ci.yml`), Postgres schema + Alembic
      migration (`workspace_id` on domain rows), local auth API (Argon2id password, TOTP with replay
      protection, passkeys register/login, step-up, sessions, RBAC deny-by-default), ArtifactStore
      (filesystem + S3 via moto tests, signed URLs), signed SkillRegistry (Ed25519), model catalog +
      ExecutionPolicy presets + routing decisions, HardwareProbe + deterministic mocks, CLI
      login/context/workspaces, bootstrap (owner + 2 demo workspaces), Temporal worker entry point.
      Pending: `apps/web` + `packages/web-ui` (theme engine, command palette, PWA shell — subagent),
      clean-checkout `./setup.sh` rehearsal (`scripts/gate-clean-checkout.sh`), theme sync check.
      Gate: clean-checkout `./setup.sh` → running app; doctor green; theme switching persists and
      syncs; two seeded workspaces isolated through the API (✔ `tests/api`).
- [ ] Phase 2 — typed contracts + deterministic static/video rendering
- [ ] Phase 3 — narration and audio
- [ ] Phase 4 — research, evidence, claims
- [ ] Phase 5 — skills, models, routing, ComfyUI
- [ ] Phase 6 — durable pipeline
- [ ] Phase 7 — editor, QC, Revision Box, image sequences
- [ ] Phase 8 — product UX, PWA, assistant, Tailscale
- [ ] Phase 9 — scheduler and first live publishing
- [ ] Phase 10 — Tier 2/3 adapters and analytics
- [ ] Phase 11 — personas, human tasks, engagement, style exploration
- [ ] Phase 12 — feature wave
- [ ] Phase 13 — hardening

## Phase 0 results (commands actually run)
| Spike | Command | Result |
|---|---|---|
| Schema → TS → Ajv roundtrip | `uv run python scripts/export_schemas.py && pnpm --filter @content-factory/content-schema-ts run generate && (cd packages/content-schema-ts && pnpm exec vitest run)` | 13 schemas, 41 defs; 10/10 tests pass (Python-emitted valid fixtures accepted, invalid rejected) |
| EditorCore apply/undo/replay | `cd packages/editor-core && pnpm exec tsc --noEmit && pnpm exec vitest run` | 8/8 pass; undo restores exact prior hash; redo reproduces hash; claim-linked edit reopens evidence |
| Remotion local-font clip + ffprobe | `cd apps/renderer && node scripts/render-spike.mjs` | `out/smoke-title.mp4`: h264 1920×1080 30 fps yuv420p 90 frames 3.000 s, moov before mdat |
| Temporal workflow + replay | `uv run pytest tests/integration/test_temporal_spike.py -m integration` then `uv run pytest tests/unit/test_temporal_replay_offline.py` | parks at WAITING_FOR_APPROVAL, signal completes it; history saved to `fixtures/temporal/`; offline replay passes; drifted code rejected |
| ComfyUI pinned workflow: validate/execute/cancel/import | `uv run pytest tests/unit/test_comfyui_client.py` | 5/5 pass against the fixture server (object_info validation, allowlist, provenance hashes, mid-run cancel, server rejection) |
| Revision Box complaint → FixPlan | `uv run pytest tests/unit/test_critique_mapping.py` | 7/7 pass (single-unit FixPlan, clarifying question, citation refusal, evidence gate, publish-scope gate) |
| Deterministic control compiler | `uv run pytest tests/unit/test_control_compile.py` | 3/3 pass; 8 pose + 8 layout frames byte-identical on rerun |
| Local auth + passkey | `uv run pytest tests/unit/test_auth_primitives.py` | 4/4 pass (Argon2id, TOTP replay refused, passkey register+auth, stale challenge/wrong origin rejected) |
| Lint/type | `uv run ruff check . && uv run pyright python tests scripts` | 0 findings / 0 errors |
| Tailscale serve | `tailscale serve --bg --https=8443 8000` | **BLOCKED (needs operator)**: "Use 'sudo tailscale serve …' or `sudo tailscale set --operator=$USER` once". Existing serve on 443 → 127.0.0.1:8002 (another app) was left untouched. API on loopback verified: `curl 127.0.0.1:8000/healthz` → ok |

## Phase 1 results so far (commands actually run)
| Item | Command | Result |
|---|---|---|
| Migrations | `uv run alembic revision --autogenerate` → `alembic upgrade head` (dev + test DB) → `alembic check` | applied; "No new upgrade operations detected" |
| Auth + isolation API tests | `set -a; . ./.env; set +a; uv run pytest tests/api -m integration` | 6/6 pass: login/logout, two-workspace isolation (foreign id → 404, switch → 403), RBAC (viewer 403), prefs per account, TOTP MFA + replay refused + step-up, passkey register → passwordless login → replay refused |
| ArtifactStore | `uv run pytest tests/unit/test_artifact_store.py` | 4 tests × 2 backends (filesystem, moto S3) pass |
| Skills/routing | `uv run pytest tests/unit/test_skills_and_routing.py` | 7/7: unsigned/foreign/tampered manifests refused; lifecycle; local_only zero cloud + pause; weak-but-fitting model rejected by floor; prefer_local visible fallback; caps → pause_budget; pinned fails closed |
| Config invariants | `uv run pytest tests/security` | funnel without MFA refused; bind 0.0.0.0 refused; unknown keys (e.g. billing) refused |
| CLI | `content-factory serve` + `login/workspaces list/whoami/logout` | works; context file mode 0600 |
| Bootstrap | `uv run content-factory bootstrap` | owner `operator` + `demo-editorial`, `demo-brand` |
| Lint/type | `ruff check`, `pyright` | 0 findings |

Unit suite: `uv run pytest -m "not integration"` → 41 passed. Node: 21 tests pass (pre-web).

## Decisions (see docs/adr/0001–0010)
- MinIO archived upstream 2026-04-25 → optional S3 service is SeaweedFS 4.44 (Apache-2.0).
- HiDream-O1-Image (MIT) confirmed real with native ComfyUI nodes; pose/layout control via
  Qwen-Image-Edit-2509 until a HiDream-O1 control node exists.
- Local TTS default Kokoro-82M (token timestamps); Chatterbox documented alternative; audio skills
  run in isolated envs (torch pin conflicts).
- comfy-mcp is AGPL → optional separate process, Workflow Lab only.
- Compose ports avoid 8080/8082/8091 (used by other stacks here): SearXNG 8083, ntfy 8092, S3 8333.
- Existing comfy-cli workspace `~/git/ComfyUI` (0.33.0, 69 GB models) can be pointed to via
  `comfyui.workspace`; no second install performed.
- Repo license: Apache-2.0 (operator can change; nothing depends on it).
- TypeScript 7.0.2 (native compiler) is what pnpm resolved as current; all packages typecheck.

## Verified-vs-assumed dependency ledger
Verified from registries/official docs on 2026-08-27 (see docs/research/*.md for URLs): every
pin in `pyproject.toml`, `package.json`s, `docker-compose.yml` image tags (postgres:18-alpine,
temporalio/temporal:1.8.2, searxng/searxng:2026.8.22-9fea41204, chrislusf/seaweedfs:4.44,
binwiederhier/ntfy:v2.27.0). Assumed (to verify when first used): Kokoro CPU/GPU speed, Chatterbox
VRAM, HiDream-O1 peak VRAM, Bluesky video caps, Telegram channel-admin requirement, MJML Node
minimum, Buttondown API tier.

## Known limitations / follow-ups
- Starlette warns that `httpx` TestClient support is deprecated in favour of `httpx2`; evaluate
  in phase 1 before wiring API tests.
- Remotion first render downloaded Chrome Headless Shell into `node_modules/.remotion` (needs
  internet once; setup.sh does it explicitly).
- `tests/unit/test_temporal_replay_offline.py` skips if the history fixture is absent; the fixture
  is committed.

## Resume instruction (next smallest task)
Finish Phase 1: integrate `apps/web` + `packages/web-ui` (verify `pnpm -r test`, `pnpm -r
typecheck`, `pnpm --filter @content-factory/web build`), run `scripts/gate-clean-checkout.sh`,
confirm theme PUT/GET `/v1/prefs/theme` round-trips from the UI, update this file, commit.
Then Phase 2 (typed contracts + deterministic static/video rendering).
Run: `set -a; . ./.env; set +a; just doctor && just test`.
