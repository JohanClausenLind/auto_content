# Chatterbox Turbo skill

Isolated environment for **ResembleAI/chatterbox-turbo** (350M, English), the explainer TTS
benchmark's alternate to Qwen3-TTS. The control plane never imports this code; the benchmark
(`python/content_factory/explainer/tts_bench.py`) runs `run.py` as a subprocess and reads one
JSON line.

**Licence:** MIT for the inference code (`chatterbox-tts` on PyPI) and for the weights (model
card, `license: mit`). No commercial restriction. **Every output carries the Resemble "Perth"
implicit watermark** — the library applies it inside `generate()` and it cannot be switched off
from this executor; the model card's own detector recovers it from the wav.

## What is on disk

- `models/speech/Chatterbox-Turbo` → `/mnt/fast/models/chatterbox-turbo`, HF revision `749d1c1`
  (`models/weights.py`, key `chatterbox-turbo`): `t3_turbo_v1.safetensors` (1.9 GB),
  `s3gen_meanflow.safetensors` (1.1 GB), `ve.safetensors` (5.6 MB), `conds.pt` (the built-in
  voice), tokenizer files. `s3gen.safetensors` (1.1 GB) is the non-meanflow decoder and is not
  loaded by Turbo.
- Loading is `ChatterboxTurboTTS.from_local(model_dir, device)`: it reads the four files above
  and the tokenizer straight from the directory, so no Hub cache layout is needed. `run.py` also
  sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` before any import, so an attempt to download
  anything raises instead of reaching the network.

## Setup (once)

```bash
uv sync --project skills/audio/chatterbox
```

Resolved 2026-09-22 from PyPI and `https://download.pytorch.org/whl/cu126`: `chatterbox-tts 0.1.7`
(which itself pins `torch==2.6.0`, `torchaudio==2.6.0`, `transformers==5.2.0`, `numpy<2`),
`torch 2.6.0+cu126`, `torchaudio 2.6.0+cu126`, `numpy 1.26.4`, `librosa 0.11.0`,
`resemble-perth 1.0.1`, `safetensors 0.5.3`, `diffusers 0.29.0`, `s3tokenizer 0.3.0`,
`gradio 6.8.0` (a hard dependency of the package, unused here), `soundfile 0.14.0`,
`setuptools 80.9.0`. Two pins exist because the resolver got them wrong:

- the thirteen `nvidia-*-cu12` packages and `triton 3.2.0` are the x86_64 cu126 wheel's own
  `Requires-Dist`; uv's lock read another platform's metadata and dropped them, and torch then
  failed at import on `libcudart.so.12`;
- `setuptools==80.9.0`: `resemble-perth` imports `pkg_resources`, removed in setuptools 81, and
  without it `perth.PerthImplicitWatermarker` silently becomes `None`.

Python 3.12, where the package's `numpy<2` pin applies; the venv is 2.3 GB, torch included.

## Run

```bash
uv run --project skills/audio/chatterbox python skills/audio/chatterbox/run.py \
  --text "Your program takes 100 milliseconds." --out /tmp/take.wav --seed 7
```

Options: `--text` (or stdin), `--out`, `--seed`, `--ref-audio <wav>` (clone that voice; the
library requires more than 5 s), `--device` (default `cuda`), `--model-dir`, `--max-chunk-chars`.
Without `--ref-audio` the built-in voice in `conds.pt` speaks.

Turbo stops at 1000 speech tokens (about 40 s), so `run.py` packs whole sentences into chunks of
at most 400 characters, generates each and joins them with a 250 ms pause; the JSON reports how
many chunks a text took. Prints one JSON line:

```json
{"wav": "...", "duration_s": 6.08, "sample_rate": 24000, "peak_vram_mib": 2878, "wall_s": 6.623,
 "load_s": 6.36, "chunks": 1, "seed": 7, "mode": "builtin_voice",
 "model_revision": "ResembleAI/chatterbox-turbo@749d1c1", "watermark": "perth", "device": "cuda"}
```

`wall_s` is synthesis only (model load is `load_s`); `peak_vram_mib` is
`torch.cuda.max_memory_allocated()` over the synthesis, as `skills/audio/breeze/run.py` reports
it. The benchmark measures the whole process from outside with `nvidia-smi` as well.

`speaker_sim.py --ref <creator.wav> --wav <take.wav>` prints the cosine similarity of the two
voices through the model's own voice encoder (`ve.safetensors`, MIT), on the CPU. The benchmark
calls it only when a creator reference recording is supplied.

## Measured

Smoke run 2026-09-22 on the RTX 3090, one 16-word sentence, built-in voice: 6.08 s of audio in
6.6 s (cold kernels), load 6.4 s, peak 2878 MiB allocated. The benchmark table for the five
passages is in `docs/journal/2026-09.md` under that date.
