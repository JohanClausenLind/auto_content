# Rendering

Contract chain: `StoryPlan` (beats + scenes, semantic parameters) → `CompiledTimeline` (integer
frames, contiguous scenes, audio cues) → `RenderBundle` (plan/timeline or artboard + resolved
datasets, sources, local asset paths, brand tokens, seed) → Node renderer → PNG/MP4 → deterministic
QC → ArtifactStore.

- **Time bases (2.3):** audio observations in integer milliseconds; `timeline/compiler.py` converts
  to integer frames (`ms_to_frames`, round-half-up; round-up for durations). No float seconds reach
  the renderer. A narrated deliverable refuses to compile before measured word timings exist.
- **Numbers (2.4):** a scene/layer shows a figure only through `DataRef → DatasetTable` rows; the
  bundle validator rejects references to datasets it does not carry.
- **Renderer (ADR 0003):** `apps/renderer/scripts/render-artboard.mjs --bundle b.json --out x.png`
  and `render-timeline.mjs --bundle b.json --out x.mp4 [--scene <id>]`. Remotion compositions
  `Artboard` and `Timeline` read the bundle; components in `packages/content-ui` (static) and
  `packages/video-ui` (temporal) use only `useCurrentFrame`/`interpolate`, seeded randomness, local
  fonts, no CSS transitions, no network. Fixed concurrency for reproducibility.
- **Python side:** `video/render.py` invokes the scripts with argument arrays (never shell strings),
  stores outputs content-addressed under the workspace, and runs `qc/media.py`: ffprobe codec /
  dimensions / fps / frame count / pix_fmt / fast-start, black or frozen frame sampling for video;
  PNG integrity / dimensions / blank detection for stills. Static outputs never fail for "no FPS".
- **Cache unit:** `--scene` renders one scene's frame range so a retimed or re-variant scene rebuilds
  alone; the demo's smoke quality renders exactly one scene.
- **Demo:** `content-factory demo --quality smoke|demo` writes `projects/prj_demo00000001/…`
  (manifest, brief, dag, per-deliverable spec/artboards/timeline/exports, final/run-report.json).
