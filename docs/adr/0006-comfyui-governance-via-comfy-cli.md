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
