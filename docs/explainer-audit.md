# Explainer pipeline, Phase 0 audit (2026-09-15)

What the checkout can do today, proven by the command beside each row, and what the editorially
directed explainer pipeline needs from it. Versions and licence terms with dated links are in
`docs/research/2026-09-15-explainer-stack.md`. Nothing was deleted in this phase.

## Environment (vegaserv)

| Fact | Command | Result |
|---|---|---|
| OS, CPU, RAM | `lsb_release -ds; lscpu; free -h` | Ubuntu 24.04.5, i9-12900K (24 threads), 31 GiB RAM |
| GPU | `nvidia-smi --query-gpu=name,memory.total,driver_version,compute_cap --format=csv` | RTX 3090 24576 MiB, driver 595.84, cc 8.6 |
| Other GPU tenants | `nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv` | 950 MiB `~/.openclaw/.../chatterbox_server.py`, 256 MiB Discord |
| Tooling | `uv --version; node --version; pnpm --version; just --version; ffmpeg -version` | uv 0.12.10, node 24.21.0, pnpm 11.24.0, just 1.58.0, ffmpeg 6.1.1 |
| Local LLMs | `ollama show qwen38-ridge` | qwen35 arch, 27.3B, IQ2_M, ctx 262144, no vision |
| Serving stacks | `uv pip list`, `ls .venvs external` | no torch in the main venv, no vLLM anywhere, ollama on :11434 |
| Doctor | `set -a; . ./.env; set +a; just doctor` | All checks passed (warnings: ASPM, power cap, ComfyUI and searxng down) |

## Baseline gates, run 2026-09-15 22:55–23:05

| Command | Result |
|---|---|
| `just test` | 1607 passed, 2 skipped, 74 deselected in 360 s; TS 463 passed across 9 packages; `apps/web` build clean |
| `just render-smoke` | pass (h264 1920x1080 30/1 yuv420p, 90 frames, faststart) |
| `just lint` | **fails**: `scripts/style_check.py` flags three pre-existing journal entries over 60 lines |
| `just typecheck` | **fails**: 4 pyright errors in `tests/unit/test_subject_locate.py` (another session's in-flight work) |
| `just schemas-check` | **red** only because the regenerated contracts are uncommitted (the check reads `git status`) |

## KEEP / REMOVE / REWRITE

Observed = what the code does now, with the command that shows it. Proposed = what the explainer
pipeline needs. NEW rows name capabilities with no counterpart today.

### Contracts and evidence

| Component | Files | Observed (command → excerpt) | Proposed |
|---|---|---|---|
| Contract base | `schemas/base.py` | `grep -rn "ConfigDict" schemas/*.py` → only `base.py:36 extra="forbid", frozen=True`; every model derives from it; `OpaqueId` regex; 70 exports in `SCHEMA_REGISTRY` | **KEEP** |
| Export pipeline | `scripts/export_schemas.py`, `content-schema-ts/scripts/generate.mjs` | `just schemas` → 70 JSON Schemas, `generated/types.d.ts`, Ajv validators, `fixtures/schema/{valid,invalid}.json`; `roundtrip.test.ts` 37 passed | **KEEP** |
| SourceRecord / EvidenceRecord / ClaimRecord | `schemas/research.py`, `research/claims.py` | URL, publisher, title, author, dates, `capture_sha256`, locators (`char_range`, `pdf_page`, `table_cell`), `ClaimKind` ×7, `independent_publishers` dedupes syndication; `grep -n "captured.body" research/*.py` → body never persisted for web sources; `conflicts` never written | **REWRITE**: add epistemic class (observation / interpretation / forecast / illustrative_assumption), typed corroboration, uncertainty, capture manifest link |
| ResearchPack, EvidenceRequirementPlan | `schemas/research.py:170-178` | `grep -rn "ResearchPack(" python tests` → no producer | **REMOVE** exports; replaced by `EvidencePack` |
| Numeric checks | `models/scriptwriter.py:validate_beat`, `research/claims.py:_match` | every number in a beat must match a cell or claim within `NUMBER_TOLERANCE=0.02`, units string-equal, no conversion; `grep -rn "import pint"` → none | **REWRITE** into a unit-aware check at the compile boundary; **REMOVE** the unused `pint` pin unless Phase 1 wires it |
| Two transform vocabularies | `data/transforms.py`, `datasets/compile.py` | `transforms.py` 4 ops, used only by its test; `compile.py` 7 ops in the pipeline | **REMOVE** `data/transforms.py` after Phase 1 |
| StoryPlan / VisualBeat / CompiledTimeline | `schemas/scenes.py`, `timeline/compiler.py` | ms → frames round-half-up, contiguity validated, `plan_hash`; `grep -rn word_cues packages apps` → 0: cues computed, never consumed | **KEEP** as the timeline truth; consume or drop `word_cues` |
| Scene union | `schemas/scenes.py` (21 kinds) | `PLACEHOLDER_KINDS` = ranking, data_table, relationship_diagram, manim_asset render as grey cards | **REWRITE** into VisualSpec (chart, diagram, text, source_document + actions); **REMOVE** `manim_asset` |
| Dependency graph | `schemas/dag.py`, `workflows/production.py:862-886` | node hash = params + upstream output hashes: stage-granular; STATUS: "the dirty-node walk … does not [exist]" | **KEEP** as the coarse layer; **NEW** source→claim→scene→review graph (Gate B2) |
| Provenance vs content | `schemas/base.py:content_hash` | hashes every field including `accessed_at`/`checked_at`; two freshness defaults (365 / 30 days) | **REWRITE**: content hash excludes provenance; one freshness policy |
| ChannelProfile, ScriptPlan lock, NarrationManifest, ReviewReport | — | `grep -rn ChannelProfile python` → none; `script/locked.json` is an untyped dict; narration is per-beat `*.segment.json`; review = FrameRecord/SetReview/QC dataclasses, three partial shapes | **NEW** contracts (Phase 1) |

### Render core

| Component | Files | Observed | Proposed |
|---|---|---|---|
| Render scripts | `apps/renderer/scripts/*.mjs` | Ajv-validated bundle, `renderMedia(h264, yuv420p, bt709)`, atomic output, bundle cache; `grep -rn licenseKey apps packages` → 0 | **KEEP**; add `licenseKey: "free-license"` |
| Determinism | `apps/renderer/test/determinism.test.ts` | sequential vs sequential byte-equal; no seek-vs-sequential case | **KEEP**; **NEW** Gate C1 test |
| Fonts | `apps/renderer/src/fonts.ts`, `content-ui/src/fonts/index.ts` | Inter and Sora loaded from local files via `@remotion/fonts`; `FONT_STACK` starts with unpinned `"HelveticaNeue Condensed"` | **REWRITE**: pin the stack to the bundled faces |
| Scene templates | `packages/video-ui/src/scenes/*.tsx` (17) | pure `useCurrentFrame` cards; ad hoc reveal/count-up/zoom; no action vocabulary; `grep -rnE 'Math.random|Date.now' packages/video-ui/src` → 0 | **REWRITE** into typed templates + actions; salvage `SceneFrame`, `DataNotice`, chart math |
| Tokens | `content-ui/src/tokens/theme.ts` | `grep -rni oklch packages/content-ui/src` → 0; hex sRGB; Okabe-Ito ×6 present; no sequential, diverging or state family; WCAG ratio, no APCA | **REWRITE** to OKLCH three-family tokens + APCA (Phase 2) |
| Text fitting | `content-ui/src/text/fit.ts` | steps down to a floor then truncates; `fit.truncated` never read by a scene | **REWRITE**: fail or split, never shrink silently (Gate C2) |
| Static legibility QC | `content-ui/src/qc/legibility.ts` | artboards only; `grep -rn legibilityReport packages apps python` → one test caller | **REWRITE** to APCA and wire into the pipeline |
| Post-render QC | `qc/media.py` | codec, size, fps, frames, faststart, 5 sampled black/frozen frames; `grep -rniE 'apca|\bocr\b|paddle' python` → none | **KEEP** as container gate; **NEW** pixel QC (Gate C3) |
| Libraries | package.json files | d3 7.9.0 used for computation only; elkjs 0.12.0 only in `pipeline-canvas`, main thread; KaTeX absent; `vega`, `vega-lite`, `vega-embed`, `maplibre-gl` declared with 0 imports | **KEEP** d3; **NEW** elkjs worker + katex in video-ui; **REMOVE** vega*, maplibre-gl |
| Manim stage | `workflows/stages.py stage_render_animation`, `models/weights.py` | opt-in `_EXTRA_STAGES` entry, not in any branch | **REMOVE** (brief: no Manim) |
| editor-core | `packages/editor-core/src/operations.ts` | 6 reversible ops with `DependencyImpact` | **KEEP**; **NEW** ops for actions and takes |

### Narration and audio

| Component | Files | Observed | Proposed |
|---|---|---|---|
| Qwen3-TTS | `skills/audio/qwen3tts`, `audio/tts.py` | Base, CustomVoice, VoiceDesign installed (4.3 GB each); default `tts="mock"`, then CustomVoice `ryan`; Base only with `qwen_ref_audio`; journal has no GPU VRAM or RTF for it | **KEEP** executors; **REWRITE** defaults; **NEW** benchmark harness with a licence gate (Phase 4) |
| Creator voice | `config/settings.py:642`, `output/vo/voices/*` | six CustomVoice renders, no human reference, `grep -rni consent python` → 0 | **NEW** recording brief, reference asset, consent record |
| ASR word timings | `audio/takes.py:142-190` | faster-whisper base.en int8 CPU word stamps, labelled `TimingSource.forced_alignment`; `grep -rni "forced_align\|montreal\|ctc" python skills` → strings only | **REWRITE** label to `asr`; **NEW** CTC/MFA aligner |
| Take verify + diff | `human_tasks/validation.py:validate_take` | `SequenceMatcher` over spoken word shape, accept ≥ 0.98 | **KEEP** (alignment stage 1) |
| Spoken / display / lexicon | `schemas/audio.py:14-38` | separated; TTS cache key hashes the whole request including `display_text` | **REWRITE** cache key (Gate E2) |
| Master chain | `audio/mix.py:300-420` | -14 LUFS, -1.3 dBTP master, `ebur128` measured, `LoudnessReport` | **KEEP** |
| Stems and ducking | `workflows/stages.py:4496` | cumulative files, no separate music or SFX stem; `sidechaincompress` in the stage, a measured-better `duck_curve` in `scripts/narrate_clip.py` | **REWRITE**: separate stems, one ducking implementation |
| Captions, cue sheet, continuity | `audio/captions.py`, `audio/cues.py`, `audio/continuity.py` | 1–6 s cues, 2 lines × 32 chars; SFX sheet pinned to library sha; quietest-window cuts | **KEEP** |
| Music / SFX libraries | `assets/music`, `assets/sfx` | 22 tracks, 670 sounds, per-file licence in manifests; MMAudio weights CC-BY-NC | **KEEP**; gate MMAudio on licence |
| `scripts/audio_variants.py` | — | nine hard-coded listening recipes at -16 LUFS | **REMOVE** to `sandbox/` |

### Orchestration, review, delivery

| Component | Files | Observed | Proposed |
|---|---|---|---|
| Temporal `ProductionWorkflow` | `workflows/production.py`, `services/runs.py` | serves the API/MCP campaign path only; signals, heartbeats, retries; `fixtures/temporal/*.history.json` | **KEEP** for the campaign path; the explainer lane does not use it (no demonstrated benefit to porting or removing) |
| Local runner | `runners/local.py`, `runners/pins.py` | 16 lanes, pins, `--from/--until`, stop registry, SIGTERM → `RunStopped`, atomic report; no per-node fingerprint, a stopped step records no outputs | **REWRITE**: fingerprint-keyed step ledger so a rerun skips valid work (Gate F4) |
| GPU tenancy | `services/local.py`, `services/gpu_priority.py` | `exclusive_gpu` stops the other tenant, `_wait_for_vram` to 18 GB free, `unload_ollama` | **KEEP** |
| Artifact store | `artifacts/store.py` | content-addressed, immutable, used by render stages only | **KEEP**; route every accepted output through it |
| Vision reviewer | `qc/vlm_review.py`, `models/catalog.py` | Ollama `Qwen3.6-27B-Heretic` Q4_K_M GGUF (17.8 GB), images only, `MAX_FRAMES=12`, 768 px, advisory, refuses while a run holds the card; catalog `commercial_use=False` | **REWRITE**: Qwen3.8-27B W4A16 on vLLM, image and video proven, fixture set before authority (Phase 5) |
| Review schema | `schemas/review.py` | strict FrameRecord/SetReview bound to `png_sha256`; no rubric version, interval, typed repair | **REWRITE** into `ReviewReport` |
| Frame review gate | `workflows/stages.py:2814-2946`, `cli/main.py frames` | human verdicts bound to file sha; `--accept-all` refused for `--as agent`; VLM never records a verdict | **KEEP** |
| Bounded repair | `config/settings.py:497` | `max_regen_attempts_per_frame=3` for deterministic checks; human rounds unbounded | **NEW** `max_review_rounds=2` per unit |
| Typed patches | `schemas/editing.py`, `editor/apply.py` | 6 ops by stable ID for copy and timeline | **KEEP**; **NEW** ops for takes, passages, holds, cue offsets |
| Export | `stages.py:stage_compile_destination_packages`, `schemas/delivery.py` | `final.mp4`, `captions.{srt,vtt,ass}`, `DeliveryPackage` with roles; thumbnail and metadata roles have no producer; no stems, chapters, source notes or review report | **REWRITE** into an export bundle (Gate F6) |
| Live gate | `distribution/publisher.py` | `kill_switch=True` by default; `-m live` deselected | **KEEP** |
| MCP | `mcp_server.py` | 11 tools on the campaign path | **KEEP**; add review-queue tools later |

## Decisions the audit forces

- Remotion stays pinned at 4.0.x with `licenseKey: "free-license"`; the 5.0 Automators clause and
  the contractor rule are recorded in `docs/licensing.md`.
- The reviewer is Qwen3.8-27B at W4A16 served by vLLM (AWQ/GPTQ kernels on cc 8.6); FP8, NVFP4 and
  MXFP4 are not planned. Until it clears the fixture set, the human frame gate stays the gate.
- The explainer lane runs on the local runner; Temporal keeps the campaign path. Neither is
  replaced in this build.
- No Manim, Blender, MapLibre, deck.gl or Motion Canvas. What would trigger each:
  `docs/scale-later.md`.
