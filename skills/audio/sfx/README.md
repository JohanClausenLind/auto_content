# SFX skill — the video sound-effect and ambience library

Builds `assets/sfx/`: **49 sounds** for video work, split into short one-shots (transitions,
impacts, UI/motion-graphics cues) and long seamless ambience beds (weather, place, room tone,
drones). Two builders fill it and they share everything downstream of the source:

| builder | recipe | fills | source |
|---|---|---|---|
| `ingest_recorded.py` | `recorded.json` | 38 sounds | excerpts from a licensed recording bundle held locally |
| `build_library.py` | `library.json` | 11 sounds | generated with **Stable Audio 3 Small-SFX** (433M params, 44.1 kHz, SFX-only) |

A recorded sound and a generated one are interchangeable in a mix: same 44.1 kHz stereo 24-bit
FLAC, same loop wrap, same two loudness targets, same QC fields. Either builder re-renders the
whole manifest and README through `index.py`, so a partial rebuild leaves the index describing
the whole library — and `build_library.py` needs no bundle, `ingest_recorded.py` no GPU or model.

Recording beats generation wherever the bundle holds the sound, which is why the recorded half is
the larger one. It does not hold everything: there is no stream in it, no night-only cricket bed
and no office room tone, so those three stay generated rather than be filled with something that
is nearly the right sound.

The control plane never imports this code, exactly like `skills/audio/breeze`.

**Licences.** Inference code (`external/stable-audio-3`, MIT); weights under the **Stability AI
Community License** and the bundled T5Gemma text encoder under the **Gemma Terms of Use** (for
commercial use see <https://stability.ai/license>) — operator accepted these for local generation.
The recording bundle is royalty-free with no attribution required, but its agreement **expressly
prohibits using the sounds to train or enhance AI** and prohibits redistributing them other than
incorporated into a project. So the recorded files are mix material only: never a training,
fine-tuning or conditioning input, not even to this repo's own audio models. Full analysis in
`docs/licensing.md`.

## What is on disk
- Weights: `models/sound_effects/StableAudio3-Small-SFX` (repo-local index link → `/mnt/fast/models/stable-audio-3-small-sfx`),
  HF `stabilityai/stable-audio-3-small-sfx`, complete: DiT + SAME-S autoencoder + `t5gemma-b-b-ul2/`.
- Inference code: `external/stable-audio-3` @ `779434a` — git-ignored upstream checkout.
- Recording bundle: **outside the repo**, default `~/Music/sonniss_gdc_2026`, override with
  `CF_SONNISS_GDC_DIR`. 8.0 GB of 96/192 kHz WAV in 122 supplier packs, plus the tracklist
  spreadsheet the ingest reads provenance out of. Never committed, never fetched by a build.
- Design rationale, sources and measurements: `docs/research/2026-09-07-video-sfx-and-ambience-library.md`.

## Setup (once)
```bash
cd skills/audio/sfx && uv sync      # torch 2.7.1+cu126, transformers 5.16.1, soundfile 0.14.0
```

## Run

```bash
# the recorded half -> assets/sfx/ (~50 s; no GPU, no model load)
uv run --project skills/audio/sfx python skills/audio/sfx/ingest_recorded.py

# select and measure every excerpt, write nothing -- check a pick before committing FLAC to it
uv run --project skills/audio/sfx python skills/audio/sfx/ingest_recorded.py --analyze

uv run --project skills/audio/sfx python skills/audio/sfx/ingest_recorded.py --list
uv run --project skills/audio/sfx python skills/audio/sfx/ingest_recorded.py --only tick_light,ocean_waves_loop
```

```bash
# the generated half -> assets/sfx/ (~35 min on CPU for the full 38-sound recipe)
uv run --project skills/audio/sfx python skills/audio/sfx/build_library.py

# one or a few, e.g. after editing their prompt in library.json
uv run --project skills/audio/sfx python skills/audio/sfx/build_library.py --only pop_in,rain_light_loop

uv run --project skills/audio/sfx python skills/audio/sfx/build_library.py --list   # plan only
uv run --project skills/audio/sfx python skills/audio/sfx/build_library.py --device cuda

# one ad-hoc prompt, prints one JSON line (breeze-style)
echo "TrackType: SFX, a heavy iron gate closing in a stone courtyard." | \
  uv run --project skills/audio/sfx python skills/audio/sfx/run.py --duration 4 --seed 7
```

`--device cpu` is the default and is the right choice here: the small model needs no GPU
(~10 s for 30 s of audio on this box), and the 3090 is usually held by the HiDream server.

## Things that are not obvious

**Never ask the model for less than ~2.5 s.** Measured over three prompts x eight seeds, takes that
pass the hiss check: **0/8 at 0.8 s, 7/8 at 1.5 s, 8/8 at 2.5 s, 8/8 at 4.0 s.** Short requests are
out of distribution and come back as broadband noise. `build_library.py` therefore always requests
at least `MIN_GEN_S = 3.0` and gets the short files by *trimming to the event afterwards*. The
requested duration is a generation parameter; it is not the length of the finished file.

**Audition, do not trust one seed.** Every sound is generated over several seeds and the best take
is picked by an objective score; the winning seed is recorded in `manifest.json`, so the library
stays exactly reproducible. One-shots are scored on spectral flatness (a failed take is broadband
hiss: flatness 0.24-0.64, versus 0.000 for a good one), on HF tilt, and on how much of the energy
lands in the band the sound is supposed to occupy — a flawless sub rumble is still the wrong file
when the entry says "airy whoosh". Beds are scored on whether they survive repeating.

**`cfg_scale` and `negative_prompt` do nothing.** small-sfx is a *post-trained* checkpoint; upstream
documents both as base-model-only. Every constraint has to live in the positive prompt, and naming
an artefact in order to forbid it makes it more likely, not less — the prompts here are phrased
positively for that reason.

**Loops cannot click, by construction.** `loop_wrap` folds the tail back over the head, so the join
is a continuous span of the original generation rather than a splice; the function asserts the wrap
is sample-adjacent in the source. Every filter applied afterwards is zero-phase and *circular*
(rFFT), and level is set with a single constant scalar — no compressor, no limiter, no ffmpeg
`loudnorm` dynamic mode, all of which would move the seam. Tonal beds (`crossfade_law: "linear"`)
get an equal-gain fade instead of equal-power, because their head and tail are correlated.

**A long ambience is not uniformly loopable — so the window is measured, not guessed.** A 680 s
food-court recording holds one good 30 s loop and a lot of foreground chatter. `ingest_recorded.py`
sweeps the whole file on a 0.25 s block-RMS envelope, ranks every candidate start on the two things
that give a loop away (a distinct event inside it, a level mismatch across the wrap), then decodes
the shortlist, wraps each one for real and keeps the best-scoring — the same judgement
`build_library.score` makes between seeds, applied to offsets. `window_start_s` and
`windows_auditioned` in the manifest say where it landed and out of how many. The recorded beds
come out at ≤5.6 dB event prominence against ≤8.2 dB for the generated ones, which is the measured
version of "a real recording of a place beats a description of one".

**A sound that swells has to be gated at -60 dB, not -35 dB.** The event gate that finds a click
also ends the event where it falls back, and for a reverse sweep or a slow horn braam the quiet
approach *is* the sound. Two picks were being cropped mid-gesture until `gate_db` moved: a 16.5 s
braam peaks at 7.7 s, so a cap sized for a normal impact decapitates it. The `truncated` flag now
catches that class of mistake — it fires when `max_s` ends a sound while it is still above -30 dB
of its own peak, which is the difference between clipping a spent reverb tail and ending a sound
early.

**`noise_like` is a generated-side check and does not transfer.** High spectral flatness with a
rising HF tilt is the signature of a take that came back as broadband hiss instead of a designed
sound. A commercial recording cannot fail that way, and the measurements say so plainly: a bright
UI pop measures +35 dB of tilt, a corrupted-data glitch 0.78 flatness, and both are exactly the
sound their supplier named. Keeping the flag on this side would have meant ten per-entry overrides
saying "this one is allowed to be what it is" — a check that has stopped checking. Flatness and
tilt are still recorded as description; `truncated` and `clipped` took the flag's place, because
those are how an *excerpt* actually goes wrong.

**A ticking clock cannot be a bed.** The bundle holds a minute of antique clock and a ticking-clock
loop is one of the most-used documentary beds, so one was cut — and then dropped, because a
metronomic event inside a loop announces the wrap on every cycle. That is precisely what
`event_prominence_db` exists to catch, and the honest response to the flag was to drop the entry,
not to raise the threshold. The clock ships as `tick_light`, one tick out of the sixty the event
gate found.

**What QC does and does not tell you.** `harsh_band_ratio` is a cheap proxy for Zwicker sharpness,
not a measurement of it. `event_prominence_db` is the one to read before shipping a bed: it is the
loudest half-second over the median half-second, and a bed above ~10 dB holds something a viewer
will hear repeat. Flags are advisory — they say which files to listen to first, they do not fail
the build.

## Files
| file | role |
|---|---|
| `sfx.py` | shared DSP: offline model loading, loop wrap, filters, EBU R128 measurement, QC |
| `index.py` | shared writer: renders `manifest.json` + `README.md` over both halves |
| `recorded.json` | the recorded recipe: 38 sources, loop lengths, which event to take |
| `recorded.py` | source side: ffmpeg/soxr decode, provenance, window search, event gate |
| `ingest_recorded.py` | sweep → pick → level → write FLAC, and re-render the index |
| `library.json` | the generated recipe: prompts, seeds, durations, loop and level targets |
| `build_library.py` | audition → pick → level → write FLAC, and re-render the index |
| `run.py` | one ad-hoc prompt, one JSON line out |

Env overrides: `CF_SA3_REPO`, `CF_SA3_SFX_MODEL_PATH`, `CF_SONNISS_GDC_DIR`. Model loading sets
`HF_HUB_OFFLINE=1`; nothing in this skill reaches the network.
