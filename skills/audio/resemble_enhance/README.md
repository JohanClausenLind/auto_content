# Resemble Enhance skill

Isolated environment for **resemble-ai/resemble-enhance** — the main restoration stage of the
voice chain. A UNet denoiser, a latent conditional flow-matching restorer and a UnivNet vocoder
at 44.1 kHz: it removes noise *and* rebuilds detail a band-limited or metallic take never had,
which is the difference between this and a denoiser. The control plane never imports this code; it
runs `run.py` by subprocess, the way `KokoroTTS` runs `skills/audio/kokoro/run.py`.

**Licence:** MIT for both the inference code and the weights. No usage restriction to revisit.

## What is on disk
- Weights: `models/speech_restoration/ResembleEnhance` (repo-local index link →
  `/mnt/fast/models/resemble-enhance`), HF `ResembleAI/resemble-enhance` revision `4e3510c`,
  `enhancer_stage2/` only (680 MB: `hparams.yaml`, `ds/G/latest`,
  `ds/G/default/mp_rank_00_model_states.pt`) — the repo's demo `.mp4` files are not downloaded.
- Inference code: `external/resemble-enhance` @ `8e97814` (2024-12-03, upstream HEAD).

`run.py` never downloads: the three files above must exist or it exits 3. Upstream's own loader
would fetch them from Hugging Face mid-run, which a production pipeline must not do.

## Setup (once)
```bash
cd skills/audio/resemble_enhance && uv sync
```
Two things about that environment are deliberate:

- **deepspeed is a dependency of an inference-only skill.** `resemble_enhance.enhancer.enhancer`
  imports `..utils.distributed`, and `resemble_enhance/utils/__init__.py` imports the training
  `Engine`, so `import deepspeed` happens whether or not anything trains. It ships an sdist whose
  `setup.py` imports torch, hence `[tool.uv] no-build-isolation-package`, and it asks for the
  installed CUDA version when the torch accelerator is CUDA, which this host has no nvcc for —
  hence `DS_ACCELERATOR=cpu` in `[tool.uv.extra-build-variables]`. That variable affects the
  *build* only; the installed package reads the accelerator at import time.
- **numpy is pinned below 2.** `enhancer/lcfm/cfm.py:73` does `float(scipy.optimize.fsolve(...))`
  on a shape-`(1,)` array. numpy 2 turns that deprecation into a `TypeError` and `--mode enhance`
  dies in the CFM solver (verified 2026-09-07).

## Run
```bash
uv run --project skills/audio/resemble_enhance python skills/audio/resemble_enhance/run.py \
  --in narration.wav --out restored.wav --device cuda
```
Prints one JSON line: wav path, sample rate (44100), in/out duration, load/inference time, RTF,
peak VRAM. `--mode denoise` runs the denoiser alone (no generative step, no band repair).
`--nfe/--solver/--lambd/--tau` are the CFM controls (upstream defaults 32 / midpoint / 0.5 / 0.5).

Env overrides: `CF_RESEMBLE_ENHANCE_REPO`, `CF_RESEMBLE_ENHANCE_MODEL_PATH`.

## Measured on vegaserv (2026-09-07)
9.25 s of 24 kHz Kokoro narration, `--device cpu`, `--mode enhance --nfe 32`: load 5.1 s,
inference 172 s (**RTF 18.6** — CPU is a fallback, not a production path), output 44.1 kHz and
**exactly 9250 ms**, and energy above 12 kHz went from nothing to 0.17 % of the total (12-16 kHz
band 0.0015, above 16 kHz 0.0002). The output length in seconds is always the input length: the
script refuses to write a file that drifted more than `--max-drift-ms` (default 10).
