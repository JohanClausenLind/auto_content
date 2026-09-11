# STATUS

Last updated: 2026-09-10. Machine: vegaserv (Ubuntu 24.04, i9-12900K, 31 GB RAM,
RTX 3090 24 GB, driver 595.84, Docker 29.7.2, FFmpeg 6.1.1, Python 3.12.3, Node 24.19.0).

**Current state.** Phases 0–8 and 11–12 are GREEN. Phase 9 is green through the idempotent
publisher and exactly-once intents but its live gate has never been opened — **nothing has ever
been published**. Phase 10 is green offline (analytics, originality, article/newsletter drafts);
remaining Tier 2/3 adapters are package-only. Phase 13 is green but for an external security
review. See "Resume instruction" for what that means for the next session.

## Resume instruction (next smallest task)

**Everything buildable without the operator is built.** The three remaining gates each need the
operator, not more code:

1. **Live Bluesky gate.** Put a designated TEST account's `BLUESKY_HANDLE` /
   `BLUESKY_APP_PASSWORD` in `.env` (the keys are present and empty), then
   `set -a; . ./.env; set +a; uv run pytest -m live tests/live -q`.
   Until this runs, nothing this repo produces has ever been published anywhere, and every
   exactly-once / kill-switch / reconciliation guarantee is proven only against mocks.
2. **Tier 2/3 + article destinations, live drafts.** Create the per-platform developer apps and
   keys, then exercise each adapter against a real draft. Mock-proven; wiring is in
   `python/content_factory/distribution/`.
3. **External security review.** `docs/requirements-traceability.md` maps capability → code →
   proof for a reviewer outside this codebase.

Code-side next smallest tasks, carried from the 2026-09-10 session and deliberately not guessed at:

- Recording a frame verdict does not resume the run — there is no local-run control plane in the
  API, so the command is printed instead. That is the gap to close first.
- The Assets page still lives outside the workspace: it reads the image-sequence output root,
  a different source from the run history, so folding it in needs a decision rather than a patch.
- `review_assets` still takes a free-text `--as <name>`.

**Baseline check before any new work:**

```
set -a; . ./.env; set +a; just doctor && just test && uv run pytest tests/api -q
```

## Phase checklist
- [x] **Phase 0 — research and spikes** (gate: all spikes pass; no license blocker) — **GREEN**
      (Tailscale operator check completed 2026-09-01, see below).
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
- [x] **Phase 6 — durable pipeline (backend)** — **GREEN** (2026-09-01); read-only run Pipeline
      Canvas shipped with phase 8; the editable node-graph Workspace shipped 2026-09-03 (below).
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
- [x] **Phase 8 — product UX, PWA, assistant, Tailscale** — **GREEN** (both operator-blocked items resolved 2026-09-01).
      Backend: Web Push (VAPID keygen in setup, subscriptions API, deep-link-only payloads),
      MCP server (`content-factory mcp`, scoped token, 8 typed tools), campaigns API (honest
      deliverable matrix, validate-and-quote preview, create), operations API (owner-only doctor +
      audit tail). UI: 3-step create flow with the matrix (unsupported types disabled with plain
      reasons), run detail + Pipeline Canvas + Revision Box + approval with step-up, Action Center,
      Operations page, Calendar (honest placeholder), Push settings, assistant drawer (honest: lists
      the MCP tools; no local model wired yet). 40 web tests + 12 canvas tests pass; build clean.
      Tailnet serve verified 2026-09-01 (operator ran `sudo tailscale set --operator=$USER`). The push/
      assistant/calendar UI tests that were cut short are now in
      `apps/web/test/calendarPushAssistant.test.tsx` (2026-09-01).
- [~] **Phase 9 — scheduler and first live publishing** — everything but the live post is GREEN.
      Token vault (AES-256-GCM envelope, context-bound AAD, key-ring rotation), OAuth broker
      (state+PKCE S256, single-use workspace-bound callbacks), Tier-1 adapters (Bluesky app-password;
      Mastodon with native Idempotency-Key + async media polling + instance limits; Discord webhook
      with mention safety), idempotent publisher (PublishIntent → validate → policy → publish once;
      ambiguous responses become blocking reconciliation, never blind retries; exactly-once proven
      under chaos 500s/timeouts), distribution profiles as immutable authorized revisions +
      kill switch (API + CLI, step-up gated), ContentProgram Temporal Schedules (overlap SKIP,
      pause-on-failure, catch-up policies; no-burst proven). LIVE gate pending operator: set
      BLUESKY_HANDLE/BLUESKY_APP_PASSWORD for a designated TEST account, then
      `uv run pytest -m live tests/live -q`.
- [~] **Phase 10 — Tier 2/3 adapters and analytics** — analytics/attribution GREEN offline:
      governed UTM builder (deterministic identity), Shopify/generic HMAC webhook verification,
      last-touch AttributionRecords (always correlation-labeled, missing stays missing), retention→
      exact scene-range mapping with no causal claims, raw observations preserved verbatim.
      Originality Engine (2.10) GREEN: text+frame shingles (noun-swap detection), hook/beat
      structure, perceptual dhash, typed decisions (ORIGINAL…MASS_PRODUCTION_RISK), declared
      adaptations, model can never override blocking. Article/newsletter destinations (21) GREEN
      offline (2026-09-01): WordPress (Application-Passwords Basic auth), Ghost (Admin API HS256
      JWT, signature byte-verified in the mock), Listmonk (draft campaigns; refuses missing
      unsubscribe or plain-text alternative) — drafts only, publishing stays behind the same
      gates as social. Other Tier 2/3 platform adapters remain package-only (constraints in
      docs/research/tier2-tier3-platform-constraints.md; each needs the operator's own developer
      app + live draft test).
- [~] **Phase 11 — personas, human tasks, engagement, style** — core GREEN:
      PersonaFirewall immutable in code (real-person deny-list, PII, injection-as-data, minor
      safety always escalates and is not configurable, crisis always escalates, exploitation/
      off-platform/meeting blocks, disclosure floor: no autonomous reply can claim to be human),
      reply governance (autonomy tiers, variation pressure vs recent replies, hourly caps,
      answer-everything skip reasons), PersonaSchedule (awake windows, deterministic jitter,
      never metronomic), human-take validation (probes + ASR-vs-script diff with tolerance,
      accept-as-performed vs re-record), human task slots IN the durable workflow (parked node
      consumes zero pending activities, ActionItem + Canvas deep link, invalid submissions
      rejected with reasons, completeness gate proven: nothing downstream ran before the slot
      filled), style explore/exploit (jittered cadence never consecutive, cooldown retests,
      ADOPT needs min samples + conservative spread, guardrail breach retires, all conclusions
      labeled observational). Completed 2026-09-01: persona persistence + /v1/personas API
      (server-assigned ids, revise=typed-diff preview, revision-bound apply, stale diff → 409) +
      real Personas page; engagement read adapters (Mastodon mentions, Discord channels) wired to
      /v1/engagement (idempotent sync, deterministic classification, safety/harassment →
      critical ActionItems, skip-requires-reason ledger) + real Inbox page; per-fan memory store
      (fan_memory.py). Phase 11 is GREEN.
- [~] **Phase 12 — feature wave** — Radar signals (topic gap/overlap/saturation/expiring, evidence
      attached, never auto-posts), import toolkit (CSV dry-run → dedup → import → reconcile →
      rollback, all proven), SIEM-shaped `content-factory audit export` JSONL. Completed
      2026-09-01: brand hierarchy persisted (/v1/brand-nodes; ancestor locks enforced on write,
      effective merge served, real Brand page with lock badges), request portal (HMAC portal
      tokens hashed at rest, revocable links, public /portal/briefs creates brief + ActionItem
      only, Requests page with one-time link minting), app i18n scaffolding (typed catalogs
      en/sv, useT with English fallback, per-account locale pref), dormant OIDC module with
      fixtures (auth/oidc.py). Phase 12 is GREEN.
- [~] **Phase 13 — hardening** — `scripts/backup.sh` (pg_dump -Fc + artifact rsync + SHA256SUMS)
      and `scripts/restore-rehearsal.sh` both EXECUTED against the live dev DB (restored scratch DB
      verified: workspaces=3, runs=25, audit=7). Retention sweeps (services/retention.py:
      retention_days=0 keeps forever, legal-hold prefixes, dry-run), requirements-traceability doc
      (docs/requirements-traceability.md), perf pass 2026-09-01 (content-memory bulk writes fixed
      an O(n²) import — 2000 pieces now ~0.2s; composite DB indexes; scale tests keep /v1/runs and
      /v1/action-items <1s at 400 runs/1200 nodes/300 items). Remaining: external security review
      (needs a human reviewer outside this codebase).
- [~] Phase 10 — Tier 2/3 adapters and analytics (analytics, originality, article/newsletter
      drafts GREEN; remaining platform adapters are package-only pending operator dev apps)
- [x] Phase 11 — personas, human tasks, engagement, style exploration
- [x] Phase 12 — feature wave
- [~] Phase 13 — hardening (external security review outstanding)

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
- **GPU hosts.** Two mitigations for the 2026-09-04 PCIe fall-off on vegaserv (`Xid 79`/`154`)
  are written down and **still unapplied** — `pcie_aspm=off` and a power cap under the stock
  350 W; both need root. `content-factory doctor` continuously verifies `gpu_pcie_health`,
  `gpu_aspm` and `gpu_power_cap`. nova needs its sleep paths removed so a GPU worker cannot
  suspend mid-run. Commands in `docs/gpu-hosts.md`; the full incident narrative is in
  `docs/journal/2026-09.md`.

## Session journal

Phase gate evidence and the session-by-session narrative live in `docs/journal/`, oldest first:

- `docs/journal/2026-08.md` — phases 0–2 gate evidence.
- `docs/journal/2026-09.md` — phases 3–13 gate evidence, every session since, and the
  host incident log.

This file carries the verdict and the next task. The journal carries the proof and the story.
Append session write-ups there, not here.
