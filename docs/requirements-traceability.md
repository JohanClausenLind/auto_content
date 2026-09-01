# Requirements traceability (lightweight)

Capability → release state → where it lives → how it is proven. States: **live** (works now),
**mock-gated** (works against adversarial mocks; live use needs operator credentials),
**dormant** (implemented, disabled by design), **package-only**, **pending**.

| Capability | State | Code | Proven by |
|---|---|---|---|
| Typed contracts (Pydantic→TS/Ajv, drift check) | live | `python/content_factory/schemas`, `packages/content-schema-ts` | `just schemas-check`; TS roundtrip suite |
| Local auth (password/TOTP/passkeys/step-up, RBAC) | live | `auth/`, `api/routes/session.py` | `tests/api/test_auth_and_isolation.py` |
| Workspace isolation | live | `api/deps.py`, `db/` | isolation tests (foreign id → 404) |
| Deterministic static/video rendering + QC | live | `packages/content-ui`, `video-ui`, `apps/renderer`, `qc/` | renderer determinism suite; `content-factory demo` |
| Narration (mock TTS), alignment, captions, mastering | live (mock voice) | `audio/` | audio suites; narrated demo e2e |
| Local TTS (Kokoro) / premium (ElevenLabs) | mock-gated | `audio/tts.py`, `skills/audio/kokoro` | respx tests; model download is operator-opt-in |
| Research: SSRF-safe fetch, claims, citations | live (fixture search) | `research/` | security + claims suites; demo claim gate |
| Durable pipeline (preflight binding, resume, targeted rebuild) | live | `workflows/` | `tests/integration/test_production_workflow.py` |
| Revision Box (map→apply→rebuild→undo) | live | `editor/` | `test_revision_apply.py`; `/v1/revisions` |
| Region Editor masks | live (rasterization) | `editor/masks.py` | `test_masks.py`; generative fills are mock-gated |
| Image sequences (anchor/lock/hub-and-spoke/drift/flipbook) | live (mock backend) | `sequences/` | `test_image_sequences.py`; real edits need a ComfyUI workflow package |
| Delivery-promise + accessibility QC in pipeline | live | `qc/delivery.py`, `qc/accessibility.py`, `workflows/stages.py` | QC suites; wired into `qc_deliverable` |
| Originality engine + pipeline gate | live | `originality/`, `workflows/stages.py` | `test_originality.py`; cross-campaign block integration test |
| Skills/models: signed registry, routing, zero-egress local_only, evaluations | live | `skills/`, `models/` | routing + gateway suites |
| Budgets (reserve/settle/caps) | live | `budgets/ledger.py` | property + concurrency tests |
| ComfyUI adapter + workflow packages | mock-gated | `comfyui/` | fixture-server suite; real sidecar is operator-opt-in |
| Token vault + OAuth broker | live | `security/vault.py`, `connections/oauth.py` | vault + attack tests |
| Tier-1 publishing (Bluesky/Mastodon/Discord) + exactly-once intents | mock-gated | `distribution/` | chaos-retry suite; `tests/live` needs a designated test account |
| Article (WordPress/Ghost) & newsletter (Listmonk/Buttondown/Mailchimp) | pending (package-only) | — | constraints in `docs/research/cms-email-analytics-and-python-libs.md` |
| Tier-2/3 platforms (YouTube/Meta/TikTok/X/…) | package-only | — | constraints in `docs/research/tier2-tier3-platform-constraints.md` |
| Program schedules (no-burst catch-up) | live | `programs/scheduler.py` | schedule integration test |
| Kill switch + distribution profiles | live | `api/routes/distribution.py`, CLI | publisher tests; step-up gated |
| Analytics/attribution (UTM, webhooks, retention mapping) | live (ingest mock-gated) | `analytics/attribution.py` | attribution suite; GA4/Shopify need operator credentials |
| Personas: contracts, revise, consistency, firewall, schedule | live | `personas/`, `schemas/personas.py` | persona suites (fail-closed fixtures) |
| Human task slots + completeness gate | live | `workflows/production.py`, `human_tasks/` | `test_human_task_slot.py` |
| Engagement: classification, governance, read adapters, fan memory | mock-gated | `engagement/`, `personas/replies.py` | engagement suites; live reads need bot/user tokens |
| Style explore/exploit | live | `style/exploration.py` | exploration suite |
| Radar signals | live (fixture feeds) | `radar/signals.py` | radar suite |
| Imports (dry-run/dedup/rollback/reconcile) | live | `imports/history.py` | imports suite |
| Web app (shell, themes, create flow, canvas, revision box, push, operations) | live | `apps/web`, `packages/*-ui`, `pipeline-canvas` | 143+ Node tests; `pnpm -r test` |
| MCP server (only agent surface) | live | `mcp_server.py` | in-process MCP tests |
| Web Push | live (needs VAPID setup) | `notifications/push.py` | push suite |
| Backups + restore rehearsal | live (executed) | `scripts/backup.sh`, `scripts/restore-rehearsal.sh` | executed against the dev DB |
| Retention + legal hold | live | `services/retention.py` | retention suite |
| Audit JSONL export | live | CLI `audit export` | executed against the dev DB |
| Brand hierarchy | live (logic) | `brands/hierarchy.py` | hierarchy suite; persistence in phase 12 UI |
| Request portal | live (logic) | `portal/requests.py` | portal suite; route arrives with the portal page |
| Enterprise identity (OIDC) | dormant | `auth/oidc.py` | fixture-IdP suite; SAML/SCIM are schema-only |
| Tailscale serve/funnel guards | live (config) / operator-blocked (serve check) | `config/settings.py` | config invariants suite; needs `sudo tailscale set --operator` |
| Localization, i18n, OTIO export, multimodal critic | pending | — | scheduled after live-publishing phases |
