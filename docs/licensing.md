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
| Qwen3-TTS-12Hz-1.7B (Base + CustomVoice) | Apache-2.0 | local narration voice; no timings of its own (force-aligned) |
| Kokoro-82M | Apache-2.0 | fallback local TTS, token timestamps from the model |
| Chatterbox | MIT | evaluated alternative TTS (no timestamps) |
| WhisperX + WAV2VEC2_ASR_BASE_960H | BSD-2 / MIT | forced alignment |
| faster-whisper | MIT | ASR fallback, human-take validation |
| RIFE / FILM interpolation weights | MIT / Apache-2.0 | in-betweens |
| Resemble Enhance (code + weights) | MIT | speech restoration (`restore_speech`); no usage restriction |
| ClearerVoice MossFormer2_SE_48K / _SR_48K (code + weights) | Apache-2.0 | speech cleanup and 48 kHz band extension |
| FLUX.1 Kontext dev | non-commercial | **not used** |
| Piper (piper1-gpl) | GPL-3.0-or-later | **not used** |

## Media assets under `assets/` (terms read from the supplied agreement, not from a summary)

| Asset set | Origin | License |
|---|---|---|
| `assets/sfx` — 38 recorded files | Sonniss #GameAudioGDC Bundle 2026 (Part 9); 11 suppliers, per-file provenance in `assets/sfx/manifest.json` | Sonniss #GameAudioGDC Bundle licensing agreement (royalty-free) |
| `assets/sfx` — 11 generated files | Stable Audio 3 Small-SFX, run locally | Stability AI Community License (weights); Gemma Terms of Use (text encoder) |
| `assets/music` — 22 generated tracks | MiniMax-Music3 (GGUF), run locally through the HOT-Step engine | MiniMax-Music3 Community License (weights); HOT-Step engine MIT |

The bundle grant is broad: worldwide, non-exclusive, royalty-free, unlimited personal **and
commercial** projects for their lifetime, modification permitted, public performance and
reproduction permitted, **no attribution required**, and explicitly "synchronization with audio
and visual projects ... games, films, television & interactive projects" — which is exactly what
this repo does with them. Suppliers are recorded in the manifest anyway, for traceability rather
than obligation.

Three clauses in it bind behaviour and are not optional:

- **No AI training or usage.** The agreement "expressly prohibited[s] ... using any sound effects
  licensed under this Agreement for the purpose of training artificial intelligence technologies
  ... including ... technologies capable of generating sound effects or works in a similar style",
  and forbids leveraging them to "develop, train, or enhance" AI without written permission. So
  these files are mix material only. They must never become training, fine-tuning, LoRA,
  embedding or retrieval-corpus input — and specifically never a conditioning or reference input
  to the repo's own audio models (Stable Audio 3, MMAudio, ACE-Step, FoleyCrafter). This is the
  reason `assets/sfx` is excluded from the `condition_sound` path that generated audio goes
  through; see the 2026-09-07 entries in STATUS.md.
- **No selling as they come** (§Restrictions b): they may be sold "as incorporated into the
  licensee project", not on their own. A rendered video containing them is fine; redistributing
  the FLACs as a sound library is not. Treat a public copy of `assets/sfx` as redistribution and
  keep the recorded half out of any published artefact.
- **No claiming authorship of the original recording** (§Restrictions a). Levelling, resampling
  and loop-wrapping are the modification right being exercised; the recordings stay the
  suppliers'.

The source bundle itself (8.0 GB of 96/192 kHz WAV) is never committed and never fetched by a
build. It lives outside the repo at `CF_SONNISS_GDC_DIR`; only the 44.1 kHz excerpts are kept.

### `assets/music` — MiniMax-Music3 Community License

Three terms bind behaviour, and one of them is unlike anything else in this file:

- **Clear disclosure of AI generation is required for publicly distributed outputs.** This is a
  licence obligation, not a courtesy, and it attaches to anything published with these tracks in
  it. The repo already has disclosure machinery (safety guardrails are code, not config); music
  from `assets/music` in a published deliverable has to carry it.
- **Prominent display of "MiniMax-Music3"** in commercial products.
- **Separate authorization above US$20M annual revenue** — not a concern now, recorded so it is not
  discovered later.

The tracks are generated from **text prompts only** — no `init_audio`, no reference audio, no
conditioning on any recording. That is deliberate and it is what keeps this library clear of every
no-AI clause below.

### Recorded libraries held outside the repo

`/mnt/fast/sound-libraries/99Sounds` — eleven 99Sounds packs (~7 GB WAV), staged by another session
on 2026-09-07 and verified against the source zips by sha256. **This is now the only copy**; the
zips were deleted. Not committed, not fetched by any build, and **not currently used by any recipe
or any file in `assets/`**.

**There is no single 99Sounds licence.** Terms are per pack, read from each pack's own PDF with
`pdftotext` on 2026-09-07, and they fall into three tiers that are not interchangeable:

| Tier | Packs | What the document actually says |
|---|---|---|
| **Commercial granted** | `#99S030 Antigen`, `#99S031 Garage Foley`, `#99S032 Underwater Sounds`, `#99S035 Underground Sounds`, `99 Sound Effects` | Each carries a `LICENSE.pdf` that says "commercial" explicitly; personal and commercial projects, "for yourself or a client", modification permitted, no attribution required |
| **Silent on commercial** | `99Sounds Rain And Thunder`, `[99Sounds] Hands Make Sounds` | A *different, narrower* PDF: "provided free of charge. You can use them for free in your music, as well as audio and video projects." The word "commercial" does not appear at all |
| **No terms on disk** | `99Sounds Water Sounds`, `99Sounds x Dronny Darko Cinematic Textures`, `99Sounds City Sounds`, `99Sounds Electromagnetic Fields` | The first two have no licence document whatsoever; City Sounds has an `INFO.txt` of credits only; Electromagnetic Fields has a `99Sounds.txt` pointing at <https://99sounds.org/license/>, which nobody here has fetched |

Consequences, in order of how likely they are to bite:

- **Silence is not a grant.** For Rain And Thunder and Hands Make Sounds the narrow PDF permits use
  in "audio and video projects" free of charge and never mentions commercial use. Do not read that
  as permission for a published commercial deliverable. Both overlap heavily with material already
  in `assets/sfx`, so the cheap resolution is to not use them rather than to interpret the gap.
- **Undocumented is unusable until documented.** `99Sounds Water Sounds` is the tempting one — a
  real stream recording would beat the generated `stream_loop` — and it is the one with no terms on
  disk at all. Before anything from that tier is cut into `assets/`, someone has to read
  <https://99sounds.org/license/>, record the date and what it said, and add the pack to the table
  above. Provenance you cannot trace is a library you cannot re-cut, and that applies to the licence
  as much as to the audio.
- **Redistribution is barred across every tier that has a document**, in one wording or another:
  the broad licence forbids sell/sublicense/stream/"redistribute (even for free)"; the narrow one
  says the sounds "may not be re-distributed". Same conclusion as the Sonniss half — excerpts inside
  a finished film are inside the grant, a public copy of the audio is not.
- **Five of the eleven forbid use as source material for another library**, in their own words:
  Antigen, Garage Foley and Underwater Sounds bar using them "as the sound source in audio related
  software and/or sound libraries"; Rain And Thunder and Hands Make Sounds bar them "as the source
  material for another sound library or virtual instrument". That is a licence requirement, not a
  house preference, and it is what the rule below is enforcing.
- Provenance for `#99S035 Underground Sounds` (geophone/hydrophone, Iceland) is documented in the
  pack's own `Metadata/Geofon & Hydrophone Metadata.pdf` rather than inferred. Several of its
  filenames carry Icelandic characters and one (`Fja╨rárgljúfur`) is mojibake in the zip's own
  central directory — any recipe referencing these must take the filename from the filesystem
  rather than a retyped literal.

**The rule across all of them, stated once:** a licensed recording may be *mixed*; it may never be
*conditioning*. No recording under any of these agreements goes to Stable Audio 3, MiniMax-Music3,
MMAudio, ACE-Step or FoleyCrafter as init audio, reference audio, a fine-tune corpus, an embedding
or a retrieval corpus. Generated assets in this repo are text-prompt-only, which is what makes that
line easy to hold.

## Platform/API terms that bind behaviour (see docs/research/tier2-tier3-platform-constraints.md)
X requires the "Automated" label and bot disclosure; TikTok requires per-post creator consent UX;
YouTube API uploads are private until audit; Meta dev-mode is limited to role users. These are
encoded as destination capabilities, not documentation only.
