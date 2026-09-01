# STATUS

Last updated: 2026-08-27 (session 1). Machine: vegaserv (Ubuntu 24.04, i9-12900K, 31 GB RAM,
RTX 3090 24 GB, driver 595.84, Docker 29.7.2, FFmpeg 6.1.1, Python 3.12.3, Node 24.19.0).

## Phase checklist
- [x] **Phase 0 — research and spikes** (gate: all spikes pass; no license blocker) — **GREEN**
      except one manual check that needs the operator (Tailscale operator rights, see below).
- [x] **Phase 1 — foundation** — **GREEN** (gate rehearsed 2026-08-27).
      Monorepo, lockfiles, CI, Postgres schema + Alembic, local auth API (Argon2id, TOTP, passkeys,
      step-up, sessions, deny-by-default RBAC), ArtifactStore (filesystem + S3), signed
      SkillRegistry, model catalog + ExecutionPolicy presets + routing decisions, HardwareProbe +
      mocks, CLI login/context/workspaces, bootstrap, Temporal worker entry, web shell
      (`apps/web`) with theme engine + command palette + PWA (`packages/web-ui`).
- [x] **Phase 2 — typed contracts + deterministic static/video rendering** — **GREEN** (2026-08-27).
      29 registered contracts (campaign/deliverable union, full SceneSpec grammar, artboards/layers,
      DeliverableDAG, StoryPlan/CompiledTimeline, RenderBundle), DAG compiler with typed pruning,
      timeline compiler (ms → integer frames), content-ui design system + Artboard, video-ui scenes
      + TimelineComposition, renderer scripts, media QC, demo runner.
- [x] **Phase 3 — narration and audio** — **GREEN** (2026-09-01).
      `voice.synthesize` contract with three executors (deterministic mock; ElevenLabs
      with-timestamps, respx-tested; Kokoro-82M in an isolated uv env at `skills/audio/kokoro`,
      opt-in), speech normalization + pronunciation lexicon, alignment validation, SRT/WebVTT
      captions, narration stem layout, two-pass loudnorm mastering to -14 LUFS/-1 dBTP, mux,
      audio QC (loudness, true peak, long silences, A/V duration), narrated timeline compilation
      from measured word timings.
- [x] **Phase 4 — research, evidence, claims** — **GREEN** (2026-09-01).
      Source/Evidence/Claim contracts (+ EvidenceRequirementPlan), SSRF-safe fetch (private ranges,
      redirect re-validation, decompressed size cap, MIME allowlist, egress allowlist, injection
      scan), SearchProvider (SearXNG + fixture), extraction (trafilatura article, pypdf, safe
      feeds via defusedxml), deterministic claim classification (2.14), numeric verification with
      unit/period/rounding handling + staleness policy, citations export + script claim gate,
      reproducible Polars transforms, magic-sniffed upload ingestion. Demo now exports
      research/{sources,evidence,claims}.json + final/sources.md + citations.json and blocks on
      the claim gate.
- [x] **Phase 5 — skills, models, routing, ComfyUI** — **GREEN** (2026-09-01).
      Cost ledger (estimate→reserve→settle, hard caps, warn thresholds, thread-safe, property-
      tested), resource leases with measured-calibration override + OOM quarantine, ModelGateway
      (litellm 1.99.0 + schema-validated retries; local_only provably zero cloud calls; skills
      without network egress never see cloud candidates; credentials from env only; budget settle
      on success, release on failure), `image.generate` through ComfyUI fixture AND mock cloud via
      one invocation path, evaluation packs with approval lifecycle (approve/revoke per model x
      skill), allowlisted `comfy model download` planner with hash verification.
- [x] **Phase 6 — durable pipeline (backend)** — **GREEN** (2026-09-01); Pipeline Canvas UI in progress.
      Durable ProductionWorkflow (CREATED→PREFLIGHTING→WAITING_FOR_APPROVAL→APPROVED→PRODUCING→
      COMPLETE), approval signal bound to the exact preflight revision (stale approvals recorded and
      ignored), ActionItems (open on waiting, resolved on decision), idempotent cached stage
      activities (input hash = campaign + quality + dependency outputs + edit overlays), per-card
      render cache, heartbeats for fast failover, run/node persistence, /v1/runs + /v1/action-items
      API, CLI `runs start|status|approve`.
- [x] **Phase 7 — editor/QC/Revision Box/image sequences (backend)** — **GREEN** (2026-09-01); editor UI arrives with the web agent.
      Revision Box loop (feedback → typed FixPlan/question/refusal/gate → apply as append-only
      overlay revisions → targeted rebuild → undo chain), /v1/revisions API, mask rasterization
      (rect/polygon/brush, subtract/invert/feather/expand/protect, byte-identical), delivery-promise
      QC (pan-zoom slideshow detection with sub-pixel compensation), accessibility pack (flashing/
      PSE, reading order, alt text, exportable report), image-sequence engine (anchor + GenerationLock
      + deterministic controls + hub-and-spoke + drift QC + bounded regen + contact sheet/MP4
      preview/print flipbook PDF; single-frame revisions rebuild exactly one frame).
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

| Web shell | `pnpm -r test` / `pnpm -r typecheck` / `pnpm --filter @content-factory/web build` | 67 Node tests pass (web-ui 32, web 14, schema-ts 10, editor-core 8, renderer 3); 0 TS errors; `dist/` built |
| Theme sync (live) | API + Vite dev proxy: login → `PUT /v1/prefs/theme` → `GET` | 200 / 204 / `{"value":{"preset":"midnight","scale":1.1}}` |
| In-place `./setup.sh` | `CF_SKIP_BROWSER=1 ./setup.sh` then `just doctor` | idempotent; doctor "All checks passed" (comfyui/searxng optional warns) |
| Clean-checkout gate | `scripts/gate-clean-checkout.sh` (git clone → ./setup.sh → doctor --json → tests) | "doctor fails: []", 41 passed, gate OK |

Core suite: `uv run pytest -m "not integration and not gpu and not live"` → 41 passed; `pnpm -r test` → 67 passed.

## Phase 2 results (commands actually run)
| Item | Command | Result |
|---|---|---|
| Contracts roundtrip | `just schemas` + `pnpm --filter @content-factory/content-schema-ts test` | 29 schemas / 128 defs; 17/17 (valid fixtures accepted; bad layer kind + bad visibility rejected) |
| DAG compiler | `uv run pytest tests/unit/test_dag_compiler.py` | 6/6: image-only → no audio/video stages; article-only → no timeline; shared stages once; narration=False prunes TTS with typed reason; deterministic hash |
| Timeline compiler | `uv run pytest tests/unit/test_timeline_compiler.py` | 4/4 incl. Hypothesis: integer frames, contiguous, refuses to freeze narrated timing before measurement |
| Renderer determinism | `pnpm --filter @content-factory/renderer test` | 6/6: PNG byte-identical across runs; scene MP4 byte-identical (sha `2304538567d0a753…`) and frame-identical |
| Offline demo | `uv run content-factory demo --quality smoke` / `--quality demo` | PASS both deliverables; PNG 1080×1080 sha `ae4ff8bd…` (matches subagent's independent render); MP4 h264 yuv420p 1080×1920 30 fps 518 frames 17.27 s faststart sha `e98a5089…` (matches) |
| Media QC | `uv run pytest tests/unit/test_media_qc.py` | 3/3 (blank/dimension/fps/black-frame detection on synthetic clips) |
| Visual review | frames 60/200/330/480 + artboard PNG inspected | legible editorial layout; follow-up: cross-scene vertical anchor consistency (phase 7 QC) |

Core suites now: Python 49 unit + 5 security; Node 105 tests (schema-ts 17, web-ui 32, content-ui 21, editor-core 8, web 14, video-ui 7, renderer 6).

## Phase 3 results (commands actually run)
| Item | Command | Result |
|---|---|---|
| Audio units | `uv run pytest tests/unit/test_audio_tts_and_alignment.py tests/unit/test_captions_and_mix.py` | 8/8: mock determinism (same request → identical bytes/timings), alignment catches injected overlap/missing/gap, character→word collapsing, ElevenLabs adapter parses `with-timestamps` (mocked), captions ≤2 lines non-overlapping, mastering reaches target |
| Narrated demo | `uv run content-factory demo --quality demo` | PASS: 16.4 s 1080×1920 MP4, integrated −14.0 LUFS, true peak −8.6 dBTP, AV delta 7 ms, alignment green, 4 caption cues, stems + SRT/VTT on disk |
| e2e | `tests/e2e/test_offline_demo.py` (in core suite) | narrated demo gates asserted end to end |

## Phase 6/7 results (commands actually run)
| Item | Command | Result |
|---|---|---|
| Approval binding + card rebuild | `uv run pytest tests/integration/test_production_workflow.py::test_full_run_...` | stale-revision approval ignored (workflow query shows rejection); exact revision approves; every node executed once; after a card-2 edit only card 2 re-rendered (cards 1/3 cached, image branch cache_hit=True) |
| Worker kill | `...::test_worker_kill_resumes_without_duplicate_executions` | SIGKILL mid-production; resumed on a fresh worker; nodes completed before the kill executed exactly once; ≤1 node executed twice |
| Revision loop | `uv run pytest tests/unit/test_revision_apply.py tests/unit/test_critique_mapping.py` | apply → overlay revision; undo chain restores prior states; refusal/gate never apply |
| Sequence gate | `uv run pytest tests/unit/test_image_sequences.py` | 5/5: hub-and-spoke (all generations reference the anchor), locks intact, injected drifted frame regenerated from the anchor, persistent drift fails honestly, flipbook PDF 6 pages + h264 preview, single-frame revision → exactly one new generation |
| Delivery/a11y QC | `uv run pytest tests/unit/test_delivery_and_accessibility_qc.py tests/unit/test_masks.py` | pan-zoom slideshow FAILS the animated-explainer promise; real animation passes; strobe blocked; alt-text/reading-order enforced; masks byte-identical |

## Phase 4 results (commands actually run)
| Item | Command | Result |
|---|---|---|
| SSRF/hostile files | `uv run pytest tests/security/test_ssrf_and_hostile_files.py` | 11/11: private/link-local/v6/credentialed URLs blocked, redirect-to-private blocked, decompression bomb capped, content-type allowlist, egress allowlist, billion-laughs feed refused, hostile PDF fails closed, injection markers flagged as data |
| Claims | `uv run pytest tests/unit/test_claims_and_citations.py` | 8/8: kinds classified, numbers parsed (units/scales/periods), supported/caveat/unsupported/dataset verification, staleness, operator assertions never silently verified, high-stakes blocks full_auto, script gate blocks uncited critical claims |
| Transforms/uploads | `uv run pytest tests/unit/test_transforms_and_uploads.py` | 3/3: derived data reproduces to identical content hash; fail-closed transforms; magic-based upload validation (HTML-as-mp4 rejected, svg rejected) |
| Search/extract | `uv run pytest tests/unit/test_search_and_extract.py` | 3/3: SearXNG JSON adapter, fixture provider overlap match, article extraction drops scripts |
| Demo | `uv run content-factory demo --quality smoke` | claim gate PASS, research/ + final/sources.md + citations.json written |

Follow-ups: AssetLibrary records and RSS→ContentSourceEvent connector land with their first
consumer (phases 6/12); WhisperX forced-alignment fallback and the Kokoro executor need model downloads;
they are contract-complete but not evaluated — evaluation packs land in phase 5.

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
