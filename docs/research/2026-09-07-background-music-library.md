# Background music for video — what to write, and how to keep it out of the way

Retrieved 2026-09-07

Research behind `skills/audio/music` and the generated library in `assets/music/`. Every claim is
either quoted from a page fetched on 2026-09-07 (URL given) or read directly out of the vendored
`external/HOT-Step-CPP` checkout. Sections marked **DECISION** are ours, not sourced.

Companion to `docs/research/2026-09-07-video-sfx-and-ambience-library.md`; the level, loop and QC
machinery is shared with it and is not re-derived here.

---

## A. Underscore is a genre, and it has rules

The operator's brief — non-verbal, subtle, "should not take over" — is the textbook definition of
**underscore**, which is a distinct craft from writing a track.

- "A background instrumental composition designed to accompany visuals, dialogue, or narration",
  "woven beneath the primary audio elements to underline mood and emotion". The composer's job is
  "to stay invisible yet indispensable".
  — https://www.soundverse.ai/blog/article/what-is-an-underscore-in-music-1325
- Instrumentation: "soft piano, strings, synths, and electronic textures for emotional layering";
  "**Pads, drones, and ambient layers are used to create mood without rhythmic interference**".
- Harmony: "**Simple chord progressions maintain focus on dialogue**."
- Melody: "**Short motifs evolve gently** throughout scenes for thematic continuity" — motifs, not
  tunes. A hummable melody competes with speech for the listener's attention.
- Mixing: "Volume automation and frequency shaping ensure that the underscore remains behind
  speech."
- In documentary specifically: music "must remain authentic and unobtrusive"; "the monologue should
  remain in the foreground"; "a too busy or dense soundtrack distracts from the essentials"; the
  underscore is "subtle and quiet, hardly noticeable, utilizing pads and sustains".
  — https://cloud.smartsound.com/blog/how-to-use-underscore-in-a-video/ ; https://c-istudios.com/creating-a-high-impact-score-for-documentaries/
- And it has to move with the film: the score must "complement the visuals without overwhelming key
  dialogue or narration, requiring composers to meticulously time musical cues and adjust their
  compositions to match the documentary's pacing".
  — https://c-istudios.com/creating-a-high-impact-score-for-documentaries/ ; https://www.documentary.org/feature/composers-confab-creating-best-score-your-film

**DECISION.** Every caption in `library.json` states *fully instrumental*, keeps harmony to slow
simple changes, and asks for texture-led writing with motifs rather than melodies. Percussion is
either absent or reduced to a soft pulse. This is not timidity — it is the brief.

## B. What the different video types actually want

- **Documentary**: "ambient, atmospheric music with subtle tension builds and minimal percussion
  that breathes with the footage"; documentaries need "reflective moments, determined moments,
  heavy moments, exuberant moments, and everything in between".
  — https://www.tunepocket.com/music-for-documentary/ ; https://lensdistortions.com/background-music-documentaries/
- **Explainer**: "uncomplicated, nondistracting music with light, upbeat tempo to hold viewer
  attention without overpowering the voiceover".
  — https://www.tunepocket.com/music-for-explainer-video/ ; https://www.trackclub.com/resources/music-for-explainer-videos
- **Corporate/promo**: "emotional corporate music with inspirational uplifting feel"; "positive
  corporate ... uplifting comforting".
  — https://www.motionelements.com/blog/articles/making-the-right-choice-of-music-that-defines-your-brand-in-corporate-videos
- **Science/technical explainer**: "orchestral background music creates soft tension and feelings of
  suspense, perfect for science explainers and documentaries".
  — https://www.hooksounds.com/blog/background-music-every-style/
- **Reflective/observational**: "calm electronica with cinematic piano and strings for reflective
  video and documentary background". — https://www.tunepocket.com/music-for-documentary/

**DECISION.** The library covers those as named categories rather than as one undifferentiated
"background music" pile, and each entry's `use` field says which cut it is for.

## C. Leaving room for the voice — the measurable part

- The voice occupies "low-mid to mid-range frequencies (250 Hz to 5 kHz)", with the presence region
  around 3 kHz; the standard fix is to "carve out a space for the voiceover" with "a narrow band (Q)
  to gently reduce the frequencies where the voiceover sits".
  — https://recordmixandmaster.com/2024-05-how-to-mix-voiceovers-and-background-music
- Ducking "automatically lowers a background track — usually music — whenever a priority signal,
  usually a voice, is active"; the recipe is "set the base levels, sidechain the bed to the dialog,
  choose an amount the ear cannot catch, **carve the presence region**, and check the meter".
  — https://www.skrrol.ai/blog/audio-ducking-explained
- Levels when someone is speaking: music **−20 to −30 dB**, dialogue −6 to −12 dB.
  — https://mytasker.com/blog/the-complete-guide-to-sound-design-for-video-creators

**DECISION — this is a QC metric, not just advice.** Every generated track is measured for
`presence_band_ratio`: the share of total energy between **1 kHz and 4 kHz**, the region a narrator
actually competes for. A track that is hot there will fight the voice however carefully it is
ducked, so it loses to a quieter-in-that-band take during candidate selection. It is a *ratio*, so
it says something normalisation cannot fix — turning the track down moves the whole spectrum, not
the overlap.

The repo already ducks music under speech (sidechain in `add_music_bed`, STATUS phase 3), so the
library's job is to arrive already suited to being ducked, not to arrive pre-ducked.

## D. "Quicker then slower" — arcs are a first-class category

The operator explicitly asked for documentary music that "becomes quicker then slower and so on",
which is the pacing point in A above. MM3's Structured Caption has a section-by-section
**Arrangement** field (E below) that is designed for exactly this, so arcs are written into the
caption rather than edited in afterwards.

**DECISION — and it is measurable.** EBU R128 **loudness range (LRA)** is the natural check:
a flat texture and a track that builds and recedes are different by construction, so the library
declares an expected LRA band per entry and QC flags a track that did not move (or moved too much
for a bed). Calm textures want a low LRA; arc tracks want a visibly higher one.

---

## E. The model, and how to drive it (read out of the checkout, not from a search)

`models/music/MiniMax-Music3-GGUF` is a five-file GGUF conversion of `MiniMaxAI/MiniMax-Music3`:
LM 8.59B (Qwen3 arch, 200k vocab incl. 16,384 semantic audio codes) → RVQ depth decoder 0.6B →
condition encoder 25M → flow-matching DiT 2.4B → vocoder 54M. Output 44.1 kHz stereo, up to 5
minutes. — `models/music/MiniMax-Music3-GGUF/README.md`

- **The weights are not usable with llama.cpp or ComfyUI-GGUF.** The model card is explicit: "They
  are **not** usable with llama.cpp alone: `mm3-lm-*.gguf` is structurally a Qwen3 GGUF, but music
  generation requires the full five-module pipeline ... implemented in HOT-Step's engine."
  STATUS agreed — "Music3 has no runtime yet".
- **The runtime is [HOT-Step CPP](https://github.com/scragnog/HOT-Step-CPP)**, engine MIT-licensed.
  Despite the project reading as a desktop app, the engine builds headless CLI/server binaries; MM3
  generation lives in the **server** (`tools/hot-step-server.cpp` → `src/minimax/`), reached over
  HTTP at `POST /mm3/synth` + `GET /mm3/job`. `ace-lm`/`ace-synth` are the ACE-Step tools and
  contain no MM3 code at all — pointing them at these weights only prints
  "unknown architecture", because the ACE registry classifies on `general.architecture` and MM3 is
  discovered separately (`<models>/mm3/*.gguf`, falling back to a flat `<models>/mm3-*.gguf`).
  — `engine/src/minimax/mm3-model.h`, `engine/src/model-registry.h:100`
- **Request fields** (`engine/src/minimax/mm3-request.h:543`, `mm3-pipeline.h:625`): `caption`,
  `lyrics`, `instrumental`, `duration`, `seed`, `steps`, `cfg_flow`, `wav_bits`, `takes`,
  `lm_temperature`, `flow_uncond_interval`, `scheduler`, `infer_method`. Frame rate is 25 fps and
  `max_frames` is capped at 9000, i.e. **360 s**.
- **`instrumental: true` is a real mechanism, not a hint.** It substitutes the literal
  `[instrumental]` structure tag for the lyrics in the assembled prompt and disables LRC alignment
  capture. — `engine/src/minimax/mm3-align.h:228`, `mm3-job.h:919`
- **Structured Caption** — MiniMax's own format, "exactly three required top-level sections in this
  specific order": **Global Metadata** (genre, subgenres, tempo as a range or qualitative
  description, emotional progression, production profile), **Vocal Details** (for an instrumental:
  "state that the piece is instrumental and identify the instrument or texture carrying the lead
  melodic role"), **Arrangement** (a section-by-section timeline of instrument lifecycles, groove
  development and transitions). The spec warns: "Do not invent a precise key, BPM, vocal gender,
  melodic interval, or production technique when a broader description is sufficient."
  — https://github.com/MiniMax-AI/MiniMax-Music3/blob/main/skills/music-caption-rewriter/SKILL.md
- Third-party prompting guidance agrees on shape: "Genre + mood + instruments + dynamic direction",
  and gives an underscore example worth copying — "begin with felt piano and low room tone,
  introduce warm cellos after the first phrase, add a restrained electronic pulse at the midpoint,
  then rise into a broad but not bombastic final minute before ending on solo piano".
  — https://www.minimax-music.com/minimax-music-3-prompt-guide ; https://www.ambienceai.com/tutorials/minimax-music-prompting-guide
- **Licence:** MiniMax-Music3 Community License. Notable terms: prominent display of
  "MiniMax-Music3" in commercial products, separate authorization above US$20M annual revenue, an
  acceptable-use policy, and **clear disclosure of AI generation for publicly distributed outputs**.
  The GGUF conversion adds no further restrictions; the HOT-Step engine is MIT.

---

## G. Measured on this box — MM3 does not take a duration

Our own measurements (RTX 3090, CUDA build of the HOT-Step engine, `steps=30`, `cfg_flow=1.7`),
run because the first build produced 17-second "two-minute" tracks.

### G1. `duration` is a cap, not a target

`mm3_assemble_prompt()` builds the LM prompt from **`caption` + `lyrics` only**
(`engine/src/minimax/mm3-request.h:527`). `duration` becomes `max_frames`, which is nothing but the
AR loop's stopping bound — the model is **never conditioned on how long the piece should be**. It
generates until it emits EOS, and the request only decides when to give up waiting.

Asking for 120–150 s, measured output:

| caption style | seeds | output as % of the cap |
|---|---|---|
| "One continuous section…" | 2 | 24 %, 28 % |
| Timed sections `[0:00-0:30] … [1:30-2:00]` | 2 | 37 %, 30 % |
| Named sections, no times | 2 | 43 %, 19 % |
| Eight timed sections | 3 | 47 %, 19 %, 22 % |
| Eight timed sections **+ "runs for a full two minutes"** | 3 | 17 %, 15 %, 29 % |

A sectioned Arrangement helps a little and is the format MiniMax specifies anyway. **Stating the
intended length in the caption makes it worse**, consistently. Absolute lengths across all the
underscore captions we tried land in a wide, noisy band of roughly **19–62 s**.

### G2. No sampler setting moves it

Same caption, same two seeds, 130 s cap:

| setting | outputs |
|---|---|
| defaults | 18.8 s, 62.2 s |
| `lm_rep_penalty` 1.15 | 18.8 s, 62.2 s — **bit-identical**, the penalty never touches the EOS token |
| `lm_temperature` 1.2 | 29.6 s, 22.9 s |
| `lm_rep_penalty` 1.3 + `lm_temperature` 1.1 | 31.6 s, 37.2 s |

Temperature only changes which draw you get. There is no EOS-suppression or minimum-length control
anywhere in the engine (`mm3-ar-loop.h` tracks `eos_hit`; nothing overrides it).

**DECISION.** Length is the one thing that cannot be requested, so it is what candidate selection
optimises: each entry is rendered from several seeds and the **longest** take that passes QC wins,
with the other metrics as tie-breakers. Entries declare `min_length_s` and a track that misses it is
flagged `short` rather than quietly shipped. Loop entries turn the problem into a non-problem —
a wrapped pad is as long as you want — which is why the static material is the loopable material.

### G3. LRA is the wrong arc metric for sparse music

The obvious check for "does this track travel" is EBU **LRA**. It does not work here: a felt-piano
bed with long gaps of near silence between chords measured **LRA 25.7 LU** while being completely
even in level. LRA is reading the gaps, which is what LRA is for.

Replaced by `slow_envelope_range_db` — p95 − p5 of a **10-second moving RMS**. Smoothing over ten
seconds discards the gaps and leaves the shape, so a bed that holds one level reads near zero
however sparse it is, and one that builds and recedes reads high. LRA is still recorded in the
manifest; the flag is on the slow envelope.

### G4. A loop needs somewhere to slide

Wrapping the *longest* loop a take can support leaves exactly one possible window, and the first
static-pad loop came out with a **7.1 dB level jump** across the wrap because the pad swells over
its length. Reserving ~8 s of slack and searching the offsets for the most level-stable window
(`mm3.best_loop_window`) took the same source material to a clean seam at a shorter length. For a
pad, a shorter loop with an inaudible join beats a longer one that pumps once per cycle.

---

## F. Sources

- https://www.soundverse.ai/blog/article/what-is-an-underscore-in-music-1325
- https://cloud.smartsound.com/blog/how-to-use-underscore-in-a-video/
- https://c-istudios.com/creating-a-high-impact-score-for-documentaries/
- https://www.documentary.org/feature/composers-confab-creating-best-score-your-film
- https://variety.com/2009/film/markets-festivals/documentary-scoring-requires-fine-balance-1118007649
- https://www.tunepocket.com/music-for-documentary/
- https://www.tunepocket.com/music-for-explainer-video/
- https://lensdistortions.com/background-music-documentaries/
- https://www.trackclub.com/resources/music-for-explainer-videos
- https://www.hooksounds.com/blog/background-music-every-style/
- https://www.motionelements.com/blog/articles/making-the-right-choice-of-music-that-defines-your-brand-in-corporate-videos
- https://recordmixandmaster.com/2024-05-how-to-mix-voiceovers-and-background-music
- https://www.skrrol.ai/blog/audio-ducking-explained
- https://mytasker.com/blog/the-complete-guide-to-sound-design-for-video-creators
- https://github.com/MiniMax-AI/MiniMax-Music3/blob/main/skills/music-caption-rewriter/SKILL.md
- https://github.com/scragnog/HOT-Step-CPP
- https://www.minimax-music.com/minimax-music-3-prompt-guide
- https://www.ambienceai.com/tutorials/minimax-music-prompting-guide
