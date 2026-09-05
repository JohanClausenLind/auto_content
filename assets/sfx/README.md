# `assets/sfx` — video sound-effect and ambience library

49 sounds: **38 cut from licensed recordings** and **11 generated locally** with StableAudio3-Small-SFX.
44.1 kHz stereo, 24-bit FLAC throughout, every file levelled to the same two targets.
Nothing here was downloaded during a build; nothing here was uploaded.

**Do not hand-edit this directory.** It is written by two builders and they own it:

| half | builder | recipe |
|---|---|---|
| recorded | `skills/audio/sfx/ingest_recorded.py` | `skills/audio/sfx/recorded.json` |
| generated | `skills/audio/sfx/build_library.py` | `skills/audio/sfx/library.json` |

Either builder re-renders this file and `manifest.json` over the whole library, so a
partial rebuild stays consistent. `manifest.json` carries per-file sha256, loudness and QC
numbers, plus the source offset every recorded excerpt was cut at. Design decisions: `docs/research/2026-09-07-video-sfx-and-ambience-library.md`.

## Provenance and licensing

The recorded half is cut from the **Sonniss #GameAudioGDC Bundle 2026 (Part 9)**, which the
operator holds locally. Its licence is royalty-free: unlimited personal and commercial
projects, modification permitted, **no attribution required**. Suppliers are recorded
anyway — provenance is not the same thing as an obligation, and a library you cannot trace
is a library you cannot re-cut. `manifest.json` names the exact source file, its sha256 and
the offset for every excerpt.

| supplier | sounds | libraries drawn from |
|---|---|---|
| 344 Audio | 6 | Antique Clocks, Antique Typewriter, Bass Drops & Downers Vol. 1, East Coast America Vol. 1, Elemental Palette Designed Vol. 1, Extreme Winds Vol. 1 |
| CB_Sounddesign | 1 | Applicable Sounds - Organic UI and Building Games SFX |
| Cinematic Sound Design | 8 | Colossal Impacts, Interface & Infographics, Paper Foley, System & UI Feedback Elements, UI Interaction Elements, Ultra Transitions & Impacts, User Interface |
| Epic Stock Media | 5 | HD Lock And Mechanism Sound Design Kit, Public Spaces - Storms Lakes Parks and Rural Nature Exteriors, Public Spaces - Urban Life Exteriors, Strange Game Ambient Loops 3 |
| Federico Soler | 1 | Effective Trailer Booms Vol. 2 |
| InMotionAudio | 4 | Back Garden Storm, Grand Central Station, USA Hotel |
| Ivo Vicic | 1 | Campfire - Bonfire FX |
| Jake Fielding | 6 | British Walla and Crowd, Cinematic Horn Braams, Fridge Hums Vol.2, Interior Wind Rain and Storms |
| Just Sound Effects | 2 | Highlands of Norway, Rocky Coast of Norway |
| Sonic Bat | 1 | Bars & Restaurants Ambience |
| The Noisery | 3 | City Rain, Moaning Metal, Rich Glitch |

The bundle is **not** in this repo and is never committed: 8.0 GB
of source audio lives outside it, and only the excerpts below are kept. Point
`CF_SONNISS_GDC_DIR` at an extracted copy to rebuild.

The generated half comes from **StableAudio3-Small-SFX** (Stability AI Community License (weights); Gemma Terms of Use (T5Gemma text encoder)),
each sound on a fixed seed, so it is reproducible from its recipe alone.

## Levels

| Family | Target | Ceiling |
|---|---|---|
| One-shots | max-momentary **-16.0 LUFS** | -3.0 dBTP |
| Beds (`loopable: true`) | integrated **-23.0 LUFS** | -1.0 dBTP |

Both sit deliberately below the -14 LUFS programme target, so you add gain rather than
fight clipping. Level was set with a single constant scalar — no compression, no limiting —
because time-varying gain would break the loop seams. Recorded and generated sounds go
through the same levelling code and land on the same numbers, so the two halves are
interchangeable in a mix.

These one-shots stop short of the loudness target because their peaks reached the
ceiling first — a short transient is nearly all crest, and reaching -16 LUFS momentary
would clip it: `click_soft`, `counter_tick`, `glitch_data`, `impact_soft`, `page_change`, `pop_in`, `pop_small`, `riser_short`, `slide_transition`, `success_chime`, `swish_fast`, `thunder_distant`, `tick_light`, `type_key`, `whoosh_reverse`, `whoosh_ui_short`.
`loudness.gain_limited_by` in the manifest is the field that says so. It is also how
they should sit — a UI click belongs under a scene transition, not level with it.

## Loops

Every `loopable: true` file is butt-joinable: set the clip to repeat with **no crossfade
and no gap**. The tail was folded back over the head before the file was written, so the
wrap point is a continuous span of the source rather than a splice — there is nothing there
to click on. Two manifest numbers say whether a bed survives repeating:
`qc.seam_rms_delta_db` is the level match across the wrap (all beds here are within 1.5 dB)
and `qc.event_prominence_db` is the loudest half-second over the median half-second — the
thing that actually gives a loop away. Above about 10 dB a viewer hears something recur;
everything here is at 8.2 dB or below.

For a recorded bed that is also what chose the excerpt. A long ambience is not uniformly
loopable: the builder sweeps the whole recording on a 0.25 s envelope, shortlists the
evenest windows, wraps each one for real and keeps the best-scoring — `window_start_s` and
`windows_auditioned` in the manifest say where it landed and how many it looked at.

Thunder is deliberately **not** in `storm_bed_loop` for that reason. Use the
`thunder_distant` one-shot over the bed and place the rolls where you want them.

## drone

| id | file | length | source | use |
|---|---|---|---|---|
| `drone_low_loop` | `drone/drone_low_loop.flac` | 28.00s | Epic Stock Media | Tension under a serious passage where music would be too much. A steady dark tonal layer, designed as a loop by its author. |
| `sub_bed_loop` | `drone/sub_bed_loop.flac` | 22.00s | generated | Adds weight under any other bed. Felt more than heard; check on a full-range system. |

## impact

| id | file | length | source | use |
|---|---|---|---|---|
| `braam_horn` | `impact/braam_horn.flac` | 9.76s | Jake Fielding | The big version of braam_soft: a real horn section, dark and wide. For a title or a single gravest point — one per film, not one per chapter. |
| `braam_soft` | `impact/braam_soft.flac` | 4.50s | generated | Documentary accent for a grave point. A restrained braam, not a trailer hit. |
| `impact_cinematic` | `impact/impact_cinematic.flac` | 9.21s | Federico Soler | Title cards and act breaks. A trailer boom with a long tail: leave it room, do not cut it short. |
| `impact_soft` | `impact/impact_soft.flac` | 4.79s | The Noisery | Weight under a lower third or a card landing. The everyday impact: a real metal thud with a short low ring, no trailer tail. |
| `sub_drop` | `impact/sub_drop.flac` | 6.88s | 344 Audio | Section change or a hard stop. A designed bass downer — check it on speakers that can reproduce 40 Hz. |

## place

| id | file | length | source | use |
|---|---|---|---|---|
| `city_distant_loop` | `place/city_distant_loop.flac` | 30.00s | Epic Stock Media | Urban interiors and skyline shots. A calm courtyard with the traffic a street away, so there are no sirens or horns to date a shot. |
| `crowd_murmur_loop` | `place/crowd_murmur_loop.flac` | 30.00s | Sonic Bat | Conferences, museums, lobbies. A large hall's worth of voices at distance — the window was picked for the lowest event prominence, which is what keeps a single foreground sentence out of it. |
| `fireplace_loop` | `place/fireplace_loop.flac` | 24.00s | Ivo Vicic | Interior warmth, historical pieces, cosy segments. A real close-miked fire; the crackles are sparse, which is why the loop is long. |
| `forest_day_loop` | `place/forest_day_loop.flac` | 28.00s | 344 Audio | Nature documentary bed. Deciduous forest with birds and insects; the loop is long to hide the sparse calls. |
| `lake_shore_loop` | `place/lake_shore_loop.flac` | 30.00s | Epic Stock Media | Still water: ripples against a dock. The quiet water bed — use it under maps and reflective passages where ocean_waves_loop would be too much movement. |
| `meadow_day_loop` | `place/meadow_day_loop.flac` | 30.00s | Just Sound Effects | Open country rather than woodland: pipits, humming insects and wind through grass. The neutral outdoor bed for a map or a landscape hold. |
| `night_crickets_loop` | `place/night_crickets_loop.flac` | 22.00s | generated | Night exteriors. The crickets are even, so the loop point is invisible. |
| `ocean_waves_loop` | `place/ocean_waves_loop.flac` | 40.00s | Just Sound Effects | Coastal establishing shots and calm reflective passages. Soft waves against cliffs on the Norwegian coast; the loop is long because a wave set is long. |
| `park_wind_trees_loop` | `place/park_wind_trees_loop.flac` | 30.00s | Epic Stock Media | Wind in foliage with a city wash behind it. The bed for an urban park or a tree-lined street — motion without weather. |
| `pub_walla_loop` | `place/pub_walla_loop.flac` | 30.00s | Jake Fielding | Bar and restaurant interiors. Closer and warmer than crowd_murmur_loop, with glass and room in it; individual voices are audible, so keep it under narration rather than beside it. |
| `station_hall_loop` | `place/station_hall_loop.flac` | 30.00s | InMotionAudio | Transit and institutional interiors: a big reverberant concourse. The long tail on every footstep is what sells the size of a room a shot only implies. |
| `stream_loop` | `place/stream_loop.flac` | 22.00s | generated | Woodland and countryside scenes; also a neutral water bed under maps. |

## room-tone

| id | file | length | source | use |
|---|---|---|---|---|
| `room_tone_hum_loop` | `room-tone/room_tone_hum_loop.flac` | 20.00s | Jake Fielding | Domestic and small-room interiors: a deep appliance hum, which is most of what an indoor room tone actually is. Patch silence between takes with it and keep it very low. |
| `room_tone_office_loop` | `room-tone/room_tone_office_loop.flac` | 20.00s | generated | Patch silence between interview takes so cuts do not go dead. Keep it very low. |

## transition

| id | file | length | source | use |
|---|---|---|---|---|
| `riser_short` | `transition/riser_short.flac` | 2.95s | generated | Three-second build into a reveal or a statistic. |
| `sweep_documentary` | `transition/sweep_documentary.flac` | 3.12s | generated | Restrained transition for factual pieces; sits under voiceover without pulling focus. |
| `swish_fast` | `transition/swish_fast.flac` | 1.60s | generated | Quick wipes and fast-paced social edits where whoosh_soft is too long. |
| `whoosh_deep` | `transition/whoosh_deep.flac` | 3.40s | generated | Bigger scene or chapter change; pair with impact_cinematic on the landing. |
| `whoosh_reverse` | `transition/whoosh_reverse.flac` | 2.37s | Cinematic Sound Design | Swell into a hard cut; align the stop exactly on the cut frame. A sweep that arrives rather than departs. |
| `whoosh_soft` | `transition/whoosh_soft.flac` | 1.04s | 344 Audio | Default cut-masker. A recorded air rush rather than filtered noise. Land the cut ~60% through the whoosh. |

## ui

| id | file | length | source | use |
|---|---|---|---|---|
| `chart_reveal` | `ui/chart_reveal.flac` | 0.73s | Cinematic Sound Design | A bar chart drawing in or a figure resolving. Time the last note of the arpeggio to the final frame of the animation. |
| `click_soft` | `ui/click_soft.flac` | 0.12s | InMotionAudio | Cursor clicks in a screen recording or a product demo. A real light switch: mid-focused, so it reads over speech without the 4 kHz spike a bright UI click puts there. |
| `counter_tick` | `ui/counter_tick.flac` | 0.23s | Epic Stock Media | The mechanical sibling of tick_light: a counting-machine detent, drier and shorter. For fast count-ups where a clock tick would ring. |
| `data_ticks` | `ui/data_ticks.flac` | 4.16s | Cinematic Sound Design | Number count-ups and data streaming in. A continuous readout — fade it under narration rather than cutting it. |
| `error_soft` | `ui/error_soft.flac` | 0.45s | Cinematic Sound Design | Something did not work. A muted deny — deliberately matter-of-fact, not a buzzer. |
| `glitch_data` | `ui/glitch_data.flac` | 5.41s | The Noisery | Data corrupting, a feed dropping out, a system failing. The hard version of error_soft, for the beat where something breaks rather than merely refuses. |
| `notify_soft` | `ui/notify_soft.flac` | 0.58s | Cinematic Sound Design | A message or notification arriving on screen. A short bright pluck — kept whole, because the gesture is the sound. |
| `page_change` | `ui/page_change.flac` | 0.39s | Cinematic Sound Design | Turning to a new page, slide or chapter — a real page turn, which is the literal cue and reads instantly. |
| `pop_in` | `ui/pop_in.flac` | 0.15s | Cinematic Sound Design | Something pops onto screen: an icon, a callout, a pin dropping on a map. |
| `pop_small` | `ui/pop_small.flac` | 0.90s | generated | Bullet points appearing one by one. Quieter sibling of pop_in. |
| `slide_transition` | `ui/slide_transition.flac` | 0.19s | Cinematic Sound Design | A graph, panel or dashboard view sliding to the next state. Short, dry and made for infographics. |
| `success_chime` | `ui/success_chime.flac` | 0.33s | CB_Sounddesign | A step completing or a checkmark landing. Three ascending kalimba notes: tuned, warm, and over in under a second. |
| `tick_light` | `ui/tick_light.flac` | 0.20s | 344 Audio | Step-by-step counters and timelines. One tick of a real mechanical clock — retrigger it; do not use data_ticks for slow counts. |
| `type_key` | `ui/type_key.flac` | 0.25s | 344 Audio | Typewriter text reveals. An actual typewriter key. Retrigger per character at low level. |
| `whoosh_ui_short` | `ui/whoosh_ui_short.flac` | 1.40s | generated | Interface panels sliding in. Sub-second whoosh for motion graphics. |

## weather

| id | file | length | source | use |
|---|---|---|---|---|
| `hail_window_loop` | `weather/hail_window_loop.flac` | 24.00s | Jake Fielding | Interior under hail. Harder and more granular than rain_window_loop; good for a turn in a story that rain is too gentle for. |
| `rain_city_loop` | `weather/rain_city_loop.flac` | 30.00s | The Noisery | Rain over a street: splatter on concrete with a distant traffic wash under it. Use where the shot is urban and rain_light_loop would sound like open country. |
| `rain_light_loop` | `weather/rain_light_loop.flac` | 24.00s | InMotionAudio | General wet-weather bed. The safest rain: real garden rainfall, no thunder to clash with narration and no traffic to place it in a city. |
| `rain_window_loop` | `weather/rain_window_loop.flac` | 22.00s | Jake Fielding | Interior scene with weather outside. Heavy rain on real glass, so it is muffled where a synthesised rain is only quiet — dialogue sits over it easily. |
| `storm_bed_loop` | `weather/storm_bed_loop.flac` | 30.00s | InMotionAudio | Heavy-weather bed. Continuous by design — the window was chosen for evenness, so no thunder clap is baked in; add thunder_distant on top where you want one. |
| `thunder_distant` | `weather/thunder_distant.flac` | 16.50s | Jake Fielding | Drop over storm_bed_loop wherever you want a thunder roll. Kept separate on purpose: a clap baked into a loop announces the repeat every cycle. |
| `wind_open_loop` | `weather/wind_open_loop.flac` | 30.00s | 344 Audio | Exteriors, landscapes, aerials. A real storm-force wind recorded in the open; gust swells are slow enough to read as one movement. |

