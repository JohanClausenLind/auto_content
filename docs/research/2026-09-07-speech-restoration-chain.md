# Speech restoration chain — Resemble Enhance + ClearerVoice-Studio

Retrieved 2026-09-07

Research behind `python/content_factory/audio/{detect,restore}.py`, the `restore_speech` stage,
and the two skills `skills/audio/{resemble_enhance,clearervoice}`. Every claim is either from an
API/page fetched on 2026-09-07 (URL given), read out of the pinned upstream source, or a
measurement made on this machine (marked **MEASURED**). Sections marked **DECISION** are ours.

---

## A. The chain

The operator's target chain, in order:

```
TTS (Breeze-TTS-2 / Kokoro / a human take)
    -> artifact / noise detection
    -> ClearerVoice          (optional cleanup)
    -> Resemble Enhance      (main restoration)
    -> de-esser
    -> EQ
    -> light compression
    -> true-peak limiter
    -> EBU R128 loudness normalization
    -> final 48 kHz WAV
```

**DECISION — where the cut is.** Everything down to *light compression* is per beat and lives in
`restore_speech`; the *limiter* and *R128* are per programme and live in `mix_audio`'s `master()`.
The reason is the music bed and the SFX: they are mixed under the speech between those two points,
and limiting each beat separately would flatten the programme twice — once before the bed exists
and once after. This preserves the operator's order (nothing is reordered) while putting each step
where it can see the signal it is supposed to act on.

## B. Resemble Enhance

- Repo `resemble-ai/resemble-enhance`, **MIT**, default branch `main`, HEAD
  `8e978149bfe8abab3eb77d965d579a111afdb0ff` (2024-12-03) — pinned.
  — https://api.github.com/repos/resemble-ai/resemble-enhance
- Weights `ResembleAI/resemble-enhance`, **MIT**, revision
  `4e3510ce4a8391159f665903544c5150bee7b2cb`, not gated. `enhancer_stage2/` is 3 files (680 MB);
  the rest of the repo is demo `.mp4`s.
  — https://huggingface.co/api/models/ResembleAI/resemble-enhance
- Architecture, read from the source: a UNet **denoiser**, a **latent conditional flow-matching**
  restorer (`enhancer/lcfm/`) and a **UnivNet** vocoder (`enhancer/univnet/`). `hparams.yaml` on
  disk sets `wav_rate: 44100`, `cfm_solver_method: midpoint`, `cfm_solver_nfe: 64`.
- `enhance()` asserts `0 < nfe <= 128`, `solver in (midpoint, rk4, euler)`, `lambd` and `tau` in
  `[0, 1]`; the CLI defaults are `nfe=32, solver=midpoint, lambd=0.5, tau=0.5`.
  — `resemble_enhance/enhancer/inference.py` @ 8e97814
- **Length is preserved.** `inference()` resamples to `hp.wav_rate`, chunks at 30 s with 1 s
  overlap, and `merge_chunks(..., length=dwav.shape[-1])` trims back to the resampled input
  length. — `resemble_enhance/inference.py` @ 8e97814

### B1. Two traps in its environment

- **deepspeed is required to import an inference-only path.**
  `enhancer/enhancer.py:12` does `from ..utils.distributed import global_leader_only`, and
  `utils/__init__.py` imports `.engine`, which imports `deepspeed`. There is no inference-only
  entry point that avoids it. Its published artifact is an **sdist only** (no wheel), its
  `setup.py` imports torch, and it calls `installed_cuda_version()` whenever the torch accelerator
  is CUDA — which raises `MissingCUDAException` on a host with no nvcc, as this one has.
  — https://pypi.org/pypi/deepspeed/json ; `deepspeed-0.19.6/setup.py:111`, `op_builder/builder.py:51`
  **DECISION:** `[tool.uv] no-build-isolation-package = ["deepspeed"]` plus
  `[tool.uv.extra-build-variables] deepspeed = { DS_ACCELERATOR = "cpu" }`, which is a *build*-time
  variable only. One `uv sync` then works. **MEASURED:** verified 2026-09-07 on a clean venv.
- **numpy must stay below 2.** `enhancer/lcfm/cfm.py:73` is
  `float(scipy.optimize.fsolve(lambda a: h(1 / n, a) - 0.5, x0=0))` — a `float()` of a shape-`(1,)`
  array. numpy 2 raises `TypeError: only 0-dimensional arrays can be converted to Python scalars`.
  **MEASURED:** `--mode enhance` dies there on numpy 2.3 and runs on 1.26.

## C. ClearerVoice-Studio

- Repo `modelscope/ClearerVoice-Studio`, **Apache-2.0**, HEAD
  `6b3774dc79c46ae8bed2a4fa5f706f0ac8c75c61` (2025-08-14).
  — https://api.github.com/repos/modelscope/ClearerVoice-Studio
- The published inference package is `clearvoice` **0.1.2** (Apache-2.0, `>=3.8`), which pins
  `librosa==0.10.2.post1`, `soundfile==0.12.1`, `numpy<2.0,>=1.24.3`, `torch>=2.0.1`.
  — https://pypi.org/pypi/clearvoice/json
- Its `network_wrapper.__call__` dispatches four tasks; `speech_super_resolution` with
  `MossFormer2_SR_48K` is present in 0.1.2 even though the `ClearVoice` docstring lists only
  three. Config files for both models ship in the wheel.
  — `clearvoice-0.1.2/clearvoice/{network_wrapper.py,config/inference/}`
- Weights, both **Apache-2.0**, both ungated:
  `alibabasglab/MossFormer2_SE_48K` @ `eff8c97925c8bec812af707814b3e5d777fd4503` (212 MB) and
  `alibabasglab/MossFormer2_SR_48K` @ `39eb1f25ea84f5e0315ade9ac0070fff216fc690`. The SR repo's
  `last_best_checkpoint` manifest names only `last_best_checkpoint_m.pt` and
  `last_best_checkpoint_g.pt`; its third file `do_03925000` (1.7 GB) is HiFi-GAN training state.
  **MEASURED:** a full SR run completes with that file absent.
  — https://huggingface.co/api/models/alibabasglab/MossFormer2_SE_48K (and `..._SR_48K`)
- Both configs set `sampling_rate: 48000`. `SpeechModel.decode()` truncates the result to
  `self.data['audio_len']`, so **length is preserved**.
  — `clearvoice/networks.py` @ 0.1.2

### C1. Two things the skill does differently from upstream

- **Upstream writes at the input's sample rate.** `SpeechModel.write_audio` resamples the model's
  48 kHz output back to `self.data['sample_rate']` — for super-resolution that discards exactly
  the band the model just created. The skill takes the returned array and writes 48 kHz itself.
- **Upstream downloads at load time.** `load_model()` calls `snapshot_download` for any missing
  checkpoint, and `checkpoint_dir` is resolved relative to the working directory. The skill runs
  in a temporary directory whose `checkpoints/<MODEL>` symlinks the pinned local weights, and
  exits non-zero if one is missing, so a production run never reaches the network.

## D. What each model actually does — MEASURED 2026-09-07

9.25 s of Kokoro-82M narration at 24 kHz (`af_heart`), `--device cpu`, i9-12900K. "Band limit" is
the highest FFT bin within 50 dB of the peak bin; the band figures are fractions of total energy.

| Input / step | Rate | RTF | Duration | Band limit | 12-16 kHz | >16 kHz |
| --- | --- | --- | --- | --- | --- | --- |
| Kokoro (source) | 24 kHz | — | 9250 ms | 12.00 kHz | 0.000001 | 0 |
| Resemble Enhance `enhance` | 44.1 kHz | 18.6 | 9250 ms | 16.37 kHz | 0.0015 | 0.00019 |
| ClearerVoice SE | 48 kHz | 2.5 | 9250 ms | 11.58 kHz | 0.000000 | 0 |
| ClearerVoice SR | 48 kHz | 3.0 | 9248 ms | **19.31 kHz** | 0.014 | 0.00072 |

Three conclusions, and they are why the pipeline treats these as three separately gated steps:

1. **Enhancement does not extend the band.** MossFormer2_SE_48K left the band limit where it found
   it. Running it on a clean synthetic take buys nothing, so it is gated on the noise measurements.
2. **Super-resolution does, strongly.** It is the right tool for the "missing high frequencies"
   symptom of a 16/24 kHz TTS, and is gated on the band-limit measurement.
3. **Resemble Enhance does both jobs at once**, more modestly on the band than SR but with the
   denoise and detail restoration SR does not do. It is the default "main restoration" step.

**MEASURED — CPU is a fallback.** RTF 18.6 for Resemble Enhance means 9 s of speech costs 3
minutes. The two ClearerVoice models are ~3x realtime. A real run should set
`CF__SPEECH_RESTORATION__DEVICE=cuda` and take the card between the image/video stages.

## E. The FFmpeg tail

FFmpeg 6.1.1 on this host has `deesser`, `acompressor`, `alimiter`, `equalizer`, `highpass`,
`bass`, `treble`, `loudnorm` and `ebur128` (checked with `ffmpeg -h filter=<name>`).

- **DECISION — the de-esser exposes FFmpeg's own controls.** `deesser` takes `i` (intensity),
  `m` (max de-essing) and `f` (frequency) as 0-1 normalized values, not Hz and dB. `VoiceChainSpec`
  mirrors that rather than inventing units that would have to be guessed back at the filter.
  **MEASURED:** at `i=1.0` it drops the 5-9 kHz / 0.3-5 kHz energy ratio of a deliberately spitty
  signal below the same chain with the de-esser off (`test_de_esser_reduces_sibilance`).
- **DECISION — the limiter sits at -1.5 dBTP, below the -1.0 dBTP delivery ceiling.** loudnorm's
  gain move happens after it, and a limiter parked exactly at the ceiling leaves that move nowhere
  to go. **MEASURED:** a stem measuring 0.0 dBTP comes out of `limit_true_peak` at exactly
  -1.5 dBTP, and `master()` still lands the programme at -14.6 LUFS / -1.0 dBTP.
- **DECISION — length is enforced in the same pass.** The tail ends with
  `apad=whole_len=N,atrim=end_sample=N` at the delivery rate, so the beat leaves the chain the
  exact sample count it entered with. Anything else would move every caption.

## F. Applying the same chain to generated non-speech audio — MEASURED 2026-09-07

The operator asked whether the same pipeline should clean up generated sound effects. **The
architecture should be shared; the models must not be.** This is the experiment that decided it.

Inputs: three sounds from the repo's own generated library `assets/sfx` (Stable Audio 3 Small SFX),
resampled to mono 48 kHz, 2-3 s each. Processed with the speech chain's models exactly as a
narration beat would be.

| Model | Sound | RMS in | RMS out | Energy surviving |
| --- | --- | --- | --- | --- |
| ClearerVoice `MossFormer2_SE_48K` | `transition/whoosh_soft` | -24.0 dBFS | **-70.9 dBFS** | 0.5 % |
| ClearerVoice `MossFormer2_SE_48K` | `weather/rain_light_loop` | -33.6 dBFS | **-73.5 dBFS** | 1.0 % |
| ClearerVoice `MossFormer2_SE_48K` | `impact/impact_soft` | -28.0 dBFS | **-71.3 dBFS** | 0.7 % |
| Resemble Enhance (`enhance`, nfe 32) | `transition/whoosh_soft` | -24.0 dBFS | **-73.4 dBFS** | 0.3 % |

Resemble Enhance's output was measured further: waveform correlation with its input **0.002**, and
the spectrum collapsed to sub-200 Hz (the 200 Hz-1 kHz band went from 0.148 to 0.007 of total
energy while `<200 Hz` went from 0.845 to 0.987). It did not degrade the whoosh — it replaced it
with an unrelated low rumble.

Neither result is a bug. Both models are doing what they were trained to do: a speech *enhancer*
suppresses everything that is not a voice, and a speech *restorer* reconstructs what it hears as
speech. A whoosh is, to both of them, noise.

**DECISION.** `content_factory.audio.condition` shares the detection stage, the true-peak limiter
and the length lock with the voice chain, and has no field, flag or code path for either speech
model; `condition_sound` refuses an `AudioProfile.speech` spec outright. What non-speech material
gets instead is damage repair gated on measurement (declip, declick at the seams between generated
windows, DC removal, sub-sonic trim) and normalisation to a known loudness.

**DECISION — the profile decides what is a defect, not the measurement.** The same numbers mean
opposite things: a flat spectrum is hiss in a narration take and the entire content of a rain bed.
`AudioArtifactThresholds.for_profile` disables the noise-floor, spectral-flatness and band-limit
checks for non-speech, reinterprets the 5-9 kHz ratio as harshness rather than sibilance at a
looser limit, and keeps every integrity check (clipping, DC, dead asset). **MEASURED:** the same
noise bed reports `spectral_flatness` + `noise_floor` under the speech profile and nothing under
the sound-effect profile, from identical measurements.

**DECISION — normalise, because the alternative is an accidental level.** Before this, the SFX bed
entered the mix at whatever loudness the model rendered, minus a blind 22 dB. **MEASURED** on real
assets: the rain loop landed at -53.2 LUFS, the ocean loop at -49.6, the mock bed at -58.8 — in a
-14 LUFS programme, all inaudible. Normalised to -23 LUFS integrated (the same target
`assets/sfx/library.json` uses for beds) the bed is 9 LU under the programme and `gain_db` becomes
a trim. **MEASURED:** narration + conditioned bed at 0 dB trim masters to -14.30 LUFS / -1.00 dBTP;
the old path measured -14.60 / -1.00. The programme target is unaffected; only the bed's audibility
and its reproducibility change.

**MEASURED — a one-shot cannot always reach its target, and says so.** `whoosh_soft` and
`impact_soft` asked for -16 LUFS max-momentary and got +4.6 dB and +2.4 dB respectively before the
-1 dBTP ceiling stopped them; the report carries `gain_limited: true` and a
`loudness:limited_by_true_peak_or_max_gain` step rather than clipping the transient to fit. This is
independently the same constraint `assets/sfx/README.md` documents for its own one-shots.
