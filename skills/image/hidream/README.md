# HiDream-O1-Image skill

Isolated environment serving the 8B image model on loopback (`127.0.0.1:8801`). The control
plane never imports this code; it talks HTTP via `HiDreamReferenceEditBackend`.

## Setup (once)

```bash
cd skills/image/hidream
uv sync
# flash-attn is REQUIRED by the model's decoder and is installed as a side wheel because no
# torch-2.10 prebuilt exists and this host has no nvcc. Re-run after any `uv sync`:
uv pip install "https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3.post1/flash_attn-2.8.3.post1+cu12torch2.8cxx11abiTRUE-cp312-cp312-linux_x86_64.whl"
# weights (~18 GB):
uvx --from 'huggingface_hub[hf-transfer]' hf download HiDream-ai/HiDream-O1-Image-Dev --local-dir ~/models/HiDream-O1-Image-Dev
```

## Run

```bash
CF_HIDREAM_REPO=~/git/ai_models/image_generators/HiDream-O1-Image \
CF_HIDREAM_MODEL_PATH=~/models/HiDream-O1-Image-Dev \
uv run --project skills/image/hidream python skills/image/hidream/server.py
```

Torch is pinned to 2.8 (not the upstream-suggested 2.10) because 2.8 is the newest release with
a prebuilt flash-attn wheel; upstream's compatibility warning concerns 2.9 only.
