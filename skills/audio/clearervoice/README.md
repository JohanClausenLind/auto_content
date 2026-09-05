# ClearerVoice-Studio skill

Isolated environment for **modelscope/ClearerVoice-Studio**, the optional cleanup and band
extension steps of the voice chain. Two of its models are used:

| `--task` | Model | What it does |
| --- | --- | --- |
| `enhancement` | `MossFormer2_SE_48K` | noise, hum and room off a dirty 48 kHz take |
| `super_resolution` | `MossFormer2_SR_48K` | rebuilds the band a 16/24 kHz TTS never produced |

The control plane never imports this code; it runs `run.py` by subprocess.

**Licence:** Apache-2.0 for the code and for both sets of weights.

## What is on disk
- `models/speech_restoration/MossFormer2_SE_48K` → `/mnt/fast/models/mossformer2-se-48k`
  (HF `alibabasglab/MossFormer2_SE_48K` @ `eff8c97`, 212 MB).
- `models/speech_restoration/MossFormer2_SR_48K` → `/mnt/fast/models/mossformer2-sr-48k`
  (HF `alibabasglab/MossFormer2_SR_48K` @ `39eb1f2`, 419 MB: the two checkpoints its
  `last_best_checkpoint` manifest names. The repo's 1.7 GB `do_03925000` is HiFi-GAN training
  state — a verified SR run never opens it, so it is not downloaded).

The inference code is the published `clearvoice==0.1.2` wheel rather than the checkout;
`external/ClearerVoice-Studio` @ `6b3774d` is kept as the readable reference for it.

## Setup (once)
```bash
cd skills/audio/clearervoice && uv sync
```
`clearvoice` caps numpy below 2.0 itself, so torch is the only pin worth making here.

## Run
```bash
uv run --project skills/audio/clearervoice python skills/audio/clearervoice/run.py \
  --in narration.wav --out clean.wav --task enhancement --device cuda
```
Prints one JSON line: wav path, sample rate (48000), in/out duration, load/inference time, RTF,
peak VRAM. Env override: `CF_CLEARERVOICE_MODEL_ROOT`.

Two things `run.py` does that upstream does not:

- **It writes at 48 kHz.** Upstream's file writer resamples the result back to the *input* file's
  rate, which for super-resolution throws away exactly the band that was just created.
- **It never reaches the network.** Upstream resolves `checkpoint_dir` relative to the working
  directory and `snapshot_download`s whatever is missing. `run.py` runs in a private temporary
  directory whose `checkpoints/<MODEL>` symlinks the pinned local weights, and exits 3 if a
  checkpoint the manifest names is absent.

## Measured on vegaserv (2026-09-07)
9.25 s of 24 kHz Kokoro narration, `--device cpu`:

| Task | Load | Inference | RTF | Duration | Band limit |
| --- | --- | --- | --- | --- | --- |
| `enhancement` | 2.4 s | 23.4 s | 2.5 | 9250 → 9250 ms | 12.0 → 11.6 kHz (cleans, does not extend) |
| `super_resolution` | 2.0 s | 27.4 s | 3.0 | 9250 → 9248 ms | 12.0 → **19.3 kHz** |

That split is why the pipeline treats them as two steps with two separate gates: enhancement is
for a noisy take, super-resolution for a band-limited one, and a 24 kHz TTS is the latter.
