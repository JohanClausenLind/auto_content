# ADR 0012 — A deterministic 3D scene-control layer in front of the generative models

Date: 2026-09-06 · Status: accepted

## Context

Generative video drifted on the two things a film cannot forgive: characters changed proportions
between shots, and camera moves were whatever the text prompt happened to be read as. The models on
this host can be *conditioned*, but only on specific things: HiDream-O1 accepts reference images
(1 = edit, 2+ = subject) and layout boxes; LTX-2.5 runs only through ComfyUI (GGUF weights) and
accepts a first frame plus keyframe guides at chosen frame indices (`LTXVAddGuide`); no depth /
pose / edge IC-LoRA exists for LTX-2.5; Wan-Animate-2 accepts a pose video. Text alone controls none
of this reliably.

## Decision

Put a headless Blender scene in front of the generators and let it supply *structure*, not looks:

- A `ShotPlan` (one `ShotSpec` per story beat: camera keyframes, rigged characters with poses,
  props, environment, lighting, `anchor_frames`) is the 3D twin of the 2D `MotionPlan`. Both compile
  into one contract, `ControlBundle` (`schemas/shots.py`).
- `compile_controls` gains an executor with `controls.compiler = motion_plan | blender`. The Blender
  path runs `skills/video/blender_scene` as a one-shot subprocess: Cycles CPU at one sample per pixel
  for exact depth / normals / object index, Workbench for a clay rough RGB, OpenPose-18 joints and
  layout boxes exported as JSON. Output is byte-identical across runs and cached per shot.
- Characters are MPFB2 (MakeHuman) rigs built once into `.blend` assets under the weight store with
  baked OpenPose vertex anchors, so every shot uses the same body and the skeleton export is
  rig-agnostic. MPFB is GPL and is never vendored: recipes in git, assets under `/mnt/fast`.
- `generate_anchor` conditions HiDream per shot per keyframe: identity references first (with
  their layout boxes), then the rough render and the skeleton as extra references. The engine's
  `ControlConditioning` replaces the old `del control_png`.
- `generate_video` animates each shot with LTX-2.5: anchor 0 as the first frame, later anchors as
  `LTXVAddGuide` keyframes at their Blender frame indices, sizes and lengths from the shot. Camera
  moves are therefore computed, not prompted. Clips are concatenated per deliverable.
- Depth, normals and Canny are rendered and stored from day one; they are the training data for a
  future LTX-2.5 V2V IC-LoRA (deferred), not something any current consumer reads.

## Consequences

- The sequence branch reorders to `plan_shots → compile_controls → generate_anchor → lock_generation`
  (`dag_compiler` 0.2.0): the anchor is downstream of the controls it is conditioned on.
- The pipeline depends on the system Blender (snap) and on a GPU context for Workbench in
  `--background`; the skill falls back to Cycles CPU for the rough RGB and records which engine ran.
- Determinism is a property of the whole chain and is tested as such: the skill's cube fixture is
  rendered twice and compared byte for byte in `tests/integration/test_blender_scene_render.py`.
- Wan-Animate-2 pose driving and the Cutie / ProPainter / SeedVR2 / interpolation post chain slot in
  behind the same bundles (segmentation seeds Cutie) and are later phases of the same plan.

## Amendment (2026-09-06): hybrid shot router

The scene-control chain and the Remotion renderer were two separate workflows: a deliverable was
either fully generated or fully typeset. The user asked for a third workflow that mixes them per
shot. Decision: keep both renderers unchanged and add routing plus assembly around them.

- `route_shots` (new stage, after `plan_shots`) writes `shots/routing.json` (`ShotRouting`): one
  `render | generate` decision per story beat from a scene-kind table (`routing.generate_kinds`,
  default: title, section intro, chapter transition, image, quote, callout, outro) plus per-beat
  overrides. No model is consulted; the routing hash is the stage output.
- `compile_controls` (Blender path) only renders shots whose beat is routed `generate`, so
  `generate_anchor` and `generate_video` follow automatically. Remotion still renders the whole
  timeline: its beat boundaries come from the narration, and cutting the finished render by frame
  index is cheaper and more exact than rendering scenes one at a time.
- `compose_video` becomes the merge: with a routing that sends beats to the generative chain it
  cuts each `render` beat out of the Remotion render by frame index, conforms each `generate`
  beat's `video/<shot>/clip.mp4` to the timeline's size and fps (letterboxed, held on its last
  frame when the LTX length snap made it shorter), encodes every segment identically (cached by
  source digest + filter), concatenates losslessly, and muxes narration when the audio branch ran.
  `exports/compose.json` records the segment list. Without such a routing the stage is unchanged.
- Web: `route_shots` node, `compose_video` accepts `clips` (VIDEO) and `routing` (SHOTS), template
  `hybrid-shot-router-video`.

Not in this amendment: per-scene Remotion renders for generate-routed beats (wasted work today,
but harmless), and running the post chain (fix/upscale/interpolate) on the mixed timeline — the
mixed path takes the LTX clips as they are; the post chain still targets `exports/final.mp4` of
the all-generative workflow.

## Amendment (2026-09-08): the router's default is one scene kind, and one rate is master

Two facts in the 2026-09-06 amendment above are superseded by what the rendered films showed.

**`routing.generate_kinds` is now `("image",)`,** not the seven kinds recorded above (title,
section intro, chapter transition, image, quote, callout, outro). The amendment's reasoning was
that a beat which "sets a scene" has no single correct picture. That is true and it is not the
operative distinction. Six of those seven kinds are *text* scenes: their entire content is words on
screen — a title, a label plus a heading, a pull quote with its attribution, a call to action. The
card renderer sets those in the pinned face at the pinned size and gets them right on every run;
an image model renders typography as ornament that resembles letters, which every wind short v1 to
v5 demonstrates. On a `quote` it is worse than cosmetic: the words are a claim attributed to a
named person, so a generative pass over them is a fabrication risk rather than an art-direction
choice. `image` is the one kind that carries no text at all — it names an asset and an alt text —
so it has nothing to typeset and nothing to garble, and it is the only default. Every other kind
stays a per-lane decision (`generate_kinds` in the lane's yaml, now set explicitly in
`hybrid-video.yaml`) or a per-beat one (`routing.overrides`), where it is written down.

**The story's frame rate is master.** The amendment says `compose_video` "conforms each `generate`
beat's clip to the timeline's size and fps". It still does, and that conform was silently load-
bearing: `ShotSettings.fps` defaulted to 24, `fixtures/story/wind_2024.json` is 30, nothing
compared the two, and `hybrid-video.yaml` pinned `compile_timeline` to 24 "to match plan_shots" —
so every wind short shipped 24 fps footage stepped up to 30 by ffmpeg duplicating one frame in
five, for the whole film. `plan_shots` now defaults its rate to the loaded `StoryPlan.fps` and
refuses a plan that disagrees with the story; `compile_timeline` reads its `fps` widget, recompiles
at that rate, and refuses a timeline that disagrees with the shot plan. The conform remains, for
the cases it is genuinely needed in (a re-used clip, a hand-authored plan run with `--force`
semantics), but `_segment_filter` now emits `fps=` only when the source is at another rate and
`compose.json` records `retimed_from_fps` per segment, so a duplicated-frame segment is legible
instead of invisible. `COMPOSE_MIXED_VERSION` is 0.3.0 because the filter string is part of every
segment's `input_hash`.

### Operations (same amendment): the pipeline runs its own GPU servers

Running the generative stages used to need an operator to start the HiDream server, run
`generate_anchor`, stop it, start ComfyUI with the LTX flags, run `generate_video`. That sequence
is now code (`services/local.py`): a stage that needs a GPU tenant calls `ensure(tenant)`, which
returns immediately when the server is healthy, otherwise stops the other tenant (the 24 GB card
never holds HiDream and the LTX GGUF stack at once), links every `RequiredModel` of the known
ComfyUI packages from the weight store into ComfyUI's model folders, starts the server and waits
for its health check. Handles live in `.services/`; `local_services.auto_start=false` turns it
back into a clear error with the manual command. `synthesize_narration` selects its voice by
`narration.tts` (mock | kokoro) and caches per beat. `content-factory run-local <workflow>` (and
`just run-local`) executes a template's stage order on one machine with no Temporal.

## Amendment (2026-09-09): the control-video path is blocked upstream, and guides are now measured

This ADR's rationale says "no depth / pose / edge IC-LoRA exists for LTX-2.5". Re-checked against
the upstream documentation vendored at `external/LTX-2` (1.3.0, 2026-08-25) on 2026-09-09, and it
still holds — with a sharper reason than the wording implied. The LTX-2.3 adapters are not
incompatible because this repo inferred it from a `base_model` field; Lightricks states it: *"a
LoRA only works with the model it was trained on"* (README, "Legacy: LTX-2.3") and *"The LoRAs at
the end of this page were trained on LTX-2.3, so pair them with an LTX-2.3 checkpoint"*
(`MODELS-LTX-2.3.md`), where `LTX-2.3-22b-IC-LoRA-Union-Control` is one of the LoRAs listed. The
only LTX-2.5 IC-LoRA published is `Pixel-Spatial-Upscaler`, a detailing adapter. See ADR-0006's
amendment of the same date for the citations in full.

So a control package taking a Blender depth / canny / pose video into LTX-2.5 cannot be built
against a weight that exists, and downloading the 2.3 Union-Control adapter would put ~10 GB on
disk that the 2.5 distilled GGUF on this host cannot load. Not done, and recorded here so the next
person does not spend the download working it out again. The scene-control layer's control passes
are still compiled and stored (`controls/` per shot) — they are what a 2.5 control adapter would
consume the day one exists, and they already drive Wan-Animate-2's pose video today.

**What replaces it for now: measuring the guides we do send.** `LTXVAddGuide` pins an anchor at a
frame index, and nothing checked that the clip went anywhere near it — a guide whose strength was
too low, or whose index the 8k+1 length rule snapped past the end of the clip, produced the same
"ok" as one the model honoured. `sequences/drift.py:guide_adherence` extracts the clip's frame at
each guide index and compares it with the anchor, and `generate_video` writes
`video/<shot>/guide-adherence.json` and reports the worst guide across the film.

It reports rather than gates, for a stated reason: the bar has not been calibrated against a live
guided clip, and the existing luminance similarity turns out to be a poor composition measure —
measured on six unrelated rendered cards, completely different pictures sharing a flat ground
scored **0.86-0.93**. A second number (`structural_similarity`, an 8x8 block correlation) is
reported alongside it because it separates better (0.47-0.84 unrelated against 0.83-0.98 for two
moments of one scene), and neither is a gate until a live run says what the threshold should be.

Drift thresholds are also per style and per camera preset now
(`ImageSequenceSettings.drift_thresholds.by_style` / `.by_camera`, layered per field), because one
pair for twelve art directions and nine camera moves either fails honest watercolour frames or
passes drifting photographic ones. The tables start empty: an override has to come from a measured
run, and `sequences.drift.UNCALIBRATED` is the one named "observe, do not gate" profile — it used
to be two magic floats inline in `scripts/generate_holding_hands.py`.
