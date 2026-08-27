# Licensing

Retrieved/verified 2026-08-27 unless noted. Full third-party notices: `THIRD_PARTY_NOTICES.md`.

## This repository
Apache-2.0 (see `LICENSE`). Private single-operator deployment; no commercial distribution.

## Renderer: Remotion — license trigger (ADR 0003)
Remotion 4.0.518 is **not** open source; its license allows free use for "an individual", "a
for-profit organization with up to 3 employees", non-profits, and evaluation. This project is used
by one operator and qualifies today.

**Trigger:** if the operating organization grows beyond 3 employees, or Content Factory is offered
as a paid product/service, a Remotion Company License is required (Creators $25/seat/month or
Automators $0.01/render with $100/month minimum, per remotion.dev/license FAQ on 2026-08-27) — or
switch the `RenderBackend` to Revideo (`@revideo/core` 0.11.0, MIT). The contract is the same.

## Separate-process copyleft components (never linked, never vendored)
| Component | License | How it is used |
|---|---|---|
| ComfyUI 0.34.0 | GPL-3.0 | sidecar process via HTTP/WS; managed by comfy-cli |
| comfy-cli 1.17/1.18 | GPL-3.0-only | CLI process for install/nodes/models/snapshots |
| comfy-mcp 0.10.0 | AGPL-3.0-or-later OR commercial | optional Workflow Lab helper process only |
| SearXNG | AGPL-3.0 | optional compose service, HTTP JSON API |
| Listmonk 6.x | AGPL-3.0 | operator's own instance, HTTP API |
| espeak-ng (via Kokoro/misaki) | GPL-3.0+ | inside the isolated TTS skill environment |
| psycopg 3 | LGPL-3.0 | dynamic linking permitted; unmodified |
| elkjs 0.12.0 | EPL-2.0 OR GPL-3.0-or-later | used under EPL-2.0 in the web app |

## Permissive core dependencies (selection)
Temporal server/CLI/UI/SDK (MIT), FastAPI/Starlette/Pydantic/SQLAlchemy (MIT), Authlib (BSD-3),
argon2-cffi (MIT), py_webauthn (BSD-3), pyotp (MIT), pywebpush (MPL-2.0), hypothesis (MPL-2.0),
trafilatura ≥1.8 (Apache-2.0), React Flow `@xyflow/react` 12.11.5 (MIT), Ajv (MIT),
`@noble/hashes` (MIT), Inter font via `@fontsource/inter` (SIL OFL 1.1), SeaweedFS 4.44
(Apache-2.0), PostgreSQL (PostgreSQL License).

## Models (licenses verified from official sources)
| Model | License | Use |
|---|---|---|
| HiDream-O1-Image | MIT | reference-image editing (anchor workflows) |
| Qwen-Image-Edit-2509 | Apache-2.0 | pose/layout-conditioned edits |
| Kokoro-82M | Apache-2.0 | local TTS with token timestamps |
| Chatterbox | MIT | evaluated alternative TTS (no timestamps) |
| WhisperX + WAV2VEC2_ASR_BASE_960H | BSD-2 / MIT | forced alignment |
| faster-whisper | MIT | ASR fallback, human-take validation |
| RIFE / FILM interpolation weights | MIT / Apache-2.0 | in-betweens |
| FLUX.1 Kontext dev | non-commercial | **not used** |
| Piper (piper1-gpl) | GPL-3.0-or-later | **not used** |

## Platform/API terms that bind behaviour (see docs/research/tier2-tier3-platform-constraints.md)
X requires the "Automated" label and bot disclosure; TikTok requires per-post creator consent UX;
YouTube API uploads are private until audit; Meta dev-mode is limited to role users. These are
encoded as destination capabilities, not documentation only.
