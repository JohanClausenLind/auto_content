# Breeze TTS 2 skill (evaluation)

Isolated environment for **BreezeBlue/Breeze-TTS-2** (3B, English + Chinese, voice design /
clone / direction). The control plane never imports this code; it would talk to `run.py` the way
`KokoroTTS` talks to `skills/audio/kokoro/run.py`.

**Licence:** the inference code is Apache-2.0; the *weights and self-hosted outputs* are under the
BreezeBlue Research and Non-Commercial License v1.1. Operator accepted this for local testing and
research on 2026-09-05. Revisit before any published or client-facing use.

## What is on disk
- Weights: `models/speech/Breeze-TTS-2` (repo-local index link → `/mnt/fast/models/breeze-tts2`) (HF revision `799624c`, complete; codec + audio
  tokenizer included — nothing else to download).
- Inference code: `external/breeze-tts` @ `43e2ea1` (2026-09-04) — the git-ignored upstream
  checkouts directory inside this repo (moved from `~/ai-video-stack/repos` on 2026-09-05).

## Setup (once)
```bash
cd skills/audio/breeze && uv sync      # torch 2.9.1 + qwen-tts 0.1.1 + transformers 4.57.3
```

## Run
```bash
echo "Welcome aboard. Your journey begins now." | \
  uv run --project skills/audio/breeze python skills/audio/breeze/run.py \
  --instruction "A warm, calm narrator with clear diction." --cfg-scale 4
```
Prints one JSON line: wav path, sample rate (24 kHz), duration, load/TTFA/RTF, peak VRAM.
`tokens` is always empty: Breeze has **no word timestamps**; captions need WhisperX or
faster-whisper alignment (ADR-0004) before this can drive a narrated timeline.

Env overrides: `CF_BREEZE_REPO`, `CF_BREEZE_MODEL_PATH`. `--fast-all` enables the CUDA-graph fast
path (~14 GiB VRAM, long warmup); the default eager path needs ~7.7 GiB.
