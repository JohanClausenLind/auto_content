# Blender scene controller skill

Renders the per-shot **control passes** of the 3D scene-control layer from a `ShotSpec`
(`python/content_factory/schemas/shots.py`): rough RGB, depth (8/16-bit + float EXR), camera-space
normals, an indexed segmentation mask, Canny edges, OpenPose-18 skeleton JSON, screen-space layout
boxes and a per-frame `camera.json`. The passes condition HiDream-O1 (layout boxes, skeleton +
rough RGB as references), give LTX-2.5 its keyframe anchors, seed Cutie's mask tracking and are the
training data for a future depth/edge IC-LoRA.

Blender is **not** a Python dependency: the skill drives the system Blender (`/snap/bin/blender`,
5.2.1 LTS) headless with `--background --factory-startup`. This env holds only the runner and the
post-processing libraries (numpy, Pillow, opencv-python-headless).

## Setup (once)

```bash
cd skills/video/blender_scene && uv sync
```

Character assets (MPFB2 / MakeHuman, GPL data, so never vendored here) live under
`/mnt/fast/models/blender-assets/` (`CF_BLENDER_ASSETS`); see `assets_build/` once it exists.
Prop-only shots render with no assets at all.

## Run

```bash
uv run --project skills/video/blender_scene python skills/video/blender_scene/render.py \
    skills/video/blender_scene/tests/fixtures/shot_cube_dolly.json out/cube \
    [--engine workbench|eevee|cycles_cpu] [--frames 0,48,96] [--keep-raw] [--timeout 1800]
```

Exit codes: `0` ok · `2` invalid spec · `3` exception inside Blender (see `out/logs/`) · `4` Blender
or an asset missing · `5` timeout · `6` post-processing mismatch. The last stdout line is one JSON
summary. Set `CF_BLENDER_KEEP_EXR=1` to keep the raw multilayer EXRs for debugging.

## Output layout

```
<out>/
  rough_rgb/frames/NNNN.png       Workbench studio clay render (sRGB, Standard view, outline on)
  depth/frames/NNNN.png           8-bit inverse depth: 255*(1-(z-near)/(far-near)), background 0
  depth16/frames/NNNN.png         16-bit linear depth, background 65535
  depth_exr/frames/NNNN.exr       float32 metres (single channel Z)
  normals/frames/NNNN.png         camera-space (n+1)/2; +X right, +Y up, +Z toward camera; background black
  segmentation/frames/NNNN.png    indexed PNG (mode P): pixel = seg id, 0 = background (Cutie contract)
  canny/frames/NNNN.png           Canny on the contrast-stretched clay render
  skeleton/frames/NNNN.json       per character: OpenPose-18 joints (normalised, y down), bones, openpose18 flat list
  layout/frames/NNNN.json         per entity: box{x,y,w,h}, xxyy, depth range, visible_fraction; hidream_layout_bboxes
  <pass>/frames/NNNN.done.json    {frame_index, pass, input_hash, png_sha256|json_sha256|exr_sha256, ...}
  camera.json                     per frame: position, quaternion (wxyz), lens, intrinsics (fx,fy,cx,cy), world_to_camera 4x4
  metadata.json  spec.json  run.json  logs/
```

`input_hash = sha256(canonical{spec_sha256, pass, frame_index, skill_version, blender_version,
engines, depth range})`, so a Blender upgrade or an engine fallback invalidates the cache.

## How the passes are made

* **Data passes** (depth, normals, object index): Cycles on the **CPU, 1 sample, box filter**,
  written as a multilayer EXR and read back with the OpenImageIO bundled in Blender. Exact integer
  object ids, no GPU needed, byte-identical across runs.
* **Rough RGB**: Workbench (studio light, material colours, object outline, AA 5). Workbench needs
  a GPU context even in `--background`; when that fails the runner falls back to a 16-sample Cycles
  CPU render and records `engines.rgb = "cycles_cpu"` in `metadata.json`.
* **Depth range**: `render.depth_range = "auto"` spans the characters and props over all frames
  (padded 5 %); the ground and walls beyond it clip. Set `{near, far}` explicitly for cross-shot
  consistency.
* **Skeleton**: joints are means of baked vertex anchors on the character's keypoint proxy
  (`body["cf_kp_anchors"]`), projected with the camera intrinsics; `visible` uses a ray cast from
  the camera with per-joint tolerance. Blender never draws skeletons — content-factory renders
  the OpenPose PNGs from the JSON.
* **Determinism**: stamp metadata off, dither 0, `Standard` view transform, no motion blur, no
  DOF, fixed seeds, EXR `DateTime` pinned, PNGs re-encoded canonically by Pillow.

## Verified against Blender 5.2.1 (2026-09-05)

* Multilayer EXR output is `image_settings.media_type = "MULTI_LAYER_IMAGE"` +
  `file_format = "OPEN_EXR_MULTILAYER"`; the file is multi-part, one part per pass; the object
  index channel is named `ViewLayer.Object Index.X`.
* Engines: `CYCLES`, `BLENDER_WORKBENCH`, `BLENDER_EEVEE` (no `BLENDER_EEVEE_NEXT`).
* `gpu.platform` cannot be queried before a render in `--background`; try Workbench, fall back.
* `camera.py`'s look-at quaternion matches `Vector.to_track_quat('-Z', 'Y')` to 4e-8.

## Tests

```bash
just test-blender-skill                                   # pure modules + post-processing, no Blender
uv run pytest -m blender tests/integration/test_blender_scene_render.py -q   # renders the cube fixture twice
```

## Licensing

MakeHuman meshes and assets are CC0. MPFB2 (code and data tables) is GPL-3.0-or-later: it is used
to *build* character assets under `/mnt/fast/models/blender-assets/` and is never imported by the
control plane or vendored into this repository.
