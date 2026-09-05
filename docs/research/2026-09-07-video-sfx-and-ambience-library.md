# Video SFX + ambience library — what to generate, and how to make it loop

Retrieved 2026-09-07

Research behind `skills/audio/sfx` and the generated library in `assets/sfx/`. Every claim below is
either quoted from a page fetched on 2026-09-07 (URL given) or read directly out of the vendored
`external/stable-audio-3` checkout @ `779434a`. Sections marked **DECISION** are ours, not sourced.

---

## A. What a video actually needs

### A1. The categories editors reach for

- Whoosh / swoosh / swish is the single most-used transition category: "the most widely-used
  transition sound effect in films, YouTube videos, ads, and TV news reports, animation".
  Whooshes "mask cuts and add momentum".
  — https://www.tunepocket.com/transition-sound-effects/ ; https://dubbingai.io/articles/en/collection/cinematic-whoosh-transition-sfx
- Impacts (hit, slam, thud, boom, braam, stinger) are used to "button up a cut, emphasize a title,
  or add weight after a whoosh". Whoosh→impact is the canonical pair.
  — https://www.tunepocket.com/sound-effects-video-editing/
- Pop / bubble / plop → "playful confirmations, stickers, casual UI".
- Click / tap / toggle → UI interactions, "kept quiet and crisp".
- Ding / ping / success / error / alert → state changes and messages.
  — https://youtubesfx.com/free-sfx-pack/ ; https://videoeditingsfx.com/free-whoosh-transitions-sound-effects/
- Motion-graphics/explainer packs additionally ship "data transfer, telemetry, data scanning,
  screen text typing, notifications, calculations, and alerts" and "reveals, grows, and surprises".
  — https://motionarray.com/sound-effects/motion-graphics-sound-effects-pack-data-79355/ ; https://sonniss.com/sound-effects/explainer-video-sound-kit/

### A2. Explainer / motion-graphics usage rules

Fetched from https://www.motiontheagency.com/blog/sound-design-for-explainer-videos:

- "A button click has a small audio cue"; "A soft whoosh can make a transition feel more natural";
  "A subtle pop can make a visual element feel like it has landed in place".
- Restraint: "Sound effects do not need to be added to every movement, but key interactions and
  transitions usually benefit from subtle audio feedback."
- Balance: "The sound effects need to be present enough to support the animation, but not so loud
  that they become distracting." "If every movement has a loud or obvious sound effect, the final
  piece can feel cluttered."

### A3. Documentary usage — ambience, not effects

- Room tone is "the near-silent signature of a single interior recorded so editors can patch quiet
  moments"; ambience is "a continuous, non-specific bed that establishes a whole environment rather
  than a single event". — https://designingsound.org/2012/11/15/room-tone-emotional-tone-the-importance-of-hearing-ambience/
- The recurring inventory of ambient elements: "wind, water, birds, forest murmurs, electrical hum,
  room tone, office clatters, traffic, and neighborhood mutterings".
  — https://journals.sagepub.com/doi/full/10.1177/2057047317742171
- "Documentaries lean on ambience to transport viewers to real places. A cut to a new location often
  opens on a distinct ambient bed before any dialogue."
  — https://www.filmmakersacademy.com/glossary/ambient-sound/
- Layering: "start by establishing a base layer of room tone, then add secondary elements like
  one-shots to create depth". — https://filmlocal.com/filmmaking/master-ambient-sound/

**DECISION.** A library that serves both documentary and explainer work therefore needs two
families, not one: short **one-shots** (transitions / impacts / UI) and long **loopable beds**
(weather, place, room tone, drones). The library is split exactly that way.

---

## B. What makes a sound pleasant (and what to avoid)

Psychoacoustic metrics, fetched from
https://acoustic-research-consulting.co.uk/blog/post/psychoacoustics-sound-quality-introduction:

| Metric | Unit | Reference | Relevance |
|---|---|---|---|
| Loudness | sone | "equivalent to a 40dB 1kHz tone" | 1 sone = 40 phon |
| Sharpness | acum | "narrow-band noise (one critical band wide) at a centre frequency of 1kHz with a level of 60dB" | "sensory pleasantness decreases with an increase in Sharpness" |
| Roughness | asper | "60dB, 1kHz tone which is 100% modulated by a 70Hz modulator" | modulation **15–300 Hz**, peak sensitivity **70 Hz**; "sensory pleasantness tends to decrease with increasing roughness" |
| Fluctuation strength | vacil | "60dB, 1kHz tone which is 100% modulated by a 4Hz modulator" | peak at **4 Hz**, hands over to roughness around **20 Hz** |
| Tonality | prominence ratio | "For tones above 1kHz, the delta value must be above 9dB" | a prominent high tone reads as a whistle |

The unpleasantness model is four-term: **high sharpness, high roughness, low tonality, high
loudness**. Corroborating: "Pure tones with high tonal frequencies were perceived as the most
annoying, whereas intermittent and combined tones with lower tonal frequencies were perceived as
more pleasant." — https://sorama.eu/psychoacoustics-and-product-sound-quality/ (search snippet;
the page 404'd on direct fetch, so treat as **UNVERIFIED** and secondary) ;
https://mediatum.ub.tum.de/doc/1138438/document.pdf (Fastl, *Psychoacoustics and Sound Quality*)

**DECISION — what this buys us concretely:**

1. Prompt for warm, dark, soft, close-mic'd sources; never for "bright", "piercing", "metallic
   ring", "screech".
2. Keep amplitude modulation of beds slow (breath/gust rate, ~0.1–2 Hz) — well below the 15–300 Hz
   roughness window. Rain, wind, fire and crowd beds are naturally in the fluctuation-strength
   region, which is the pleasant side of the handover.
3. Prefer 2–3 note intervals over single pure tones for notification/success/error cues (low
   tonality is only *one* of the four unpleasantness terms; a prominent pure tone above 1 kHz is the
   thing to avoid).
4. Measure it. Every generated file gets a **harsh-band ratio** — energy in 2–5 kHz over total
   energy — plus spectral centroid, logged in the manifest. This is a cheap stand-in for sharpness
   (which weights high frequencies) and lets a harsh outlier be caught and regenerated rather than
   shipped. It is a *proxy*, not an acum measurement.

---

## C. Making a bed loop without anyone thinking twice

Fetched from https://www.frontiersoundfx.com/how-to-seamlessly-loop-any-ambience-audio-file/ —
the three-step "flip-flop" method:

1. Split the file at roughly ¼ from the end; that split point "becomes the starting and ending point
   of the finished audio file that guarantees seamless transition".
2. Move the first section to the right and overlap it with the second, finding "a point in the
   waveform where the transition between audio region 1 and region 2 have similar amplitude and
   tonal quality".
3. Crossfade at the boundary, adjusting "the length and curve of the cross-fade to find a transition
   that is unperceivable"; then consolidate.

Two hard numbers from the same page and the crossfade literature:

- **"A minimum 20 – 30 seconds for ambiences to avoid distinct sounds within the ambience from being
  noticeably repeated."** — frontiersoundfx (above)
- "A small crossfade (20–50 ms) is enough for most music; longer crossfades suit pads, drones and
  ambient textures." — https://10hourloop.com/blog/what-is-crossfade-audio-looping/
- Equal-power vs linear matters: "Using linear crossfade when equal power would sound better (or
  vice versa) can result in volume dips". — https://audioedit.io/blog/what-is-crossfade-audio-looping/
  (same claim at https://10hourloop.com/blog/what-is-crossfade-audio-looping/)
- The failure mode to design against: "if the waveform at the end of your loop doesn't line up with
  the start, you get an audible click or thump every time it repeats".
  — https://gamedevbeginner.com/create-looping-sound-effects-for-games-for-free-with-audacity/
- The strictest join is at zero crossings on both sides.
  — https://exceed7.com/tiny-ambience/advanced/seamless-loop.html

**DECISION — the wrap we implement** (`skills/audio/sfx/sfx.py::loop_wrap`), which is the
flip-flop method expressed as arithmetic so it needs no ear-driven splice point:

Generate `lead + L + C + tail_pad` seconds, discard `lead` from the front (the model habitually
fades in) and `tail_pad` from the back (it habitually fades out). From the remaining `x` of length
`L + C`:

```
head = x[0 : C]      body = x[C : L]      tail = x[L : L+C]
out  = concat( tail·cos(½πt) + head·sin(½πt) ,  body )        t : 0→1 over C
```

`out` is exactly `L` long. Its last sample is `x[L-1]` and its first is dominated by `tail[0] =
x[L]`, i.e. the wrap point is a *continuous span of the original generation* — there is no splice,
so there is nothing to click. `cos/sin` is the equal-power law, which is the correct choice because
ambience beds are noise-like and therefore uncorrelated across the fade; equal-power holds RMS flat
where a linear fade would dip ~3 dB in the middle.

`L ≥ 20 s` and `C = 2–5 s` per the two sourced numbers above (ambient textures get the long fade,
not the 20–50 ms music fade). Beds built round a recurring event get the longest `L`: `ocean_waves`
is 40 s because a wave set is audible as an event, and thunder is not in the storm bed at all — it
ships as the `thunder_distant` one-shot, because a clap baked into a loop announces the repeat on
every cycle (measured `event_prominence_db` 11.9 when it was inside).

---

## D. Level targets

- General video guidance: "keep dialogue between −6 dB and −12 dB, background music between −20 dB
  and −30 dB whenever someone is speaking, and sound effects between −10 dB and −20 dB."
  — https://mytasker.com/blog/the-complete-guide-to-sound-design-for-video-creators
- "SFX tends to be secondary to dialogue within the sound mix."
  — https://krotos.studio/blog/how-to-balance-music-and-sound-effects
- Programme targets: −14 LUFS for YouTube, −16 LUFS streaming/podcast, −23/−24 LUFS broadcast,
  Netflix −27 LUFS integrated / −2 dBTP.
  — https://youlean.co/loudness-standards-full-comparison-table/ ; https://www.izotope.com/en/learn/the-mixers-guide-to-loudness-for-broadcast.html

**DECISION.** The repo already masters programme audio to −14 LUFS / −1 dBTP (STATUS phase 3). A
source library must land *below* that so an editor adds gain rather than fighting clipping:

| Family | Target | Ceiling |
|---|---|---|
| One-shots (transition, impact, UI) | max-momentary **−16 LUFS** | −3 dBTP |
| Beds (weather, place, room tone, drone) | integrated **−23 LUFS** | −3 dBTP |

That is a ~7 LU gap, which reproduces the "SFX −10..−20 dB / beds −20..−30 dB under speech" split
above. Integrated LUFS is meaningless for a 0.5 s pop (BS.1770 gating needs ≥400 ms blocks and
discards quiet ones), hence max-momentary for one-shots.

**Gain is applied as a single constant scalar, never with a compressor or limiter.** ffmpeg
`loudnorm` in its default dynamic mode would apply time-varying gain and destroy the loop seam that
section C just constructed. We measure with `ebur128`, compute one number, multiply, and re-measure.

---

## E. Model constraints (`stable-audio-3` @ `779434a`, `small-sfx`)

Read out of the checkout and its docs, not from a search:

- 433M params, SAME-S autoencoder, **44.1 kHz stereo**, max length **120 s**, and — per the model
  table — SFX-only: `small-sfx` is marked "—" for Music and Stems, "✓" for Samples/SFX.
  — `external/stable-audio-3/README.md`, `docs/guides/prompting.md`
- `small-sfx` is a **post-trained** checkpoint. `docs/workflows/inference.md`: `cfg_scale` and
  `negative_prompt` are listed under "**Base models only** … these parameters have no effect on
  post-trained checkpoints." **Everything must be steered from the positive prompt** — there is no
  negative prompt to lean on. Defaults `steps=8`, `cfg_scale=1.0`; "going higher than 8 doesn't
  necessarily increase quality".
- Prompt recipe for SFX (`docs/guides/prompting.md`): prefix `TrackType: SFX`; then state **the core
  source** ("exactly what object, instrument, or synthesizer is making the sound"), **the action**
  ("how is the sound being triggered, and how long does it last"), and **the
  production/characteristics** ("where is the mic placed, and what's the room character? How is the
  sound processed?"). "Set a short duration. Most sound effects are brief."
- Trained on AudioSparx + CC-0/CC-BY/CC-Sampling+ Freesound; prompts phrased like library metadata
  land better than poetic ones. — model card `README.md`
- Licence: **Stability AI Community License** for the weights; the T5Gemma text encoder is
  redistributed under the **Gemma Terms of Use**. Commercial use is governed by
  https://stability.ai/license. Inference code is MIT (`external/stable-audio-3/LICENSE`).
- Loading offline: `StableAudioModel.from_pretrained("small-sfx")` resolves through
  `hf_hub_download`. `T5GemmaConditioner.__init__` takes `model_path` which "takes precedence over
  `repo_id`", so rewriting `model_config["model"]["conditioning"]["configs"][0]["config"]` to
  `{"model_path": <local t5gemma dir>}` and calling `load_diffusion_cond()` directly loads the whole
  thing from `models/sound_effects/StableAudio3-Small-SFX` **with no network access**.
  — `stable_audio_3/models/conditioners.py:157-205`, `stable_audio_3/loading_utils.py:60`

---

---

## E2. Measured on this box, not read anywhere — small-sfx behaviour

These are our own measurements (RTX 3090 box, CPU inference, `steps=8`), run because the first build
of the library produced unusable one-shots. They are the load-bearing facts behind
`skills/audio/sfx/build_library.py`.

### E2.1 Short requests are out of distribution and return broadband hiss

Three prompts (a keycap click, a wooden plop, a marimba chime) x eight seeds x four requested
durations. "Pass" = spectral flatness ≤ 0.15 **and** HF tilt ≤ 0 dB after trimming (see E2.2).

| requested duration | click | plop | chime |
|---|---|---|---|
| 0.8 s | **0/8** | **0/8** | **0/8** |
| 1.5 s | 7/8 | 7/8 | **0/8** |
| 2.5 s | 8/8 | 8/8 | 8/8 |
| 4.0 s | 8/8 | 8/8 | 8/8 |

Flatness at 0.8 s clusters at 0.25–0.54; at 2.5 s it is 0.00–0.01. The failure is not gradual and it
is not seed luck — at 0.8 s *every* take across three prompts is noise.

A duration sweep at a fixed prompt and seed (thud, seed 1007) alternates good/bad through the
transition zone — 1.0 good, 1.2 bad, 1.5 good, 1.7 bad, 2.0 good, 3.0 good — which is why the first
diagnosis ("generation variance") looked right and was wrong. The variance is real but it is
confined to the 1–2 s band; below ~1 s the model simply does not produce a designed sound.

**Consequence.** The requested duration is a *generation* parameter, not the output length. Always
ask for ≥ 3 s and cut the short file out of the result afterwards. This also explains a second
observed habit: the model routinely places the event late in the requested window (a 1.5 s thud
peaking at 1.38 s), so the trim has to find the onset rather than gate on silence — a −60 dB gate
treats the model's low-level pre-roll as signal and leaves half a second of dead air in front of a
cue that has to land on a frame. `trim_oneshot` uses −35 dB relative to peak for the head and
−60 dB for the tail.

### E2.2 Spectral flatness separates a designed sound from a failed take

Wiener entropy (geometric over arithmetic mean of the power spectrum, 50 Hz–16 kHz), measured on the
first build:

| file | flatness | HF tilt | what it is |
|---|---|---|---|
| `impact_cinematic` | 0.0000 | −51.3 dB | a real boom |
| `whoosh_deep` | 0.0000 | −59.9 dB | a real whoosh |
| `pop_in` | 0.0000 | −15.3 dB | a real pop |
| `impact_soft` | 0.6426 | +8.6 dB | hiss |
| `success_chime` | 0.5530 | +6.1 dB | hiss |
| `click_soft` | 0.4161 | +4.8 dB | hiss |
| `rain_light_loop` | 0.2813 | 0.0 dB | **legitimately** noise-like |

The separation is total for one-shots and meaningless for beds — rain *is* noise. The test is
therefore applied to one-shots only, at flatness > 0.15 or tilt > 0.

### E2.3 Negations

With no CFG there is nothing to steer away from a negative clause. At matched (above-threshold)
durations the negation-carrying click prompt gave flatness 0.18–0.34 where the positively phrased
one gave 0.01. On a thud the two phrasings were indistinguishable (−20.6 vs −20.9 dB tilt, same
seed and duration), so this is not a universal effect — but there is no upside to naming an
artefact, and every prompt in `library.json` is phrased positively.

### E2.4 The seam does not need a click test

`loop_wrap` output satisfies `out[0] == x[L]` and `out[-1] == x[L-1]`: adjacent samples of the
source. There is no splice, so there is no click, and the function asserts this rather than
measuring it. This matters because a *statistical* seam test cannot tell a splice artefact from a
real transient that happens to sit at the join: on the first build a broadband-transient test
flagged `forest_day_loop` at +14.7 dB, which turned out to be a loud bird at 26.8 s of 28 s — real
content, correctly reproduced. The metric that survived is `event_prominence_db` (loudest
half-second over the median half-second), which is what actually predicts a loop giving itself away.

Two failures the wrap *can* produce, both retained as QC:
- a **level mismatch** across the wrap, measured over 2 s either side (200 ms was too short a window
  to be stable on a bass-heavy bed);
- a **crossfade gain error** on correlated material. `sub_bed_loop` — a sustained tone, so head and
  tail are phase-related — showed a 6.1 dB jump under the equal-power law. Sustained tonal beds get
  `crossfade_law: "linear"` (equal gain) instead; equal power is correct only for the uncorrelated,
  noise-like material that every other bed here is made of.

---

## F. Sources

- https://www.tunepocket.com/transition-sound-effects/
- https://www.tunepocket.com/sound-effects-video-editing/
- https://dubbingai.io/articles/en/collection/cinematic-whoosh-transition-sfx
- https://videoeditingsfx.com/free-whoosh-transitions-sound-effects/
- https://youtubesfx.com/free-sfx-pack/
- https://www.motiontheagency.com/blog/sound-design-for-explainer-videos
- https://sonniss.com/sound-effects/explainer-video-sound-kit/
- https://motionarray.com/sound-effects/motion-graphics-sound-effects-pack-data-79355/
- https://designingsound.org/2012/11/15/room-tone-emotional-tone-the-importance-of-hearing-ambience/
- https://journals.sagepub.com/doi/full/10.1177/2057047317742171
- https://www.filmmakersacademy.com/glossary/ambient-sound/
- https://filmlocal.com/filmmaking/master-ambient-sound/
- https://acoustic-research-consulting.co.uk/blog/post/psychoacoustics-sound-quality-introduction
- https://mediatum.ub.tum.de/doc/1138438/document.pdf
- https://sorama.eu/psychoacoustics-and-product-sound-quality/ (404 on fetch — snippet only)
- https://www.frontiersoundfx.com/how-to-seamlessly-loop-any-ambience-audio-file/
- https://10hourloop.com/blog/what-is-crossfade-audio-looping/
- https://audioedit.io/blog/how-to-loop-audio-seamlessly
- https://gamedevbeginner.com/create-looping-sound-effects-for-games-for-free-with-audacity/
- https://exceed7.com/tiny-ambience/advanced/seamless-loop.html
- https://mytasker.com/blog/the-complete-guide-to-sound-design-for-video-creators
- https://krotos.studio/blog/how-to-balance-music-and-sound-effects
- https://youlean.co/loudness-standards-full-comparison-table/
- https://www.izotope.com/en/learn/the-mixers-guide-to-loudness-for-broadcast.html
