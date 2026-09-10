# SFX skill — the video sound-effect and ambience library

Builds `assets/sfx/`: **670 sounds** for video work, split into short one-shots (transitions,
impacts, UI/motion-graphics cues, animals, human foley, instruments, vehicles) and long seamless
ambience beds (weather, place, room tone, drones). Three builders fill it and they share
everything downstream of the source:

| builder | recipe | fills | source |
|---|---|---|---|
| `ingest_recorded.py` | `recorded.json` | 346 sounds | the #GameAudioGDC bundle held locally |
| `ingest_packs.py` | `mixkit.json` | 294 sounds | individually downloaded files, **Mixkit Sound Effects Free License** |
| `ingest_packs.py` | `local_renders.json` | 19 sounds | rendered locally by the operator before ingest; no recipe, no licence |
| `build_library.py` | `library.json` | 11 sounds | generated with **Stable Audio 3 Small-SFX** (433M params, 44.1 kHz, SFX-only) |

Sounds from any origin are interchangeable in a mix: same 44.1 kHz stereo 24-bit FLAC, same loop
wrap, same two loudness targets, same QC fields. Any builder re-renders the whole manifest and
README through `index.py`, so a partial rebuild leaves the index describing the whole library —
and `build_library.py` needs no bundle, the two ingests need no GPU or model.

Recording beats generation wherever a source holds the sound, which is why only 11 of the 670 are
generated. No source holds everything: there is still no office room tone in the bundle, so that
one stays generated rather than be filled with something that is nearly the right sound.

**The three cut modes**, shared by both ingests and chosen per entry in the recipe:

| mode | what it does | when |
|---|---|---|
| `whole` | take the file as delivered: trim the silence, optionally cap, high-pass, level | the source is already one cut sound — a stock one-shot, a supplier's sampler file. 495 of the 659 ingested sounds |
| `loop` | sweep the whole recording for the evenest window, wrap the tail over the head | a long ambience, where a window is still to be chosen. 144 sounds |
| `oneshot` | gate the file into events and take one | a take that warms up before the hit, or a minute of clock holding sixty ticks. 20 sounds |

`whole` is the default for pack files and for most of the bundle because the supplier already
decided where the sound starts and stops: an event gate over that decision throws away the edit
being licensed, and crops the quiet approach of anything that swells. Two options tune it —
`bed: true` levels a long continuous texture to the bed target instead of the one-shot target, and
`trim: false` keeps the delivered length exactly, which matters for a musical render whose silence
is part of its bar count.

The control plane never imports this code, exactly like `skills/audio/breeze`.

**Licences.** Inference code (`external/stable-audio-3`, MIT); weights under the **Stability AI
Community License** and the bundled T5Gemma text encoder under the **Gemma Terms of Use** (for
commercial use see <https://stability.ai/license>) — operator accepted these for local generation.
The recording bundle is royalty-free with no attribution required, but its agreement **expressly
prohibits using the sounds to train or enhance AI** and prohibits redistributing them other than
incorporated into a project. The Mixkit pack is free for commercial use with no attribution
clause, and forbids redistributing an Item on its own or aggregating Items "on a stock or
inventory basis" — which is what a published copy of `assets/sfx` would be. Mixkit says nothing
about AI, and that is not read as permission: the halves sit in one directory and are deliberately
interchangeable, so **the strictest term governs the whole library**. Every file here is mix
material only: never a training, fine-tuning or conditioning input, not even to this repo's own
audio models. Full analysis in `docs/licensing.md` and
`docs/research/2026-09-09-mixkit-sfx-pack-licence.md`.

## What is on disk
- Weights: `models/sound_effects/StableAudio3-Small-SFX` (repo-local index link → `/mnt/fast/models/stable-audio-3-small-sfx`),
  HF `stabilityai/stable-audio-3-small-sfx`, complete: DiT + SAME-S autoencoder + `t5gemma-b-b-ul2/`.
- Inference code: `external/stable-audio-3` @ `779434a` — git-ignored upstream checkout.
- Recording bundle: **outside the repo**, default `~/Music/sonniss_gdc_2026`, override with
  `CF_SONNISS_GDC_DIR`. 8.0 GB of 96/192 kHz WAV — 347 files in 122 supplier packs, 3.6 hours —
  plus the tracklist spreadsheet the ingest reads provenance out of. Never committed, never
  fetched by a build. **Its directory layout is load-bearing**: `src.pack` in the recipe is the
  supplier-and-library directory name, so the bundle must stay exactly as it was extracted. It is
  not sorted into library categories and must not be — that would break all 346 recipe entries
  and throw away the attribution the directory names carry.
- Pack roots: **outside the repo**, `/mnt/fast/sound-libraries/mixkit` (0.7 GB, override
  `CF_MIXKIT_SFX_DIR`) and `/mnt/fast/sound-libraries/local-renders` (14 MB, override
  `CF_LOCAL_SFX_DIR`). One directory per library category, a `SOURCES.sha256` beside them, and
  `_duplicates/` holding downloads that turned out to be copies. `stage_pack.py` puts them there.
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
# sort a fresh download into the pack root by category (idempotent; --dry-run explains)
uv run --project skills/audio/sfx python skills/audio/sfx/stage_pack.py \
    --recipe skills/audio/sfx/mixkit.json --from ~/Downloads --dry-run

# the Mixkit pack -> assets/sfx/ (~4 min; no GPU, no model load)
uv run --project skills/audio/sfx python skills/audio/sfx/ingest_packs.py \
    --recipe skills/audio/sfx/mixkit.json
uv run --project skills/audio/sfx python skills/audio/sfx/ingest_packs.py \
    --recipe skills/audio/sfx/local_renders.json

# same --analyze / --list / --only flags as the recorded ingest
uv run --project skills/audio/sfx python skills/audio/sfx/ingest_packs.py \
    --recipe skills/audio/sfx/mixkit.json --analyze --only rain_thunder_loop
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

**Most of the bundle is already cut, which is why `whole` exists.** The first 38 excerpts were
taken with the window sweep and the event gate, and the impression that stuck was "a 680 s
food-court recording". That is the exception: measured across all 347 files, 199 are under eight
seconds and the median pack is three sampler files totalling under a minute. For those the
supplier has already made the edit, and `whole` keeps it. Only 90 of the 346 recorded entries
still need a window swept, and 20 need an event gated.

**Categories come from the filename where the supplier set one.** Three quarters of the bundle is
named to the Universal Category System — `ANMLDog_`, `AMBTran_`, `WEAPSwrd_`, `VOXMale_`,
`CREAMnstr_` — so the mapping onto this library's categories is a documented translation
(`ANML`→animal, `AMB`→place, `CREA`→creature, `MAG`→magic, `DSGN`/`ROBT`→scifi,
`VEH`/`TRN`/`AERO`→vehicle, `VOX` split between voice and human by whether there are words in it)
rather than 308 separate judgements. The quarter that uses supplier-specific naming was classified
by hand.

**One file is deliberately not ingested.** `Camp fire, ... _B-format, Ambix.wav` is four-channel
ambisonic with an unknown channel layout; `-ac 2` would fold the omni W against the X/Y/Z
gradients rather than decode it, and this pipeline has no ambisonic decoder. The other four
multichannel sources are quad, 5.1 and 7.1.2 — layouts ffmpeg has a real downmix matrix for — and
those are ingested.

**A ticking clock cannot be a bed.** The bundle holds a minute of antique clock and a ticking-clock
loop is one of the most-used documentary beds, so one was cut — and then dropped, because a
metronomic event inside a loop announces the wrap on every cycle. That is precisely what
`event_prominence_db` exists to catch, and the honest response to the flag was to drop the entry,
not to raise the threshold. The clock ships as `tick_light`, one tick out of the sixty the event
gate found.

**A pack can ship one recording under two names.** Mixkit items 2390 and 2401 are the same
storm: 2401 is 2390 trimmed by one second at the head. Both window sweeps landed on the same span,
so the library would have carried one sound under two ids, offering a choice it did not have. The
byte-identical downloads a browser leaves behind (`x(1).wav`) are caught by `stage_pack.py`, which
compares hashes rather than trusting the filename; this one was not byte-identical and was found
by anchoring a fingerprint on the peak sample, which survives any head or tail trim. `index.write`
now reports entries with matching FLAC sha256 on every build, so the next one is caught by the
builder rather than by hand.

**`harsh_band` fires often on a pack and that is not a defect.** 49 of the 294 Mixkit sounds trip
it, against 2 of the 49 in the original library — because a dawn chorus, a kiss and a cartoon
monkey genuinely put most of their energy in the harsh band. It is the same argument that removed
`noise_like` from the recorded side, one threshold short of the same conclusion: the flag is kept
because "listen to this before you put it under narration" is still useful advice, but it is
description, not failure.

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
| `recorded.json` | the bundle recipe: 346 sources, cut mode, loop lengths, which event to take |
| `recorded.py` | source side: ffmpeg/soxr decode, provenance, window search, event gate, pack roots |
| `ingest_recorded.py` | sweep → pick → level → write FLAC, and re-render the index |
| `mixkit.json` | the Mixkit recipe: 294 files, one licence, per-sound mode/category/tags/use |
| `local_renders.json` | the operator's 19 local renders, described from measurement |
| `ingest_packs.py` | whole-file or loop-window ingest of a pack, and re-render the index |
| `stage_pack.py` | sort a download into the pack root by category; sha256-verified, idempotent |
| `library.json` | the generated recipe: prompts, seeds, durations, loop and level targets |
| `build_library.py` | audition → pick → level → write FLAC, and re-render the index |
| `run.py` | one ad-hoc prompt, one JSON line out |

Env overrides: `CF_SA3_REPO`, `CF_SA3_SFX_MODEL_PATH`, `CF_SONNISS_GDC_DIR`,
`CF_MIXKIT_SFX_DIR`, `CF_LOCAL_SFX_DIR`. Model loading sets
`HF_HUB_OFFLINE=1`; nothing in this skill reaches the network.
