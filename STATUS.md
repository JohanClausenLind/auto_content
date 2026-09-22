# STATUS

Last updated: 2026-09-15. Hosts: vegaserv (Ubuntu 24.04, 31 GB RAM, RTX 3090 24 GB) and nova
(76 GB RAM, RTX 3090, `docs/gpu-hosts.md`). The proof behind every line here is in `docs/journal/`.

## Where things stand

Phases 0–8 and 11–12 are GREEN. Phase 9 is green through the idempotent publisher and
exactly-once intents, but its live gate has never been opened: **nothing this repo produces has
ever been published**. Phase 10 is green offline; the remaining Tier 2/3 adapters are
package-only. Phase 13 is green except for an external security review.

Sixteen workflow lanes run end to end on real backends (`just workflows`). Which model belongs on
which host, and why: `docs/gpu-hosts.md`, "Where to run which model". Ideogram 4 is the operator's
main still-image model and must be prompted with a JSON caption, never prose
(`docs/research/2026-09-12-ideogram-4.md`). It has two paths: the fp8 ComfyUI package on nova, and
the SDNQ 4-bit repack (17.3 GiB, group offloading) meant for vegaserv, which has not yet generated
a pixel (journal 2026-09-15).

## Constraints every session must respect

- **31 GB of system RAM binds vegaserv, not VRAM.** A graph too big for the card stages through
  RAM and OOMs at model load; the fp8 Ideogram 4 stages ~28 GB and runs on nova only. Point a
  lane at nova with `--endpoint http://100.82.150.94:8188`.
- Run renders in the foreground one at a time, never run the suites with a model resident, and
  stop a tenant before switching models inside it. The three ways this box goes down, and the fix
  for each: `docs/setup.md`, "The three ways this box goes down".
- A review gate (`review_assets`, `review_frames`) is answered by a person looking at the frames.
  `--force` and `--accept-all` are the one wrong answer.
- Style is one-line docstrings and two-line comments, enforced by `just lint`
  (`.claude/skills/style/SKILL.md`).

## Explainer pipeline (build started 2026-09-15)

An editorially directed explainer lane is being built on top of the lanes above, in phases with
named gates. Audit and decisions: `docs/explainer-audit.md`; the six context docs it consumes at
runtime (`docs/CHANNEL_PROFILE.md`, `EDITORIAL.md`, `DESIGN_SYSTEM.md`, `PLANNER_PROMPT.md`,
`EVIDENCE_POLICY.md`, `SPONSORS.md`) are drafts derived from the repo and need the creator's review.

- [x] 0 audit — GREEN (2026-09-15): table exists, every "works" row has its command, nothing deleted
- [x] 1 contracts and evidence — GREEN (2026-09-22): B1 arithmetic, B2 invalidation, B3 actionable errors
- [x] 2 compiler and render core — GREEN (2026-09-22): C1 seek determinism, C2 label bounds, C3 post-encode APCA and edge contrast, C4 60 s compile
- [x] 3 real source documents — GREEN (2026-09-22): D1 highlight tracking, D2 passage cases, D3 OCR rejection, D4 replay after change
- [x] 4 narration and alignment — GREEN (2026-09-23): E1, E2; VoiceDesign measured best; creator reference recording still missing
- [ ] 5 QC, reviewer, repair, delivery — gates F1–F6 (Qwen3.8-27B W4A16 reviewer, advisory first)
- [ ] 6 editorial workflow and demonstration episodes

## Next smallest task

Explainer Phase 5: deterministic QC, the Qwen3.8-27B reviewer on vLLM, repair loop, delivery.
Before that, the three operator gates below stand as they were:

1. **Live Bluesky gate.** Put a designated TEST account's `BLUESKY_HANDLE` /
   `BLUESKY_APP_PASSWORD` in `.env`, then
   `set -a; . ./.env; set +a; uv run pytest -m live tests/live -q`.
2. **Tier 2/3 and article destinations, live drafts.** Per-platform developer apps and keys, then
   each adapter against a real draft (`python/content_factory/distribution/`).
3. **External security review.** `docs/requirements-traceability.md` maps capability → code → proof.

Code-side: the SDNQ Ideogram 4 server **now runs on vegaserv** (2026-09-15) — 150.5 s for a
1024x576/20-step frame, 140.3 s for an inpaint, peak 14.8 GiB, where every previous attempt OOMed
at model load. Six bugs between the published recipe and a working server, and the measurement
that compositing cuts frozen-background drift from 13.5 % to 0.2 % of pixels, are in the journal.
Next on that thread is the **`moving-subject-video` lane**: the keyframe loop is proven, but the
LTX i2v half, the 5 fps assembly to one minute, and the voice and sound effects are untouched, and
`sequences/subject_locate.py` has only ever run against a stub gateway.

Then, Ideogram 4 as an *anchor* backend with FLUX.2 composing the spokes from it: the caption builder (`prompting/ideogram.py`), the governed package
(`media/ideogram_packages.py`), the refusal retry and the count lesson (one element per figure)
exist; the setting that routes the anchor to one backend and the spokes to another does not.

Baseline before any new work:

```
set -a; . ./.env; set +a; just doctor && just test && uv run pytest tests/api -q
```

## Open follow-ups

One line each; the detail is in the journal under the date given.

- Film response (`imaging/film.py`, `content-factory frames film`) is written and not wired; wire
  it after a GPU run measures the new presets (2026-09-12).
- romsketch LoRA cannot merge: trained on the full HiDream weights, the server runs `dev`
  (2026-09-12).
- `controls.compiler=blender` needs four unstated preconditions: a story at the shots' fps,
  approved character assets, `appearance` on every figure, and
  `CF__CONTROLS__BLENDER_BIN=/snap/bin/blender` (2026-09-12).
- LTX-2.5 tangles two bodies in contact; nothing carries the mocap through the interpolation
  (2026-09-12).
- 58 of 15,789 reference clips are retargeted, so a beat can match well and stage nothing
  (2026-09-12).
- Ideogram 4 miscounts subjects; a backend needs a count check, not only the refusal retry
  (2026-09-12).
- The lettered refusal has no automatic detector; only the flat grey card is caught (2026-09-13).
- Qwen3-TTS 25 Hz is announced (paper 2601.15621 Table 1) but not released: the linked collection
  holds six repos, all 12 Hz. Re-check `hf models list --author Qwen --search TTS` (2026-09-15).
- Two-gate lanes get no "N to review" badge; needs a per-deliverable→node mapping (2026-09-11).
- `MAX_FRAMES` is 12 for the vision reviewer; larger sets are reviewed as their first twelve
  (2026-09-11).
- A step stopped mid-execution records no outputs (2026-09-11).
- Partial execution: pinning exists; the dirty-node walk that derives the re-run set does not
  (2026-09-11).
- Eleven undeclared widget reads, pinned by a test and untriaged (2026-09-11).
- `fixtures/temporal/spike_production_run.history.json` is rewritten by every
  `just test-integration` with no semantic change; refresh it deliberately or stop tracking it
  (2026-09-11).
- No `typeVersion` per node, so renaming a widget breaks saved graphs silently (2026-09-11).
- Recording a frame verdict does not resume a local run; the command is printed instead
  (2026-09-10).
- The Assets page reads a different source from the run history (2026-09-10).
- `review_assets` takes a free-text `--as <name>` (2026-09-10).
- vegaserv's PCIe mitigations (`pcie_aspm=off`, a power cap) are written down and unapplied, and
  nova's sleep paths are not removed (`docs/gpu-hosts.md`).

## Phase checklist

- [x] 0 research and spikes — GREEN (2026-09-01)
- [x] 1 foundation — GREEN (2026-08-27)
- [x] 2 typed contracts and deterministic rendering — GREEN (2026-08-27)
- [x] 3 narration and audio — GREEN (2026-09-01); one-take narration and VoiceDesign 2026-09-15
- [x] 4 research, evidence, claims — GREEN (2026-09-01)
- [x] 5 skills, models, routing, ComfyUI — GREEN (2026-09-01)
- [x] 6 durable pipeline — GREEN (2026-09-01); editable node-graph Workspace 2026-09-03
- [x] 7 editor, QC, Revision Box, image sequences — GREEN (2026-09-01)
- [x] 8 product UX, PWA, assistant, Tailscale — GREEN (2026-09-01); per-node run review and the
      vision reviewer 2026-09-11
- [~] 9 scheduler and first live publishing — everything but the live post is GREEN
- [~] 10 Tier 2/3 adapters and analytics — analytics, originality and article/newsletter drafts
      GREEN offline; other adapters package-only
- [x] 11 personas, human tasks, engagement, style — GREEN (2026-09-01)
- [x] 12 feature wave — GREEN (2026-09-01)
- [~] 13 hardening — backups and restore rehearsal executed; external security review outstanding

What each phase contains and its gate evidence: `docs/journal/2026-08.md` (phases 0–2) and
`docs/journal/2026-09.md` (3–13, every session since, and the host incident log).

## Decisions

Twelve ADRs in `docs/adr/`. Dependency pins were verified from registries on 2026-08-27
(`docs/research/*.md`). Optional S3 is SeaweedFS (MinIO archived upstream). Compose ports avoid
8080/8082/8091, which other stacks on this host use.

This file carries the verdict and the next task. The journal carries the proof and the story.
Append session write-ups there, not here.
