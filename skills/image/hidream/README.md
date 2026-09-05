# HiDream-O1-Image skill

Isolated environment serving the 8B image model on loopback (`127.0.0.1:8801`). The control
plane never imports this code; it talks HTTP via `HiDreamReferenceEditBackend`.

**Which weights are on this host:** the model index calls the directory `HiDream-O1-Image-Dev`,
but the files under `/mnt/fast/models/hidream-o1` are the **full** `HiDream-O1-Image` (35 GB fp32,
model card title "HiDream-O1-Image"). The server therefore defaults to `CF_HIDREAM_MODEL_TYPE=full`
(50 steps, guidance 5.0); set `dev` only if you download the distilled Dev checkpoint.

## Conditioning (2026-09-06)

`POST /generate` takes `ref_images_b64` (a list; identity references first, then the Blender rough
render and the OpenPose skeleton — the upstream README §4 "IP_skeleton" pattern) and
`layout_bboxes` as relative `[x, y, w, h]` boxes (converted to upstream's `[x1, x2, y1, y2]`; box i
places reference i; at most 5). The legacy single `ref_image_b64` still works. Upstream's IP
pipeline supports layout and skeleton conditioning since 2026-05-13; depth/normals/canny have no
path into HiDream and are not sent.

## Setup (once)

```bash
cd skills/image/hidream
uv sync
# flash-attn is REQUIRED by the model's decoder and is installed as a side wheel because no
# torch-2.10 prebuilt exists and this host has no nvcc. Re-run after any `uv sync`:
uv pip install "https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3.post1/flash_attn-2.8.3.post1+cu12torch2.8cxx11abiTRUE-cp312-cp312-linux_x86_64.whl"
# weights: already present on vegaserv at /mnt/fast/models/hidream-o1 (full model, fp32). Elsewhere:
uvx --from 'huggingface_hub[hf-transfer]' hf download HiDream-ai/HiDream-O1-Image --local-dir models/image_generation/HiDream-O1-Image-Dev
```

## Run

```bash
uv run --project skills/image/hidream python skills/image/hidream/server.py
```
Defaults resolve inside the repo: code at `external/hidream-o1-code`, weights at
`models/image_generation/HiDream-O1-Image-Dev`. Override with `CF_HIDREAM_REPO` / `CF_HIDREAM_MODEL_PATH`.

Torch is pinned to 2.8 (not the upstream-suggested 2.10) because 2.8 is the newest release with
a prebuilt flash-attn wheel; upstream's compatibility warning concerns 2.9 only.
