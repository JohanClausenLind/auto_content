# ADR 0006 — ComfyUI as a governed sidecar managed by comfy-cli

- Status: accepted (2026-08-27)
- Research: docs/research/comfyui-and-image-models.md

## Context
ComfyUI (GPL-3.0, v0.34.0) is the most capable open image/video execution engine, but its node
ecosystem is a supply-chain risk and its licence is incompatible with vendoring. The operator
already has a comfy-cli-managed install at `~/git/ComfyUI` (0.33.0) with 69 GB of models.

## Decision
- ComfyUI runs as a **separate process**; we never vendor its code or Vue frontend. Lifecycle is
  exclusively through **comfy-cli** (1.17.0 installed; 1.18.0 current; GPL-3.0-only CLI, also a
  separate process): `comfy --workspace=<dir> install`, `comfy launch --background`, `comfy stop`,
  `comfy env`, `comfy node install|deps-in-workflow --output|install-deps|bisect|save-snapshot|
  restore-snapshot`, `comfy model download --url … --relative-path models/<class>`, `comfy model
  list|remove`, `comfy run --workflow … --wait --json`, `comfy workflow validate` (the bare
  `comfy validate` is deprecated). The default workspace is `<repo>/.comfy`; the config may point
  at an existing workspace such as `~/git/ComfyUI` to avoid a second multi-gigabyte install.
- Production executes only **signed `ComfyWorkflowPackage`s**: canonical API-format JSON, typed
  parameter bindings (the only mutable inputs), exact ComfyUI range, comfy-cli node snapshot as
  lockfile, required models with hashes/licences, capability flags (`text_to_image`,
  `reference_image_edit`, `control_pose|layout|depth|edge`, `mask_inpaint`, `upscale`,
  `deterministic_seed`, `max_resolution`), golden fixtures, promotion record.
- The adapter uses the documented HTTP/WS API (`/prompt`, `/ws?clientId=`, `/history/{id}`,
  `/view`, `/object_info`, `/system_stats`, `/interrupt`, `/free`, `/queue`) and validates every
  workflow against `/object_info` and an allowlist before submission. Phase 0 proved this against
  a fixture server: validate, execute, stream progress, cancel, import outputs with provenance.
- **First reference-edit family: HiDream-O1-Image** (8B, MIT, released 2026-05-08; native
  ComfyUI nodes `HiDreamO1ReferenceImages`, `EmptyHiDreamO1LatentImage`,
  `HiDreamO1PatchSeamSmoothing` since 2026-05-12, present in the local checkout). All-in-one
  checkpoint in `models/checkpoints/` (fp8_scaled 8.07 GB; bf16 16.4 GB) — fits the RTX 3090.
  Pose/layout conditioning has no core node yet, so the sequence engine's control path uses
  **Qwen-Image-Edit-2509** (Apache-2.0, 20.4 GB fp8) or Qwen-Image + InstantX Union ControlNet
  until a HiDream-O1 control node exists. Frame interpolation is in core
  (`FrameInterpolationModelLoader`; RIFE MIT / FILM Apache-2.0 weights).
- The Workflow Lab may use **comfy-mcp** (0.10.0, AGPL-3.0-or-later OR commercial) as a separate
  process for assistant-driven graph editing; it is optional, never linked, and never touches
  production. AI-authored workflows never promote themselves.
- Registry licence metadata is optional and unreliable; licences are verified from the node's
  repository before allowlisting.

## Consequences
- Production is reproducible per package (snapshot + model hashes) and rejects unpinned nodes,
  arbitrary paths/URLs, and runtime installation.
- Model downloads go through `comfy model download` with visible size/licence/disk estimates.

## Amendment 2026-09-06 — control conditioning for LTX-2.5 is keyframe guidance

The Blender scene-control layer (ADR 0012) does not condition LTX-2.5 on control *video*: no depth,
pose or edge IC-LoRA exists for LTX-2.5. Instead the packages `ltx-2.5.i2v-guided{1..4}` (`media/ltx_packages.py`) pin
HiDream anchors at Blender-chosen frame indices through `LTXVAddGuide`; camera moves are computed in
3D and expressed as which frames the anchors sit on. Control video for LTX-2.5 returns when a V2V
IC-LoRA is trained on the stored depth / edge passes. The `--cache-none` requirement of the GGUF
stack means the client polls `/history` (`run_package(collect="history")`) instead of relying on
websocket output events.

## Amendment 2026-09-09 — the 2.3 adapters' incompatibility is upstream's statement, not our inference

The sentence above used to read "the on-disk IC-LoRAs target LTX-2.3 **and are recorded as
incompatible**", which sounds like a status this repo assigned after reading a `base_model` field
in the adapter metadata. It is not an inference. Lightricks states it, in the upstream
documentation vendored at `external/LTX-2` (LTX-2 1.3.0, 2026-08-25), twice:

* `README.md`, "Legacy: LTX-2.3": *"Files are not interchangeable between the two models, and **a
  LoRA only works with the model it was trained on**."*
* `MODELS-LTX-2.3.md`, first paragraph: *"If you want LTX-2.5 instead, start from the Quick Start
  — its weights are split per component and are **not interchangeable** with the files here. **The
  LoRAs at the end of this page were trained on LTX-2.3, so pair them with an LTX-2.3
  checkpoint.**"*

`LTX-2.3-22b-IC-LoRA-Union-Control` is listed under exactly those LoRAs, so it is one of the
adapters that sentence is about. Checked against the same source on 2026-09-09: the **only**
LTX-2.5 IC-LoRA Lightricks publishes is `LTX-2.5-22b-IC-LoRA-Pixel-Spatial-Upscaler`, a *detailing*
adapter for `DFRPipeline`'s refinement stage. There is no 2.5 depth, canny, pose, union or
motion-track adapter to download.

So a control-video package for LTX-2.5 is still blocked on an upstream weight that does not exist,
and the decision recorded above stands unchanged. What changed is where the claim comes from: a
primary source with a quotation and a date, rather than a status field whose provenance a reader
had to guess at. `models/video_stack.py`'s `ltx-2.3-ic-loras` entry carries the same citation.

Two things that do *not* need the missing adapter, and are done instead (2026-09-09):
`sequences/drift.py:guide_adherence` measures whether a generated clip actually passed through the
`LTXVAddGuide` anchors it was given — the guided path had no such check, so a guide whose strength
was too low, or whose index the 8k+1 length rule snapped past the end of the clip, read as success.
And drift thresholds are now per style and per camera preset
(`ImageSequenceSettings.drift_thresholds.by_style` / `.by_camera`) rather than one global pair.
