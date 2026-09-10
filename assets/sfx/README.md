# `assets/sfx` — video sound-effect and ambience library

670 sounds: **346 cut from the #GameAudioGDC bundle**, **11 generated locally** with StableAudio3-Small-SFX, **294 from the Mixkit free sound-effects pack** and **19 rendered locally by the operator**.
44.1 kHz stereo, 24-bit FLAC throughout, every file levelled to the same two targets.
Nothing here was downloaded during a build; nothing here was uploaded.

**Do not hand-edit this directory.** It is written by the builders below and they own it:

| source | builder | recipe |
|---|---|---|
| recorded | `skills/audio/sfx/ingest_recorded.py` | `skills/audio/sfx/recorded.json` |
| generated | `skills/audio/sfx/build_library.py` | `skills/audio/sfx/library.json` |
| mixkit | `skills/audio/sfx/ingest_packs.py` | `skills/audio/sfx/mixkit.json` |
| local-renders | `skills/audio/sfx/ingest_packs.py` | `skills/audio/sfx/local_renders.json` |

Any builder re-renders this file and `manifest.json` over the whole library, so a
partial rebuild stays consistent. `manifest.json` carries per-file sha256, loudness and QC
numbers, plus the source file and offset every excerpt was cut at. Design decisions:
`docs/research/2026-09-07-video-sfx-and-ambience-library.md`.

## Provenance and licensing

### 346 sounds — Sonniss #GameAudioGDC Bundle 2026 (Part 9) (8.0 GB)

Sonniss #GameAudioGDC Bundle licensing agreement: worldwide, non-exclusive, royalty-free; unlimited personal and commercial projects; modification permitted; no attribution required

The source audio is **not** in this repo and is never committed: it lives at
`/home/vega/Music/sonniss_gdc_2026` and only the levelled excerpts are kept here. Point
`CF_SONNISS_GDC_DIR` at another copy to rebuild.

### 294 sounds — Mixkit free sound effects (0.8 GB)

Mixkit Sound Effects Free License: free use in commercial and non-commercial projects; licensed to download, copy, modify, distribute and publicly perform the Item as part of an End Product that is larger in scope and different in nature than the Item; no attribution clause in the licence. Prohibited: redistributing the Item on its own, as stock, in a tool or template, or with source files; claiming it as your own; registering it with a rights management service. Envato User Terms cl.9 additionally forbids aggregating Items and making them available on a stock or inventory basis.

Licence read from <https://mixkit.co/license/#sfxFree> on 2026-09-09.

The source audio is **not** in this repo and is never committed: it lives at
`/mnt/fast/sound-libraries/mixkit` and only the levelled excerpts are kept here. Point
`CF_MIXKIT_SFX_DIR` at another copy to rebuild.

### 19 sounds — Operator's local renders (13 MB)

Rendered locally by the operator before ingest; no third-party licence attaches. The generator and its settings were not recorded, so unlike the rest of the library these cannot be rebuilt from a recipe -- only re-ingested from the staged source file.

The source audio is **not** in this repo and is never committed: it lives at
`/mnt/fast/sound-libraries/local-renders` and only the levelled excerpts are kept here. Point
`CF_LOCAL_SFX_DIR` at another copy to rebuild.

Suppliers are recorded even where the licence does not ask for it — provenance is not
the same thing as an obligation, and a library you cannot trace is a library you cannot
re-cut. `manifest.json` names the exact source file, its sha256 and the offset for every
excerpt.

| supplier | sounds | libraries drawn from |
|---|---|---|
| 344 Audio | 82 | Air Designed, Anime Fight Voices Vol. 1, Anime Fight Voices Vol. 2, Antique Books, Antique Clocks, Antique Luggage, Antique Small Metals, Antique Telephone, Antique Typewriter, Barbershop Vol. 1, Barbershop Vol. 2, Bass Drops & Downers Vol. 1, Bass Drops & Downers Vol. 2, Bass Drops & Downers Vol. 3, British Police Radio Vol. 1, Car Foley Vol. 1, Casino Cards Vol. 1, Christmas Vol. 1, Cinema Experience Vol. 1, Cinematic Fight Vol. 1, Climbing Gear Foley Vol. 1, Dinosaurs Vol. 1, Dinosaurs Vol. 2, Dog Vocalisations Vol. 1, East Coast America Vol. 1, Elemental Palette Designed Vol. 1, Extreme Winds Vol. 1, Ghostly Presences Vol. 1, Haunting Ambiences Vol. 3, Haunting Ambiences Vol. 5, Historical Weapons Vol. 2 |
| Alexander Kopeikin | 8 | 100 kHz Designed Ice, Emotion and Magic |
| CB_Sounddesign | 4 | Applicable Sounds - Organic UI and Building Games SFX |
| Cinematic Sound Design | 36 | Cartoon & Animation Vol 2, Cartoon Bloopers, Cartoon Impacts, Colossal Impacts, Hybrid Game & UI Elements, Interface & Infographics, Paper Foley, Sci-Fi Drones, System & UI Feedback Elements, UI Interaction Elements, Ultra Transitions & Impacts, User Interface |
| David Dumais Audio | 4 | Melee Weapons Sound Effects Pack 2 |
| Epic Stock Media | 72 | AAA Game Character British Female Detective, AAA Game Character Police Officer, Anime Game, Board Game - Sound Set Kit for Tabletop and Digital Games, Elemental Mutation Whooshes and Impacts, Fake Advertisements and Radio Sound Effects Audio Construction Kit, Fantasy Game 2 - Sound Kit for Enchanted Realms, HD Game Materials, HD Lock And Mechanism Sound Design Kit, Halloween Game - Haunted House and Horror Audio Scare Kit, Humanoid Creatures Vol 4 - Monstrous and Undead Creature Vocalization Sound Sets, Public Spaces - Basic Transportation Sounds, Public Spaces - Crowds Walla and Everyday Ambiences, Public Spaces - Storms Lakes Parks and Rural Nature Exteriors, Public Spaces - Urban Life Exteriors, Shooter Game Announcer Voice, Strange Game Ambient Loops 3, Synthesized Nature Loops and Sounds, Tower Defense Game |
| Federico Soler | 6 | Effective Trailer Alarms Vol. 2, Effective Trailer Booms Vol. 2 |
| InMotionAudio | 45 | Arc, Back Garden Storm, Car Interior Driving Ambience, Chimney Wind, Christmas Cracker, Foley T-Shirt, Grand Central Station, Instrument Case, Medical Thermometer, Saxophone, Scratch Card, Sinister Textures 4, Sinister Textures 5, The Commute, The Death Whistle, USA Hotel, Velcro, Washing Basket Foley |
| Ivo Vicic | 6 | Campfire - Bonfire FX, Church Bells, Fireworks FX |
| Jake Fielding | 9 | British Walla and Crowd, Cinematic Horn Braams, Fridge Hums Vol.2, Interior Wind Rain and Storms |
| Just Sound Effects | 4 | Highlands of Norway, Rocky Coast of Norway |
| Mixkit (Envato) | 294 | Mixkit free sound effects |
| Sonic Bat | 16 | Bars & Restaurants Ambience, Fireworks Ambience, Music Boxes, Stormy Night Ambience, Swimming Pool Ambience, Vintage Radio, Volleyball Match Ambience |
| Sonik Sound Library | 8 | Spanish Crowds, Spanish Wallas, Toy Quadcopter, Urban Life |
| SoundBits | 24 | Cars - Mad Mustang Mercury, Motorcycles - Honda, Motorcycles - Kawasaki, Pass-By - Trains, Trucks & Cars 2, Vox Bestiae - Source Elements, Vox Hominis - Human Effort Voices |
| The Noisery | 11 | City Rain, Moaning Metal, Rich Glitch, Transit Tunes - The German Train Collection |
| TheWorkRoom | 9 | Flip Flops, Nigerian Walla, South African Walla, Wire Cars |
| Victor Ermakov | 2 | Industrial Ambiences - Ship Repair Factory |

### 11 sounds — StableAudio3-Small-SFX (Stability AI Community License (weights); Gemma Terms of Use (T5Gemma text encoder))

Generated locally, each sound on a fixed seed, so it is reproducible from its recipe
alone.

## Levels

| Family | Target | Ceiling |
|---|---|---|
| One-shots | max-momentary **-16.0 LUFS** | -3.0 dBTP |
| Beds (`loopable: true`) | integrated **-23.0 LUFS** | -1.0 dBTP |

Both sit deliberately below the -14 LUFS programme target, so you add gain rather than
fight clipping. Level was set with a single constant scalar — no compression, no limiting —
because time-varying gain would break the loop seams. Every source goes through the same
levelling code and lands on the same numbers, so sounds from different origins are
interchangeable in a mix.

These one-shots stop short of the loudness target because their peaks reached the
ceiling first — a short transient is nearly all crest, and reaching -16.0 LUFS momentary
would clip it: `accept_glassy`, `animal_eating_long`, `animal_eating_meat`, `ape_call`, `applause_light`, `arrow_hit`, `arrow_whoosh`, `barber_chair_pump`, `bass_downer_fast_alt`, `bass_downer_medium_alt`, `bass_downer_slow`, `big_cat_purr`, `bird_flutter_medium`, `bird_flutter_small`, `bird_flying_away`, `blow_fast`, `book_page_turns_slow`, `book_pages_flick`, `books_falling`, `broom_sweeping`, `campfire_extinguished`, `car_cup_holder`, `car_door`, `car_fan_dial`, `car_light_switch`, `car_pedals`, `carabiner_lock`, `carabiner_rope`, `card_flip`, `card_shuffler`, `cards_dealing`, `cartoon_animal_pain`, `cartoon_monkey_applause`, `cartoon_pull_swoosh`, `case_close`, `case_handle`, `case_set_down`, `chewing_crunchy`, `cladding_scratch`, `click_double_vintage`, `click_soft`, `clock_ticking`, `cloth_bag_flip`, `cloth_movement`, `cloth_pat`, `cockerel_cry`, `coin_flip`, `coins_ting`, `confetti_spill`, `cough_hard`, `cough_sick`, `counter_tick`, `cow_eating`, `cracker_grab`, `cracker_pull`, `crowd_laugh_applause`, `dog_barks_indoors`, `dog_sniffing`, `dry_leaves`, `eating_man`, `egg_hatching`, `electric_arc`, `fish_in_water`, `fly_wings`, `game_piece_drop`, `glitch_data`, `guitar_body_hit`, `guitar_slide_noise`, `guitar_slide_western`, `hair_brushing`, `hens_clucking`, `horse_gallop_concrete`, `horse_gallop_dirt`, `horse_gallop_puddles`, `horse_trot_concrete`, `horse_walk_concrete`, `impact_chord`, `impact_soft`, `kiss_double`, `kiss_loving`, `kiss_small`, `latch_thunk`, `laundry_basket`, `laundry_basket_short`, `lock_tinkering`, `measuring_tape`, `medical_button_beep`, `metal_door_close`, `metal_hatch_open`, `metal_lid_open`, `metal_tap`, `metal_whoosh_pass`, `mic_hit`, `monkey_chest_thump`, `motorcycle_horn`, `motorcycle_keys`, `motorcycle_mount`, `music_box_b`, `music_box_wind_up`, `nail_scratch`, `newspaper_rummage`, `noise_box_hit`, `notify_guitar`, `page_change`, `paper_a4_turn`, `paper_hat_movement`, `pig_drinking`, `pig_grunt_short`, `pigeons_flutter`, `pop_hard`, `pop_in`, `pop_long`, `pop_small`, `punch_fast`, `punches_body`, `radio_mode_wheel`, `radio_power_button`, `radio_tuning_sweep`, `rattlesnake`, `record_scratch`, `riser_short`, `sax_case_place`, `sax_handling`, `sax_key_press`, `scissor_snips`, `scratchcard_movement`, `scratchcard_scratch`, `scratchcard_wipe`, `seat_creak`, `select_modern`, `shaker_snap`, `sleigh_pulling`, `slide_transition`, `snap_percussive`, `spear_impact`, `splat_small`, `spray_bottle`, `stairwell_door`, `struggle_choking`, `success_chime`, `suitcase_lock`, `suitcase_open_close`, `suitcase_rummage`, `sweep_small`, `swish_fast`, `sword_cuts`, `tarot_shuffle`, `tech_slide`, `telephone_earpiece_drop`, `telephone_earpiece_handle`, `thunder_distant`, `tick_light`, `timer_alert`, `toilet_flush`, `tram_passing`, `type_key`, `typewriter_carriage`, `typewriter_paper`, `velcro_rip`, `velcro_squeeze`, `venom_squirt`, `whoosh_debris`, `whoosh_reverse`, `whoosh_ui_short`, `wood_roll`, `wrapping_paper`, `xylophone_ringtone`.
`loudness.gain_limited_by` in the manifest is the field that says so. It is also how
they should sit — a UI click belongs under a scene transition, not level with it.

## Loops

Every `loopable: true` file is butt-joinable: set the clip to repeat with **no crossfade
and no gap**. The tail was folded back over the head before the file was written, so the
wrap point is a continuous span of the source rather than a splice — there is nothing there
to click on. Two manifest numbers say whether a bed survives repeating:
`qc.seam_rms_delta_db` is the level match across the wrap (all beds here are within 2.7 dB)
and `qc.event_prominence_db` is the loudest half-second over the median half-second — the
thing that actually gives a loop away. Above about 10 dB a viewer hears something recur;
the highest here is 21.8 dB.

For a bed cut from a longer recording that is also what chose the excerpt. A long ambience
is not uniformly loopable: the builder sweeps the whole recording on a 0.25 s envelope,
shortlists the evenest windows, wraps each one for real and keeps the best-scoring —
`window_start_s` and `windows_auditioned` in the manifest say where it landed and how many
it looked at.

These beds carry an event loud enough to be heard recurring, and are listed rather than
quietly shipped: `airfield_loop`, `birds_morning_loop`, `dawn_chorus_loop`, `farmyard_morning_loop`, `footsteps_grass_loop`, `forest_storm_loop`, `garden_morning_loop`, `glacial_lake_loop`, `goat_herd_close_loop`, `meadow_birds_loop`, `owl_forest_loop`, `pigsty_herd_loop`, `pigsty_loop`, `respirator_loop`, `storm_clear_rain_loop`, `thunder_rumble_loop`, `thunderstorm_rain_loop`, `wire_car_loop`, `woodland_birds_loop`.
Each one is a bed whose *subject* is the event — thunder in a thunderstorm, a bleat in a
herd — so the alternative was not having it. Use them where the repeat is masked by
narration or picture, and prefer a neutral bed plus a placed one-shot where it is not:
`storm_bed_loop` has no thunder in it for exactly this reason, and `thunder_distant`
and `thunder_strike` exist to be dropped on top where you want the rolls.

## animal

| id | file | length | source | use |
|---|---|---|---|---|
| `animal_eating_long` | `animal/animal_eating_long.flac` | 20.52s | Mixkit (Envato) | Twenty seconds of the same. Long enough to run under a whole feeding sequence. |
| `animal_eating_meat` | `animal/animal_eating_meat.flac` | 5.06s | Mixkit (Envato) | A predator at a kill. Five seconds, and unpleasant on purpose. |
| `ape_call` | `animal/ape_call.flac` | 1.23s | Mixkit (Envato) | An ape calling in the canopy. Primate and rainforest subjects. |
| `bat_squeak` | `animal/bat_squeak.flac` | 0.49s | Mixkit (Envato) | A bat's squeak. Caves, dusk and urban wildlife. |
| `bee_flying` | `animal/bee_flying.flac` | 2.09s | Mixkit (Envato) | A bee working hard. Pollination, agriculture and garden subjects. |
| `big_cat_purr` | `animal/big_cat_purr.flac` | 3.53s | Mixkit (Envato) | A big cat purring — lower and rougher than cat_purr. Wildlife. |
| `big_cat_roar` | `animal/big_cat_roar.flac` | 1.53s | Mixkit (Envato) | A big cat roaring. A real recording, unlike the creature roars. |
| `bird_chirp_call` | `animal/bird_chirp_call.flac` | 1.04s | Mixkit (Envato) | One small bird calling. The plain single chirp. |
| `bird_chirp_tropical` | `animal/bird_chirp_tropical.flac` | 0.36s | Mixkit (Envato) | A third of a second of tropical chirp. The shortest sound in the library. |
| `bird_chirp_wild` | `animal/bird_chirp_wild.flac` | 5.04s | Mixkit (Envato) | Five seconds of a bird calling in the open, with air around it. |
| `bird_flutter_ambience` | `animal/bird_flutter_ambience.flac` | 29.71s | Mixkit (Envato) | Twenty-eight unbroken seconds of wingbeats at close range: an aviary, a loft, a flock working over a field. Not loopable — the passes are too uneven to wrap (seam 5.0 dB, event prominence 19.5 dB when it was tried) — so lay it under a shot and fade it. bird_flutter_medium is the single pass that lands on a frame. |
| `bird_flutter_medium` | `animal/bird_flutter_medium.flac` | 3.12s | Mixkit (Envato) | A larger bird's wings. Slower beats, more air moved. |
| `bird_flutter_small` | `animal/bird_flutter_small.flac` | 2.78s | Mixkit (Envato) | A small bird's wings, close. For a cutaway to something landing. |
| `bird_flying_away` | `animal/bird_flying_away.flac` | 3.14s | Mixkit (Envato) | A single bird leaving. Three seconds, and it moves. |
| `bird_screech_jungle` | `animal/bird_screech_jungle.flac` | 1.10s | Mixkit (Envato) | A harsh jungle screech. Alarm rather than song. |
| `bird_song_wings` | `animal/bird_song_wings.flac` | 3.55s | Mixkit (Envato) | Song and wingbeats together. |
| `bird_squeak_tropical` | `animal/bird_squeak_tropical.flac` | 1.17s | Mixkit (Envato) | A squeaking tropical call. Distinctive enough to carry a location. |
| `bird_whistle_toy` | `animal/bird_whistle_toy.flac` | 0.80s | Mixkit (Envato) | A bird imitated by a whistle. Honest about being an imitation; use it where a real call would over-claim. |
| `bumblebee_buzz` | `animal/bumblebee_buzz.flac` | 1.44s | Mixkit (Envato) | A short bumblebee pass. Lower and rounder than bee_flying. |
| `cat_meow_attention` | `animal/cat_meow_attention.flac` | 0.68s | Mixkit (Envato) | A cat asking for something. Short and direct. |
| `cat_meow_pain` | `animal/cat_meow_pain.flac` | 0.98s | Mixkit (Envato) | A distressed meow. Veterinary and welfare subjects. |
| `cat_meow_sweet` | `animal/cat_meow_sweet.flac` | 0.87s | Mixkit (Envato) | The softest meow here. Domestic and affectionate. |
| `cat_purr` | `animal/cat_purr.flac` | 7.19s | Mixkit (Envato) | Seven seconds of purring, close-miked. Warmth in a shot that has none. |
| `chickens_alarmed` | `animal/chickens_alarmed.flac` | 1.83s | Mixkit (Envato) | Chickens alarmed. Use it where something has disturbed the flock. |
| `chickens_pigeons` | `animal/chickens_pigeons.flac` | 5.04s | Mixkit (Envato) | Chickens and pigeons together in a yard. Five seconds of small-holding. |
| `cockerel_call` | `animal/cockerel_call.flac` | 3.27s | Mixkit (Envato) | A cockerel calling. Morning, farms, and the passage of a day. |
| `cockerel_cry` | `animal/cockerel_cry.flac` | 3.52s | Mixkit (Envato) | A longer, more strained crow. The alternative take to cockerel_call. |
| `cow_breath` | `animal/cow_breath.flac` | 3.39s | Mixkit (Envato) | A cow breathing close to the microphone. For a close-up where a moo would be too much. |
| `cow_eating` | `animal/cow_eating.flac` | 7.17s | Mixkit (Envato) | Seven seconds of a cow eating. Feed, grazing and food-system sequences. |
| `cow_moo` | `animal/cow_moo.flac` | 2.22s | Mixkit (Envato) | The default cow. One clear moo in the open. |
| `cow_moo_barn` | `animal/cow_moo_barn.flac` | 2.01s | Mixkit (Envato) | A moo with a barn around it. The reflections do the work of an establishing shot. |
| `cow_moo_indoors` | `animal/cow_moo_indoors.flac` | 3.26s | Mixkit (Envato) | Indoors and closer still — intensive rather than pastoral farming. |
| `cow_moo_sick` | `animal/cow_moo_sick.flac` | 1.19s | Mixkit (Envato) | A strained moo. Animal-welfare and veterinary subjects; it sounds wrong, which is the point. |
| `cow_moo_single` | `animal/cow_moo_single.flac` | 1.74s | Mixkit (Envato) | A shorter, closer moo. Use it where cow_moo runs long against the picture. |
| `cricket_single` | `animal/cricket_single.flac` | 1.68s | Mixkit (Envato) | One cricket. For a silence that needs exactly one thing in it. |
| `dog_bark_angry` | `animal/dog_bark_angry.flac` | 1.73s | Mixkit (Envato) | An angry bark from a mid-sized dog. Warning rather than greeting. |
| `dog_bark_big` | `animal/dog_bark_big.flac` | 2.71s | Mixkit (Envato) | A big dog, annoyed. Three seconds of it. |
| `dog_bark_twice` | `animal/dog_bark_twice.flac` | 1.56s | Mixkit (Envato) | Two barks. The everyday dog cue. |
| `dog_barks_indoors` | `animal/dog_barks_indoors.flac` | 5.71s | 344 Audio | Several barks with a room around them. The indoor counterpart to dog_bark_twice. |
| `dog_growl` | `animal/dog_growl.flac` | 3.16s | Mixkit (Envato) | A growl building towards a bark. Threat. |
| `dog_growl_aggressive` | `animal/dog_growl_aggressive.flac` | 6.16s | Mixkit (Envato) | Six seconds of sustained aggressive growling. |
| `dog_growl_giant` | `animal/dog_growl_giant.flac` | 5.54s | Mixkit (Envato) | The largest dog in the library. Use it where the animal is meant to frighten. |
| `dog_settling` | `animal/dog_settling.flac` | 5.32s | 344 Audio | A dog shuffling and grunting as it lies down. Domestic, quiet, and very readable. |
| `dog_sniffing` | `animal/dog_sniffing.flac` | 4.78s | Mixkit (Envato) | A dog working a scent. Search, detection and tracking sequences. |
| `dogs_pack_barking` | `animal/dogs_pack_barking.flac` | 10.40s | Mixkit (Envato) | Many dogs at once. Kennels, shelters, or a village at night. |
| `donkey_bray` | `animal/donkey_bray.flac` | 11.95s | Mixkit (Envato) | Twelve seconds of braying. Loud, unmistakable, and hard to place under narration. |
| `donkey_bray_sad` | `animal/donkey_bray_sad.flac` | 10.92s | Mixkit (Envato) | A more mournful bray. Animal-welfare subjects. |
| `donkey_scream` | `animal/donkey_scream.flac` | 8.99s | Mixkit (Envato) | A single scream rather than a run of brays. Nine seconds. |
| `donkey_screech` | `animal/donkey_screech.flac` | 13.87s | Mixkit (Envato) | The harshest of the three: fifteen seconds of screeching bray. |
| `eaglet_chirp` | `animal/eaglet_chirp.flac` | 2.89s | Mixkit (Envato) | A young raptor calling for food. Not the adult scream people expect — which is why it is worth having. |
| `fly_buzzing` | `animal/fly_buzzing.flac` | 1.33s | Mixkit (Envato) | A large fly. Heat, decay, and discomfort in one and a third seconds. |
| `fly_wings` | `animal/fly_wings.flac` | 5.57s | Mixkit (Envato) | Wings without the tonal buzz. Closer and more physical than fly_buzzing. |
| `forest_birds_short` | `animal/forest_birds_short.flac` | 2.13s | Mixkit (Envato) | Two seconds of forest birdsong. For a cutaway too short for a bed. |
| `frog_call` | `animal/frog_call.flac` | 0.57s | Epic Stock Media | One frog, with an echo on it. Wetland and pond subjects. |
| `geese_flock` | `animal/geese_flock.flac` | 5.19s | Mixkit (Envato) | Geese calling in flight. Migration and wetland subjects. |
| `goat_baa` | `animal/goat_baa.flac` | 1.06s | Mixkit (Envato) | One goat, once. The clean single bleat. |
| `goat_baa_single` | `animal/goat_baa_single.flac` | 0.99s | Mixkit (Envato) | A second goat with a different voice — two are better than one when a herd is on screen. |
| `goat_kid_bleat` | `animal/goat_kid_bleat.flac` | 0.97s | Mixkit (Envato) | A young goat. Higher and shorter than the adults. |
| `hens_clucking` | `animal/hens_clucking.flac` | 1.27s | Mixkit (Envato) | Hens clucking. The short one, for a cutaway. |
| `horse_gallop_concrete` | `animal/horse_gallop_concrete.flac` | 12.85s | Mixkit (Envato) | A gallop on hard ground. Thirteen seconds, so it can run under a whole sequence. |
| `horse_gallop_dirt` | `animal/horse_gallop_dirt.flac` | 13.06s | Mixkit (Envato) | The same gallop on earth: duller, softer, and the one for open country. |
| `horse_gallop_puddles` | `animal/horse_gallop_puddles.flac` | 12.90s | Mixkit (Envato) | A gallop through standing water. Weather and picture in one sound. |
| `horse_neigh` | `animal/horse_neigh.flac` | 2.80s | Mixkit (Envato) | The default neigh. Three seconds, clean. |
| `horse_neigh_intense` | `animal/horse_neigh_intense.flac` | 2.31s | Mixkit (Envato) | A harder neigh with more effort in it. |
| `horse_neigh_scared` | `animal/horse_neigh_scared.flac` | 2.19s | Mixkit (Envato) | A frightened horse. For an incident, not for a paddock. |
| `horse_scared` | `animal/horse_scared.flac` | 6.18s | Mixkit (Envato) | Six seconds of an agitated horse: breath, movement and voice together. |
| `horse_snore` | `animal/horse_snore.flac` | 1.13s | Mixkit (Envato) | A horse asleep. Stables at night. |
| `horse_snort` | `animal/horse_snort.flac` | 1.27s | Mixkit (Envato) | A snort. The small close sound that says a large animal is standing there. |
| `horse_trot_concrete` | `animal/horse_trot_concrete.flac` | 13.04s | Mixkit (Envato) | A trot on the same surface — the pace up from horse_walk_concrete. |
| `horse_walk_concrete` | `animal/horse_walk_concrete.flac` | 13.11s | Mixkit (Envato) | Shod hooves walking on hard ground. Streets and yards. |
| `insect_pass` | `animal/insect_pass.flac` | 0.98s | Mixkit (Envato) | An insect crossing the frame. Barely a second. |
| `jungle_birds_short` | `animal/jungle_birds_short.flac` | 6.59s | Mixkit (Envato) | Seven seconds of tropical birds. The short form of jungle_river_loop's bird layer. |
| `jungle_call` | `animal/jungle_call.flac` | 1.68s | Mixkit (Envato) | An unidentified jungle call. Atmosphere for a rainforest sequence where a named species would be a claim. |
| `lion_growl_wounded` | `animal/lion_growl_wounded.flac` | 5.17s | Mixkit (Envato) | A wounded lion growling. Conservation and hunting subjects. |
| `lion_roar` | `animal/lion_roar.flac` | 1.28s | Mixkit (Envato) | A lion. The one everyone recognises. |
| `monkey_chest_call` | `animal/monkey_chest_call.flac` | 1.74s | Mixkit (Envato) | The same display with a call over it. |
| `monkey_chest_scared` | `animal/monkey_chest_scared.flac` | 2.55s | Mixkit (Envato) | A frightened version of the display. |
| `monkey_chest_thump` | `animal/monkey_chest_thump.flac` | 2.82s | Mixkit (Envato) | A chest display. Percussive as well as vocal. |
| `monkey_grunt` | `animal/monkey_grunt.flac` | 2.39s | Mixkit (Envato) | Grunting. The quieter primate cue. |
| `monkey_screech` | `animal/monkey_screech.flac` | 1.78s | Mixkit (Envato) | An excited screech. Real, unlike the cartoon monkeys. |
| `nestlings` | `animal/nestlings.flac` | 8.60s | Mixkit (Envato) | Nestlings begging. Breeding-season and conservation subjects. |
| `owl_shriek` | `animal/owl_shriek.flac` | 1.10s | Mixkit (Envato) | An owl's shriek — the barn-owl scream, not the hoot. Night. |
| `pig_drinking` | `animal/pig_drinking.flac` | 9.73s | Mixkit (Envato) | A pig drinking. Ten seconds of a very specific and very readable sound. |
| `pig_grunt_short` | `animal/pig_grunt_short.flac` | 0.54s | Mixkit (Envato) | A single short grunt. The smallest pig sound here. |
| `pig_grunting` | `animal/pig_grunting.flac` | 11.24s | Mixkit (Envato) | Twelve seconds of a pig going about its business. Continuous enough to run under a shot. |
| `pigeons_cooing` | `animal/pigeons_cooing.flac` | 6.03s | Mixkit (Envato) | Pigeons cooing. City squares, lofts and station roofs. |
| `pigeons_flutter` | `animal/pigeons_flutter.flac` | 4.80s | Mixkit (Envato) | A flock of pigeons taking off. The wings are the sound. |
| `rattlesnake` | `animal/rattlesnake.flac` | 4.09s | Mixkit (Envato) | A rattlesnake warning. Four seconds, and it needs no explanation. |
| `raven_call` | `animal/raven_call.flac` | 1.64s | Mixkit (Envato) | A raven. Cold, northern and slightly ominous. |
| `rooster_morning` | `animal/rooster_morning.flac` | 3.41s | Mixkit (Envato) | The clean dawn crow with a quiet yard behind it. The one to use if you only use one. |
| `sheep_baa` | `animal/sheep_baa.flac` | 0.63s | Mixkit (Envato) | Sheep. Upland farming, wool and pasture subjects. |
| `small_birds_trees` | `animal/small_birds_trees.flac` | 8.27s | Mixkit (Envato) | Eight seconds of small birds in a tree. Between a cue and a bed. |
| `wild_grunting` | `animal/wild_grunting.flac` | 3.84s | Mixkit (Envato) | An unidentified large animal feeding. Use it where the species does not need naming. |
| `wolf_howl` | `animal/wolf_howl.flac` | 7.80s | Mixkit (Envato) | A single wolf howling. Eight seconds — give it space. |
| `wolves_forest` | `animal/wolves_forest.flac` | 9.54s | Mixkit (Envato) | Wolves with a forest around them. Closer to a scene than a cue. |
| `wolves_pack_howl` | `animal/wolves_pack_howl.flac` | 6.07s | Mixkit (Envato) | A pack answering each other. |

## creature

| id | file | length | source | use |
|---|---|---|---|---|
| `aquatic_gurgle` | `creature/aquatic_gurgle.flac` | 4.08s | SoundBits | A wet gurgling creature. Designed; underwater fiction. |
| `beast_growl` | `creature/beast_growl.flac` | 1.75s | Mixkit (Envato) | A low growl rather than a roar. Threat approaching, not arriving. |
| `beast_roar` | `creature/beast_roar.flac` | 3.17s | Mixkit (Envato) | A fuller roar with more body. For the creature being seen rather than heard off-screen. |
| `beast_roar_aggressive` | `creature/beast_roar_aggressive.flac` | 1.36s | Mixkit (Envato) | A short aggressive roar. The default creature accent. |
| `beast_roar_long` | `creature/beast_roar_long.flac` | 2.82s | Mixkit (Envato) | A three-second sustained roar. Long enough to carry a reveal. |
| `cartoon_animal_pain` | `creature/cartoon_animal_pain.flac` | 2.76s | Mixkit (Envato) | A cartoon animal yelping. Broad comedy only. |
| `cartoon_cat_angry` | `creature/cartoon_cat_angry.flac` | 0.55s | Mixkit (Envato) | A cross cartoon meow, half a second long. |
| `cartoon_cat_beg` | `creature/cartoon_cat_beg.flac` | 2.23s | Mixkit (Envato) | A pleading cartoon meow. |
| `cartoon_cat_meow` | `creature/cartoon_cat_meow.flac` | 1.32s | Mixkit (Envato) | A performed cartoon meow. Not a recording of a cat — cat_meow_sweet is. |
| `cartoon_hyena_laugh` | `creature/cartoon_hyena_laugh.flac` | 1.60s | Mixkit (Envato) | A cartoon hyena laugh. Comic, and clearly performed. |
| `cartoon_insect` | `creature/cartoon_insect.flac` | 2.32s | Mixkit (Envato) | A grumbling cartoon insect. For animation, not for entomology. |
| `cartoon_monkey_applause` | `creature/cartoon_monkey_applause.flac` | 4.23s | Mixkit (Envato) | Cartoon applause with a monkey in it. Four seconds of pure silliness. |
| `cartoon_monkey_laugh` | `creature/cartoon_monkey_laugh.flac` | 1.23s | Mixkit (Envato) | A cartoon monkey laughing. |
| `cartoon_monkey_laugh_baby` | `creature/cartoon_monkey_laugh_baby.flac` | 1.56s | Mixkit (Envato) | The smaller, higher version of cartoon_monkey_laugh. |
| `cartoon_monkey_mock` | `creature/cartoon_monkey_mock.flac` | 1.91s | Mixkit (Envato) | Mocking giggles. A reaction cue for animation. |
| `cartoon_wolf_howl` | `creature/cartoon_wolf_howl.flac` | 0.65s | Mixkit (Envato) | A cartoon howl. For the joke version of wolf_howl. |
| `creature_breath` | `creature/creature_breath.flac` | 1.57s | Mixkit (Envato) | Heavy creature breathing. Something large just out of frame. |
| `creature_inhale` | `creature/creature_inhale.flac` | 0.98s | Epic Stock Media | A weak screeching inhale. Small, and more frightening than a roar. |
| `creature_roar` | `creature/creature_roar.flac` | 1.74s | Mixkit (Envato) | A wet, unpleasant roar. Horror rather than fantasy. |
| `death_vocal` | `creature/death_vocal.flac` | 4.87s | Epic Stock Media | A stuttered death vocal. Strong material; fiction only. |
| `desert_creature` | `creature/desert_creature.flac` | 1.65s | Mixkit (Envato) | An unidentifiable desert call. Use it where the film wants the unknown rather than a named species. |
| `dinosaur_eating` | `creature/dinosaur_eating.flac` | 18.51s | 344 Audio | Twenty seconds of a large designed animal feeding. Bed level — it runs under a shot. |
| `dragon_growl` | `creature/dragon_growl.flac` | 4.18s | Mixkit (Envato) | Five seconds of dragon. Fantasy, folklore and mythology subjects. |
| `dragon_roar` | `creature/dragon_roar.flac` | 4.17s | Mixkit (Envato) | The full dragon roar. Big enough to be the loudest thing in a scene. |
| `egg_hatching` | `creature/egg_hatching.flac` | 1.50s | 344 Audio | An egg cracking open. |
| `ethereal_pain` | `creature/ethereal_pain.flac` | 1.73s | SoundBits | A grim, disembodied cry. Ghost material. |
| `hatchling_call` | `creature/hatchling_call.flac` | 1.67s | 344 Audio | A small reptile hatchling calling. Designed — this is dinosaur material, not a wildlife recording. |
| `herbivore_roar` | `creature/herbivore_roar.flac` | 11.88s | 344 Audio | A long, lower call from a large plant-eater. Less aggressive than trex_roar. |
| `humanoid_exhale` | `creature/humanoid_exhale.flac` | 0.43s | SoundBits | A short violent exhale. Under half a second. |
| `insectoid_attack` | `creature/insectoid_attack.flac` | 2.47s | SoundBits | A trembling insectoid attack. Chittering rather than roaring. |
| `jumpscare_whisper` | `creature/jumpscare_whisper.flac` | 2.97s | Epic Stock Media | An aggressive distorted whisper. A jump scare — use it once or not at all. |
| `monster_roar` | `creature/monster_roar.flac` | 3.16s | Mixkit (Envato) | The largest of the roars: layered and clearly designed. Titles and trailers. |
| `orc_attack` | `creature/orc_attack.flac` | 2.54s | Epic Stock Media | A humanoid creature attacking. Fantasy. |
| `raptor_call` | `creature/raptor_call.flac` | 1.78s | 344 Audio | A raptor. Designed; do not use it in a film that claims to know what one sounded like. |
| `sea_beast_yell` | `creature/sea_beast_yell.flac` | 4.90s | Epic Stock Media | A long pained yell from something large and aquatic. |
| `tentacle_wobble` | `creature/tentacle_wobble.flac` | 2.04s | Epic Stock Media | A modulated creature movement with no animal in it. Abstract enough for a graphic. |
| `trex_roar` | `creature/trex_roar.flac` | 5.59s | 344 Audio | The big dinosaur roar. Five seconds, and unmistakable. |
| `venom_squirt` | `creature/venom_squirt.flac` | 0.57s | Mixkit (Envato) | A designed venom spray. Wholly synthetic — do not use it in a wildlife film as if it were a recording. |
| `werewolf_growl` | `creature/werewolf_growl.flac` | 5.73s | Epic Stock Media | A menacing designed growl. Folklore and horror. |
| `zombie_breath` | `creature/zombie_breath.flac` | 1.79s | Mixkit (Envato) | One rasping breath. Quieter and more effective than zombie_roar. |
| `zombie_roar` | `creature/zombie_roar.flac` | 0.94s | Mixkit (Envato) | A short zombie snarl. Horror and Halloween material. |

## crowd

| id | file | length | source | use |
|---|---|---|---|---|
| `applause_light` | `crowd/applause_light.flac` | 9.80s | Mixkit (Envato) | Polite applause in a large space. The restrained one — for a conference, an award or a result. |
| `club_cheer_applause` | `crowd/club_cheer_applause.flac` | 26.86s | Sonik Sound Library | Fifty people cheering and applauding in a small club, recorded in 5.1 and folded to stereo. Warmer and closer than applause_light. |
| `crowd_cheer_applause` | `crowd/crowd_cheer_applause.flac` | 10.30s | Mixkit (Envato) | A small group cheering. Ten seconds, so it can run under a whole celebration shot. |
| `crowd_dense_exterior_loop` | `crowd/crowd_dense_exterior_loop.flac` | 30.00s | Sonik Sound Library | A dense group of adults outdoors before a show. The busiest walla here — individual voices are audible. |
| `crowd_laugh` | `crowd/crowd_laugh.flac` | 4.05s | Mixkit (Envato) | An audience laughing. For a joke that landed, or an archive clip that needs a room around it. |
| `crowd_laugh_applause` | `crowd/crowd_laugh_applause.flac` | 5.64s | Mixkit (Envato) | Laughter turning into applause. A small room — a lecture theatre rather than a stadium. |
| `kids_playing` | `crowd/kids_playing.flac` | 6.85s | Mixkit (Envato) | Children playing and laughing. Shorter than playground_loop and closer to the camera. |
| `kids_screaming` | `crowd/kids_screaming.flac` | 3.82s | Mixkit (Envato) | Children shrieking with excitement. Loud and unmistakably play, not distress. |
| `school_playground_loop` | `crowd/school_playground_loop.flac` | 30.00s | Epic Stock Media | Two and a half minutes of a school playground, looped to thirty seconds. Bigger and further off than playground_loop. |
| `sports_match_loop` | `crowd/sports_match_loop.flac` | 30.00s | Sonic Bat | An indoor volleyball match with its crowd. The bed for amateur and school sport. |
| `sports_match_regional_loop` | `crowd/sports_match_regional_loop.flac` | 30.00s | Sonic Bat | A bigger regional match: more crowd, more room. Six and a half minutes of source, so the window had plenty to choose from. |
| `walla_nigerian_busy_loop` | `crowd/walla_nigerian_busy_loop.flac` | 30.00s | TheWorkRoom | The busier Nigerian walla: more voices, more movement. |
| `walla_nigerian_loop` | `crowd/walla_nigerian_loop.flac` | 30.00s | TheWorkRoom | Crowd walla recorded in Nigeria. The library's wallas were British and Spanish until now; a film set somewhere should be able to sound like it. |
| `walla_nigerian_short` | `crowd/walla_nigerian_short.flac` | 10.14s | TheWorkRoom | The ten-second take from the same session, for a cutaway. |
| `walla_south_african_alt_loop` | `crowd/walla_south_african_alt_loop.flac` | 30.00s | TheWorkRoom | A second South African take. Use it where the first has already been heard. |
| `walla_south_african_loop` | `crowd/walla_south_african_loop.flac` | 30.00s | TheWorkRoom | Crowd walla recorded in South Africa. |

## drone

| id | file | length | source | use |
|---|---|---|---|---|
| `bass_pulse_loop` | `drone/bass_pulse_loop.flac` | 14.00s | Mixkit (Envato) | Slow pulsing low end under an unresolved passage. Musical in a way drone_low_loop is not, so do not run it under a music bed. |
| `drone_low_loop` | `drone/drone_low_loop.flac` | 28.00s | Epic Stock Media | Tension under a serious passage where music would be too much. A steady dark tonal layer, designed as a loop by its author. |
| `electric_buzz_loop` | `drone/electric_buzz_loop.flac` | 6.00s | InMotionAudio | An electrical buzz. Shorter and harsher than electric_hum_loop — a fault rather than a floor. |
| `electric_hum_loop` | `drone/electric_hum_loop.flac` | 24.00s | 344 Audio | Mains hum picked up off a lightbulb coil. The electrical layer under an interior — also the honest sound for a film about the grid. |
| `factory_roomtone_loop` | `drone/factory_roomtone_loop.flac` | 30.00s | Epic Stock Media | A factory's room tone: heavy machinery, a vent, and a dark tonal floor. The industrial interior bed. |
| `fridge_hum_rhythmic_loop` | `drone/fridge_hum_rhythmic_loop.flac` | 24.00s | Jake Fielding | An electric fridge with a rhythm in its buzz. Rougher than room_tone_hum_loop, and the rhythm is audible — use it where a kitchen or a shop is the subject. |
| `industrial_dark_loop` | `drone/industrial_dark_loop.flac` | 24.00s | Cinematic Sound Design | A dark industrial bed. Between machine_hum_loop and drone_low_loop: mechanical, but with unease in it. |
| `machine_hum_loop` | `drone/machine_hum_loop.flac` | 24.00s | Mixkit (Envato) | Factory and plant-room interiors. A steady mechanical hum with no cycle audible in it — also usable as an oppressive room tone under an interview about work. |
| `metal_bangs_loop` | `drone/metal_bangs_loop.flac` | 30.00s | 344 Audio | Banging on metal doors, three minutes of it. Institutional, industrial or frightening depending on what you put it under. |
| `metal_bowed_screech` | `drone/metal_bowed_screech.flac` | 20.55s | The Noisery | Twenty seconds of bowed metal screeching into reverb. Tonal, dissonant, and effective under something going wrong. |
| `metal_scrape_low` | `drone/metal_scrape_low.flac` | 6.32s | The Noisery | A low metal scrape with real sub content. Check it on speakers that can reproduce it. |
| `noise_box_hit` | `drone/noise_box_hit.flac` | 12.27s | InMotionAudio | A struck noise box, ringing for fifteen seconds. An eerie one-shot with a very long decay — give it room. |
| `noise_box_hit_alt` | `drone/noise_box_hit_alt.flac` | 12.84s | InMotionAudio | The second noise-box hit. Alternate them so a repeated scare does not repeat exactly. |
| `radio_static_loop` | `drone/radio_static_loop.flac` | 18.00s | Epic Stock Media | Ham-radio static, powered on and garbled. Communications, isolation and the history of broadcast. |
| `sub_bed_loop` | `drone/sub_bed_loop.flac` | 22.00s | generated | Adds weight under any other bed. Felt more than heard; check on a full-range system. |
| `suspense_waiting_loop` | `drone/suspense_waiting_loop.flac` | 24.00s | Mixkit (Envato) | The televised-suspense cue: a ticking musical loop for a result about to be revealed. It is music, not texture — use it instead of a score, never under one. |
| `tech_hum_loop` | `drone/tech_hum_loop.flac` | 4.00s | Mixkit (Envato) | Server rooms, labs and anywhere the subject is a machine thinking. A short tonal loop: repeat it as long as the passage needs. |
| `wood_hits_dark` | `drone/wood_hits_dark.flac` | 45.00s | 344 Audio | Heavy wooden hits in a dark space, the first forty-five seconds of a three-minute take. Not a loop: the hits are sparse enough that a wrap announces itself (63.7 dB event prominence when it was tried), which is the same reason the library has no ticking-clock bed. Lay it under a passage and fade it. |

## foley

| id | file | length | source | use |
|---|---|---|---|---|
| `atm_key_press` | `foley/atm_key_press.flac` | 0.49s | Mixkit (Envato) | A cash-machine key press. Mechanical and public — different in character from a soft UI click. |
| `barber_chair_pump` | `foley/barber_chair_pump.flac` | 7.32s | 344 Audio | A barber's chair being pumped up. Trades, high streets and small business. |
| `board_pieces_reset` | `foley/board_pieces_reset.flac` | 1.03s | Epic Stock Media | Wooden game pieces being reset. Small, organic and very readable as a table. |
| `book_page_turns_slow` | `foley/book_page_turns_slow.flac` | 13.72s | 344 Audio | Fourteen seconds of slow, deliberate page turns. Long enough to lay under a whole archive shot. |
| `book_pages_flick` | `foley/book_pages_flick.flac` | 1.71s | 344 Audio | Flicking through a book. Archives, research and libraries — the sound of someone looking for something. |
| `books_falling` | `foley/books_falling.flac` | 1.27s | 344 Audio | A pile of books going over. A small disaster; also a comic beat. |
| `broom_sweeping` | `foley/broom_sweeping.flac` | 5.70s | 344 Audio | Sweeping debris off a hard floor. Work, closing time, aftermath. |
| `campfire_extinguished` | `foley/campfire_extinguished.flac` | 30.78s | Ivo Vicic | A fire put out with water from a bottle. Thirty seconds of hiss and steam — the end of an evening, and an unusually specific cue. |
| `carabiner_lock` | `foley/carabiner_lock.flac` | 2.88s | 344 Audio | A carabiner gate screwed shut. Climbing, safety and rigging. |
| `carabiner_rope` | `foley/carabiner_rope.flac` | 6.23s | 344 Audio | A carabiner flicked along a rope. |
| `card_flip` | `foley/card_flip.flac` | 0.17s | Epic Stock Media | One card flipped. Retrigger it for a hand. |
| `card_shuffler` | `foley/card_shuffler.flac` | 5.10s | 344 Audio | An automatic card shuffler. Gambling, chance and randomness. |
| `cards_dealing` | `foley/cards_dealing.flac` | 6.90s | 344 Audio | Cards being dealt. Seven seconds — enough for a whole hand. |
| `cards_pick_up` | `foley/cards_pick_up.flac` | 0.38s | 344 Audio | Picking cards up off a table. A third of a second. |
| `case_close` | `foley/case_close.flac` | 0.45s | InMotionAudio | An instrument case closed. Music, travel, packing up. |
| `case_handle` | `foley/case_handle.flac` | 0.88s | InMotionAudio | A case handle moving. Small and specific. |
| `case_set_down` | `foley/case_set_down.flac` | 0.81s | InMotionAudio | A case put down on concrete. An arrival, or the end of a journey. |
| `cladding_scratch` | `foley/cladding_scratch.flac` | 1.55s | InMotionAudio | A longer scrape across the same surface. |
| `clock_ticking` | `foley/clock_ticking.flac` | 59.76s | 344 Audio | A minute of an antique clock. Deliberately **not** loopable: a metronomic event inside a loop announces the wrap on every cycle, which is why the library has tick_light as a single tick. Lay this under a passage and fade it. |
| `cloth_bag_flip` | `foley/cloth_bag_flip.flac` | 0.66s | Epic Stock Media | A canvas bag flipped open. Light textile movement. |
| `cloth_movement` | `foley/cloth_movement.flac` | 2.16s | InMotionAudio | Cloth moving. The generic clothing-move cue for a person shifting on camera. |
| `cloth_movement_short` | `foley/cloth_movement_short.flac` | 1.47s | InMotionAudio | The shorter cloth move. Retrigger it under a gesture. |
| `cloth_pat` | `foley/cloth_pat.flac` | 0.38s | InMotionAudio | A single pat on clothing. |
| `coin_flip` | `foley/coin_flip.flac` | 0.91s | Cinematic Sound Design | A single coin flip. Chance, decision and money. |
| `cracker_grab` | `foley/cracker_grab.flac` | 0.64s | InMotionAudio | Picking up a cracker. Small paper handling. |
| `cracker_pull` | `foley/cracker_pull.flac` | 0.22s | InMotionAudio | A Christmas cracker pulled, with the bang. Half a second. |
| `doorbell` | `foley/doorbell.flac` | 0.97s | Mixkit (Envato) | Someone at the door. Domestic arrival, one press. |
| `dry_ice_squeak` | `foley/dry_ice_squeak.flac` | 2.10s | Epic Stock Media | The small squeak from the same technique. It reads as a mouse, which is what its author called it. |
| `dry_ice_squeal` | `foley/dry_ice_squeal.flac` | 3.92s | Epic Stock Media | Dry ice squealing against metal. Unpleasant on purpose — a texture for tension. |
| `dry_leaves` | `foley/dry_leaves.flac` | 0.57s | Mixkit (Envato) | A handful of dry leaves moving. Small, close and specific — for a cutaway to the forest floor. |
| `fish_in_water` | `foley/fish_in_water.flac` | 2.29s | Mixkit (Envato) | A fish moving just under the surface. Small water movement for an aquaculture or river sequence. |
| `flip_flops_walking` | `foley/flip_flops_walking.flac` | 39.17s | TheWorkRoom | Forty seconds of someone walking in flip-flops. Hot climates, beaches, home — footwear does a lot of characterisation. |
| `flip_flops_walking_alt` | `foley/flip_flops_walking_alt.flac` | 31.36s | TheWorkRoom | A second flip-flop walk at a different pace. |
| `footsteps_grass_loop` | `foley/footsteps_grass_loop.flac` | 18.00s | Mixkit (Envato) | Walking through long grass. The movement bed for a field sequence — under a walking shot it does what a music cue would otherwise have to. |
| `game_piece_drop` | `foley/game_piece_drop.flac` | 0.22s | Epic Stock Media | A single piece falling and bouncing. A fifth of a second. |
| `hair_brushing` | `foley/hair_brushing.flac` | 6.00s | 344 Audio | Brushing hair with a plastic brush. Close and domestic. |
| `hair_dryer` | `foley/hair_dryer.flac` | 12.31s | 344 Audio | On, idle and off in one twelve-second take. A whole gesture rather than a loop. |
| `hair_trimmer` | `foley/hair_trimmer.flac` | 44.68s | 344 Audio | Forty-five seconds of clippers: on, idle, off. Bed level because it runs under a shot rather than landing on a frame. |
| `hydraulic_door` | `foley/hydraulic_door.flac` | 1.66s | Mixkit (Envato) | The pneumatic hiss of a bus door. Places a shot in public transport in under two seconds. |
| `ice_crack` | `foley/ice_crack.flac` | 4.59s | Alexander Kopeikin | Ice snapping. The sharp one — a cue that lands on a frame. |
| `ice_crushed` | `foley/ice_crushed.flac` | 2.57s | Alexander Kopeikin | A block of ice crushed. Recorded at 100 kHz, so it holds up to pitching down. |
| `ice_fissure` | `foley/ice_fissure.flac` | 3.43s | Alexander Kopeikin | A fissure running fast through ice. Five seconds, and it moves. |
| `keyboard_typing_loop` | `foley/keyboard_typing_loop.flac` | 20.00s | Mixkit (Envato) | Someone working at a keyboard. A bed, not a per-character trigger — type_key is the one you retrigger. |
| `latch_thunk` | `foley/latch_thunk.flac` | 0.35s | Epic Stock Media | A deep latch closing. The mechanical full stop. |
| `laundry_basket` | `foley/laundry_basket.flac` | 5.03s | InMotionAudio | A washing basket patted and moved. Domestic routine. |
| `laundry_basket_short` | `foley/laundry_basket_short.flac` | 1.60s | InMotionAudio | The short version of laundry_basket. For a single move. |
| `lock_tinkering` | `foley/lock_tinkering.flac` | 3.96s | 344 Audio | Fiddling with an old lock. Four seconds of it — use it where a single click would be too neat. |
| `measuring_tape` | `foley/measuring_tape.flac` | 0.61s | 344 Audio | An antique measuring tape. Craft, making and precision. |
| `metal_box_drag` | `foley/metal_box_drag.flac` | 67.11s | 344 Audio | Sixty-seven seconds of a large metal box dragged across ground, recorded with a geophone. Heavy industry, and a striking texture in its own right. |
| `metal_door_close` | `foley/metal_door_close.flac` | 1.37s | Mixkit (Envato) | A heavy metal door closing. Industrial, institutional, or the end of an act. |
| `metal_hatch_open` | `foley/metal_hatch_open.flac` | 1.65s | Mixkit (Envato) | A metal hatch being opened. The opening counterpart to metal_door_close. |
| `metal_lid_open` | `foley/metal_lid_open.flac` | 1.20s | 344 Audio | A metal lid coming off a blowtorch. Small, mechanical, specific. |
| `metal_tap` | `foley/metal_tap.flac` | 0.37s | Epic Stock Media | A light metallic tap. Half a second. |
| `mic_hit` | `foley/mic_hit.flac` | 0.87s | Mixkit (Envato) | Something striking a live microphone. For an interview cutaway or as an honest imperfection at the top of a piece. |
| `motorcycle_mount` | `foley/motorcycle_mount.flac` | 13.00s | SoundBits | Getting on a bike and putting a helmet on. Fourteen seconds of preparation — the human half of a riding sequence. |
| `nail_scratch` | `foley/nail_scratch.flac` | 0.96s | InMotionAudio | A nail dragged down cladding. Deliberately unpleasant. |
| `newspaper_rummage` | `foley/newspaper_rummage.flac` | 4.42s | Cinematic Sound Design | Rummaging through a newspaper. Journalism, archives and the press. |
| `paper_a4_turn` | `foley/paper_a4_turn.flac` | 1.26s | Cinematic Sound Design | An A4 sheet turned, with a rattle and a tail. Office and print subjects — cleaner than page_change. |
| `paper_hat_movement` | `foley/paper_hat_movement.flac` | 1.62s | InMotionAudio | Thin paper being handled. A party hat, and a good generic light-paper move. |
| `pipe_on_concrete` | `foley/pipe_on_concrete.flac` | 0.44s | InMotionAudio | A metal pipe against concrete. Small, hard and industrial. |
| `punch_fast` | `foley/punch_fast.flac` | 1.07s | Mixkit (Envato) | A fast body punch. Sport, conflict and anywhere the picture shows contact. |
| `punch_light` | `foley/punch_light.flac` | 1.05s | Epic Stock Media | A light quick punch. The small version of punch_fast. |
| `punches_body` | `foley/punches_body.flac` | 3.60s | 344 Audio | Four body punches in a row. Sport and conflict; retrigger single hits from punch_fast instead if you need them placed. |
| `radio_mode_wheel` | `foley/radio_mode_wheel.flac` | 1.11s | Sonic Bat | The mode wheel on a vintage radio. |
| `radio_power_button` | `foley/radio_power_button.flac` | 0.58s | Sonic Bat | The power button. A mechanical on, not a beep. |
| `radio_tuning_sweep` | `foley/radio_tuning_sweep.flac` | 102.23s | Sonic Bat | A hundred seconds of an AM radio being tuned across the band. Broadcast history, searching, and a striking transition if you cut a few seconds out of it. |
| `sax_case_place` | `foley/sax_case_place.flac` | 0.49s | InMotionAudio | A saxophone placed in its case. Music and rehearsal. |
| `sax_handling` | `foley/sax_handling.flac` | 1.42s | InMotionAudio | Picking up and releasing a saxophone. The handling, not the note. |
| `sax_key_press` | `foley/sax_key_press.flac` | 0.46s | InMotionAudio | A single saxophone key. Mechanical, quiet, and the detail a close-up wants. |
| `scissor_snips` | `foley/scissor_snips.flac` | 4.83s | 344 Audio | Scissor snips. The literal cue for cutting, editing or trimming anything. |
| `scratchcard_movement` | `foley/scratchcard_movement.flac` | 2.50s | InMotionAudio | A scratch card handled. Gambling, chance and small stakes. |
| `scratchcard_scratch` | `foley/scratchcard_scratch.flac` | 1.07s | InMotionAudio | Scratching a card with a coin. The literal sound of a reveal. |
| `scratchcard_wipe` | `foley/scratchcard_wipe.flac` | 1.05s | InMotionAudio | Wiping the surface clear. Pairs with scratchcard_scratch. |
| `seat_creak` | `foley/seat_creak.flac` | 4.15s | 344 Audio | Someone sitting down on a creaky seat. Theatres, waiting rooms, old buildings. |
| `shop_door_bell` | `foley/shop_door_bell.flac` | 1.79s | Mixkit (Envato) | The bell over a shop door. Small-business and high-street subjects. |
| `sleigh_pulling` | `foley/sleigh_pulling.flac` | 11.26s | 344 Audio | A sleigh being pulled, bells moving with it. Winter and Christmas subjects. |
| `spray_bottle` | `foley/spray_bottle.flac` | 0.52s | 344 Audio | One spray. Cleaning, gardening, salons. |
| `spring_wire_clatter` | `foley/spring_wire_clatter.flac` | 11.00s | Epic Stock Media | Eleven seconds of spring wire flicked and clattering. A long, odd metallic texture. |
| `stairwell_door` | `foley/stairwell_door.flac` | 6.48s | InMotionAudio | A heavy stairwell door. Institutional buildings — hospitals, hotels, offices. |
| `suitcase_lock` | `foley/suitcase_lock.flac` | 8.95s | 344 Audio | An old case being locked. A closing beat. |
| `suitcase_open_close` | `foley/suitcase_open_close.flac` | 7.47s | 344 Audio | Opening and closing in one take, so you can cut either half out of it. |
| `suitcase_rummage` | `foley/suitcase_rummage.flac` | 8.51s | 344 Audio | Searching through an old suitcase. History, migration and personal-archive subjects. |
| `tape_measure_retract` | `foley/tape_measure_retract.flac` | 3.26s | Epic Stock Media | A tape measure snapping back. Making, building and measurement. |
| `tarot_shuffle` | `foley/tarot_shuffle.flac` | 4.18s | Epic Stock Media | A heavy deck shuffled. Slower and thicker than card_shuffler. |
| `telephone_earpiece_drop` | `foley/telephone_earpiece_drop.flac` | 1.21s | 344 Audio | An old telephone earpiece falling. The sound of a call ending badly. |
| `telephone_earpiece_handle` | `foley/telephone_earpiece_handle.flac` | 3.22s | 344 Audio | Picking up and putting down an antique handset. |
| `telephone_rotary_dial` | `foley/telephone_rotary_dial.flac` | 2.44s | 344 Audio | One digit dialled on a rotary telephone. Archive and mid-century subjects; retrigger it per digit. |
| `telephone_vintage` | `foley/telephone_vintage.flac` | 6.44s | Mixkit (Envato) | An old telephone ringing. Archive and historical sequences; also the honest sound for a call that is being dramatised. |
| `toilet_flush` | `foley/toilet_flush.flac` | 9.81s | InMotionAudio | A toilet flushing. Domestic and public interiors; also the plainest available water-plumbing cue. |
| `toy_whistle` | `foley/toy_whistle.flac` | 1.14s | Mixkit (Envato) | A toy slide whistle. Children, play and comic beats only. |
| `typewriter_carriage` | `foley/typewriter_carriage.flac` | 3.55s | 344 Audio | The carriage moving. Pairs with type_key, which is the key itself. |
| `typewriter_paper` | `foley/typewriter_paper.flac` | 2.99s | 344 Audio | Paper going in and out of a typewriter. |
| `velcro_rip` | `foley/velcro_rip.flac` | 0.31s | InMotionAudio | A velcro rip. Sport, medical and equipment handling. |
| `velcro_squeeze` | `foley/velcro_squeeze.flac` | 6.43s | InMotionAudio | Velcro pressed and squeezed slowly. The long version. |
| `wire_car_alt_loop` | `foley/wire_car_alt_loop.flac` | 24.00s | TheWorkRoom | A second wire-car take. |
| `wire_car_loop` | `foley/wire_car_loop.flac` | 24.00s | TheWorkRoom | A wire car — the handmade toy — being pushed. Craft, childhood and improvisation; a specific and unusual texture. |
| `wood_roll` | `foley/wood_roll.flac` | 4.12s | Epic Stock Media | Wood rolling across a table. Grainy and close. |
| `wrapping_paper` | `foley/wrapping_paper.flac` | 5.58s | 344 Audio | Opening a wrapped present. Gift, retail and festive sequences. |

## human

| id | file | length | source | use |
|---|---|---|---|---|
| `blow_breath` | `human/blow_breath.flac` | 1.06s | Mixkit (Envato) | A longer sustained blow. Wind instruments, hot drinks, cold hands. |
| `blow_fast` | `human/blow_fast.flac` | 0.34s | Mixkit (Envato) | A fast puff of air. Blowing out a candle, clearing dust, or a small comic exhale. |
| `breath_annoyed` | `human/breath_annoyed.flac` | 0.94s | Epic Stock Media | An annoyed breath. A reaction with no words in it. |
| `breath_artificial` | `human/breath_artificial.flac` | 1.07s | Mixkit (Envato) | A single processed breath — through a mask, a machine or a helmet. Medical or science-fiction, not a person in a room. |
| `chewing_crunchy` | `human/chewing_crunchy.flac` | 0.50s | Mixkit (Envato) | One crunchy bite. Food, agriculture and anything about what people actually eat. |
| `child_babble` | `human/child_babble.flac` | 2.16s | Mixkit (Envato) | A small child talking without words. Family, early-years and language subjects; no language to date or place it. |
| `child_breath` | `human/child_breath.flac` | 1.63s | Mixkit (Envato) | A child taking a deep breath. Use it before something a child is about to do. |
| `child_laugh` | `human/child_laugh.flac` | 0.69s | Mixkit (Envato) | A short child's laugh. The lightest human sound in the library. |
| `child_laugh_happy` | `human/child_laugh_happy.flac` | 2.22s | Mixkit (Envato) | A longer, more delighted child's laugh. Two and a half seconds, so it can carry a cutaway. |
| `cough_hard` | `human/cough_hard.flac` | 1.34s | Mixkit (Envato) | A hard chest cough. Use it once; a run of these turns a serious subject into an impression of one. |
| `cough_loud` | `human/cough_loud.flac` | 0.96s | Mixkit (Envato) | A loud, unguarded cough. The most foreground of the coughs — it will pull focus. |
| `cough_male_alt` | `human/cough_male_alt.flac` | 1.13s | Epic Stock Media | A third male cough, drier than cough_man. Useful when a scene needs two different people coughing. |
| `cough_man` | `human/cough_man.flac` | 0.84s | Mixkit (Envato) | A plain single cough. Health, air-quality and occupational-illness subjects — the everyday one. |
| `cough_sick` | `human/cough_sick.flac` | 0.85s | Mixkit (Envato) | A weaker cough from someone genuinely unwell. Quieter and more honest than cough_loud for a medical piece. |
| `cough_very_sick` | `human/cough_very_sick.flac` | 1.57s | Mixkit (Envato) | A three-second coughing fit. Strong material — reserve it for a film about respiratory illness. |
| `cough_woman` | `human/cough_woman.flac` | 0.96s | Mixkit (Envato) | A woman's cough. The library needs it for the same reason it needs cough_man: half of everyone. |
| `cough_young_man` | `human/cough_young_man.flac` | 1.14s | Mixkit (Envato) | A younger voice coughing. Use it where cough_man would read as older than the person on screen. |
| `crying_female` | `human/crying_female.flac` | 4.27s | SoundBits | Four seconds of crying. Performed; handle it the way you would handle any depiction of grief. |
| `eating_man` | `human/eating_man.flac` | 0.97s | Mixkit (Envato) | Hurried eating. Close and unglamorous — which is usually the point when a film shows a meal. |
| `exclaim_pain` | `human/exclaim_pain.flac` | 1.12s | Mixkit (Envato) | A short exclamation of pain. Injury and accident sequences — brief, and not graphic. |
| `gasp_female` | `human/gasp_female.flac` | 0.68s | Mixkit (Envato) | A sharp gasp of astonishment. For a reveal seen by someone, not by the audience. |
| `gasp_male` | `human/gasp_male.flac` | 0.35s | Epic Stock Media | A male gasp of shock. The counterpart to gasp_female. |
| `kiss_big` | `human/kiss_big.flac` | 0.68s | Mixkit (Envato) | A big, unmistakable kiss. Comic or affectionate, never subtle. |
| `kiss_double` | `human/kiss_double.flac` | 0.36s | Mixkit (Envato) | Two quick kisses — the cheek-kiss greeting rather than a romantic one. |
| `kiss_long` | `human/kiss_long.flac` | 0.84s | Mixkit (Envato) | A longer kiss. Romance, reunion, farewell. |
| `kiss_loving` | `human/kiss_loving.flac` | 0.88s | Mixkit (Envato) | Softer than kiss_long, and closer to the microphone. |
| `kiss_small` | `human/kiss_small.flac` | 0.23s | Mixkit (Envato) | A quick peck. The one to use on a greeting. |
| `laugh_female` | `human/laugh_female.flac` | 0.71s | SoundBits | A short female laugh. The counterpart to laugh_male. |
| `laugh_male` | `human/laugh_male.flac` | 1.69s | Epic Stock Media | A short male laugh. The library had children and crowds laughing but no single adult. |
| `lips_smacking` | `human/lips_smacking.flac` | 2.72s | Mixkit (Envato) | Lips smacking in sleep. A small, uncomfortably intimate detail — use it deliberately. |
| `pain_grunt_female` | `human/pain_grunt_female.flac` | 2.00s | Epic Stock Media | A pained grunt. Injury, effort or collapse. |
| `panting_male` | `human/panting_male.flac` | 8.95s | SoundBits | Nine seconds of heavy panting. Exertion, sport, fear — the longest human effort here. |
| `scream_pain` | `human/scream_pain.flac` | 0.98s | Mixkit (Envato) | A pained scream from a fight. Strong material; use it where the film has earned it. |
| `sleep_breath` | `human/sleep_breath.flac` | 5.15s | Mixkit (Envato) | Five seconds of slow sleeping breath. Quiet enough to lay under a night shot as a bed. |
| `sneeze_baby` | `human/sneeze_baby.flac` | 0.68s | Mixkit (Envato) | An infant sneeze. Small and immediately readable as a very young child. |
| `sneeze_man` | `human/sneeze_man.flac` | 0.75s | Mixkit (Envato) | A single sneeze. Colds, allergies, pollen and dust. |
| `snore` | `human/snore.flac` | 1.41s | Mixkit (Envato) | A single strong snore. Sleep, fatigue and night sequences. |
| `struggle_choking` | `human/struggle_choking.flac` | 8.70s | 344 Audio | Nine seconds of strained struggle. Strong material; it is performed, and it will read as violence. |
| `throat_clear` | `human/throat_clear.flac` | 1.48s | Mixkit (Envato) | Clearing the throat before speaking. The sound of someone about to say something difficult. |
| `tribal_voice` | `human/tribal_voice.flac` | 4.08s | Mixkit (Envato) | A chanted ritual voice. It carries a strong cultural signal — only use it where the film is about that culture and can say which. |
| `voice_no_no_no` | `human/voice_no_no_no.flac` | 1.60s | Mixkit (Envato) | A performed "no, no, no". It is a comic line reading, not a sound effect — treat it as dialogue. |
| `waking_up` | `human/waking_up.flac` | 6.11s | Mixkit (Envato) | Someone surfacing in the morning: breath, movement, a groan. Six seconds, so it can carry a whole cutaway. |
| `weeping_female` | `human/weeping_female.flac` | 2.59s | Epic Stock Media | Low-energy weeping. Performed; strong material and easy to misuse. |
| `whistle_human` | `human/whistle_human.flac` | 2.44s | SoundBits | Someone whistling. Casual, human and instantly readable as a person off camera. |
| `yawn_cartoon` | `human/yawn_cartoon.flac` | 1.27s | Mixkit (Envato) | A performed, comic yawn. Only for a piece that is already playing for laughs. |
| `yawn_deep` | `human/yawn_deep.flac` | 1.45s | Mixkit (Envato) | A long, deep yawn. Exhaustion rather than boredom. |
| `yawn_quiet` | `human/yawn_quiet.flac` | 3.79s | Mixkit (Envato) | A yawn someone is trying to hide. Works under narration where yawn_tired would not. |
| `yawn_short` | `human/yawn_short.flac` | 1.13s | Mixkit (Envato) | The shortest yawn here. For a beat about boredom or fatigue that must not overstay. |
| `yawn_stretch` | `human/yawn_stretch.flac` | 2.15s | Mixkit (Envato) | Yawn and stretch together, with the body in it. Pairs with waking_up. |
| `yawn_tired` | `human/yawn_tired.flac` | 2.08s | Mixkit (Envato) | An audible tired yawn. The default of the five. |

## impact

| id | file | length | source | use |
|---|---|---|---|---|
| `bass_downer_fast` | `impact/bass_downer_fast.flac` | 2.10s | 344 Audio | A fast bass drop. The short one, for a cut that falls away. |
| `bass_downer_fast_alt` | `impact/bass_downer_fast_alt.flac` | 2.64s | 344 Audio | A second fast downer with a different shape. Alternate it with bass_downer_fast so a run of them does not repeat. |
| `bass_downer_medium` | `impact/bass_downer_medium.flac` | 4.03s | 344 Audio | The middle bass downer. The default of the six. |
| `bass_downer_medium_alt` | `impact/bass_downer_medium_alt.flac` | 3.54s | 344 Audio | The alternate medium downer. |
| `bass_downer_rattling` | `impact/bass_downer_rattling.flac` | 5.53s | 344 Audio | A downer with rattle in it. Rougher and more mechanical than the clean drops. |
| `bass_downer_slow` | `impact/bass_downer_slow.flac` | 4.47s | 344 Audio | The slow one: four and a half seconds of descent. For a section ending, not a cut. |
| `bass_downer_tonal` | `impact/bass_downer_tonal.flac` | 5.82s | 344 Audio | A tonal downer with a long reverb tail. It has a pitch, so check it against any music underneath. |
| `bass_drop_jump_start` | `impact/bass_drop_jump_start.flac` | 3.34s | 344 Audio | A drop that starts rather than ends — it jolts into place. Use it on an opening cut. |
| `braam_creeping` | `impact/braam_creeping.flac` | 3.76s | Cinematic Sound Design | A slow, creeping braam. Between braam_soft and braam_horn in weight, and darker than both. |
| `braam_horn` | `impact/braam_horn.flac` | 9.76s | Jake Fielding | The big version of braam_soft: a real horn section, dark and wide. For a title or a single gravest point — one per film, not one per chapter. |
| `braam_huge` | `impact/braam_huge.flac` | 11.52s | Jake Fielding | The largest horn braam in the library: fourteen seconds, dark and wide. One per film, and only for the title. |
| `braam_short` | `impact/braam_short.flac` | 3.95s | local-renders | A four-second braam: the middle size between braam_soft and braam_horn. For a grave point that is not the film's gravest. |
| `braam_soft` | `impact/braam_soft.flac` | 4.50s | generated | Documentary accent for a grave point. A restrained braam, not a trailer hit. |
| `cartoon_airy_impact` | `impact/cartoon_airy_impact.flac` | 2.25s | Cinematic Sound Design | A light impact with creaks in it. Softer than any of the cinematic hits. |
| `explosion_small` | `impact/explosion_small.flac` | 1.48s | Epic Stock Media | A small designed blast. Cartoonish rather than documentary. |
| `gore_smash` | `impact/gore_smash.flac` | 1.63s | Epic Stock Media | A heavy wet smash. Designed horror; unpleasant, and meant to be. |
| `ice_shatter` | `impact/ice_shatter.flac` | 2.46s | Epic Stock Media | A freeze-and-shatter. Two and a half seconds, and it breaks in the middle. |
| `impact_chord` | `impact/impact_chord.flac` | 2.82s | Cinematic Sound Design | An impact with a chord in it. Tonal — check it against any music underneath. |
| `impact_cinematic` | `impact/impact_cinematic.flac` | 9.21s | Federico Soler | Title cards and act breaks. A trailer boom with a long tail: leave it room, do not cut it short. |
| `impact_electric` | `impact/impact_electric.flac` | 3.64s | Epic Stock Media | An electric impact with movement in it. For a graphic that arrives with a charge. |
| `impact_hit` | `impact/impact_hit.flac` | 1.72s | local-renders | A dry two-second hit with almost no tail. The everyday accent for a card landing, where impact_soft would ring. |
| `impact_laser_thunder` | `impact/impact_laser_thunder.flac` | 4.64s | Mixkit (Envato) | A synthetic strike with a thunder tail. Fits science and technology subjects where a horn braam would be too romantic. |
| `impact_soft` | `impact/impact_soft.flac` | 4.79s | The Noisery | Weight under a lower third or a card landing. The everyday impact: a real metal thud with a short low ring, no trailer tail. |
| `impact_submerge` | `impact/impact_submerge.flac` | 2.08s | Epic Stock Media | Something going under water. Weight plus bubbles in two seconds. |
| `impact_trailer` | `impact/impact_trailer.flac` | 4.14s | Mixkit (Envato) | A trailer hit with a six-second tail. Title cards and act breaks where impact_cinematic has already been used. |
| `impact_trailer_epic` | `impact/impact_trailer_epic.flac` | 3.03s | Mixkit (Envato) | The biggest impact in the library: a deep boom with a wide tail. For an opening title, not a chapter heading. |
| `splat_small` | `impact/splat_small.flac` | 0.33s | Epic Stock Media | A small wet splat. A third of a second. |
| `sub_drop` | `impact/sub_drop.flac` | 6.88s | 344 Audio | Section change or a hard stop. A designed bass downer — check it on speakers that can reproduce 40 Hz. |
| `trailer_boom_a` | `impact/trailer_boom_a.flac` | 9.93s | Federico Soler | A trailer boom with a long tail. The sibling of impact_cinematic from the same library. |
| `trailer_boom_b` | `impact/trailer_boom_b.flac` | 12.69s | Federico Soler | The second boom. Alternate them across a film so no two act breaks sound the same. |
| `water_impact_scifi` | `impact/water_impact_scifi.flac` | 1.15s | 344 Audio | A designed liquid hit. Synthetic — for a graphic, not for a river. |

## instrument

| id | file | length | source | use |
|---|---|---|---|---|
| `bell_bright` | `instrument/bell_bright.flac` | 3.20s | local-renders | A high bell around 1 kHz. Cuts through a busy mix, so use it where bell_low_dry disappears. |
| `bell_low_dry` | `instrument/bell_low_dry.flac` | 2.97s | local-renders | A low bell struck once and damped — half a second of ring. A section mark for a contemplative film, where a UI chime would be wrong. |
| `bell_low_long` | `instrument/bell_low_long.flac` | 4.79s | local-renders | The same low bell left to ring for two seconds. For the end of a chapter rather than the start of one. |
| `chime_bar` | `instrument/chime_bar.flac` | 3.87s | local-renders | A struck chime bar near 1.6 kHz. Bright and tuned — for a figure resolving or a small good thing happening. |
| `chime_bar_bright` | `instrument/chime_bar_bright.flac` | 4.49s | local-renders | The glassier chime bar: same pitch, more air above it. Sits over narration without masking it. |
| `echo_swell` | `instrument/echo_swell.flac` | 9.78s | Mixkit (Envato) | A tonal swell into echo. Between an instrument and a transition — use it where a riser would be too obvious. |
| `gong_long` | `instrument/gong_long.flac` | 4.99s | local-renders | The longest gong: over a second of decay. Give it silence on either side or it reads as noise. |
| `gong_mid` | `instrument/gong_mid.flac` | 3.95s | local-renders | The middle gong. For a transition in a film about practice, ritual or slowness. |
| `gong_small` | `instrument/gong_small.flac` | 4.59s | local-renders | A small gong, under a second of decay. The gentlest of the three — a beat, not a full stop. |
| `guitar_body_hit` | `instrument/guitar_body_hit.flac` | 0.55s | Mixkit (Envato) | A percussive hit on the guitar body. A dry accent with a wooden character. |
| `guitar_chords_happy` | `instrument/guitar_chords_happy.flac` | 0.97s | Mixkit (Envato) | A short major figure. A resolution cue for a light piece — over in just over a second. |
| `guitar_nylon_note` | `instrument/guitar_nylon_note.flac` | 3.51s | Mixkit (Envato) | A single nylon-string note. The softest guitar sound here — it will sit under speech. |
| `guitar_riff` | `instrument/guitar_riff.flac` | 1.35s | Mixkit (Envato) | A brief riff with attitude. It is a musical statement, so it will fight a score. |
| `guitar_slide_bass` | `instrument/guitar_slide_bass.flac` | 3.57s | Mixkit (Envato) | A slide on the bass string. Lower and shorter — a full stop rather than a journey. |
| `guitar_slide_noise` | `instrument/guitar_slide_noise.flac` | 6.52s | Mixkit (Envato) | Finger noise on wound strings, no note. Texture for a montage about craft or practice. |
| `guitar_slide_western` | `instrument/guitar_slide_western.flac` | 5.37s | Mixkit (Envato) | A twanging western slide. It carries a genre with it; only use it where that genre is the subject. |
| `guitar_sliding` | `instrument/guitar_sliding.flac` | 10.09s | Mixkit (Envato) | Ten seconds of sliding on the strings. Usable as a transition in a film that already has guitar in it. |
| `guitar_string_acute` | `instrument/guitar_string_acute.flac` | 0.81s | Mixkit (Envato) | One sharply plucked string. A punctuation mark for an acoustic film. |
| `guitar_string_clink` | `instrument/guitar_string_clink.flac` | 1.04s | Mixkit (Envato) | A string clinking against a fret. The small human detail you put under a close-up of hands. |
| `guitar_string_tone` | `instrument/guitar_string_tone.flac` | 1.02s | Mixkit (Envato) | A plucked string left to sustain. Warmer and rounder than guitar_string_acute. |
| `guitar_stroke_down` | `instrument/guitar_stroke_down.flac` | 7.25s | Mixkit (Envato) | A slow downward strum, allowed to ring out. Opens a scene in an acoustic film. |
| `guitar_stroke_up` | `instrument/guitar_stroke_up.flac` | 5.99s | Mixkit (Envato) | The upward strum. Pairs with guitar_stroke_down across a cut. |
| `guitar_tone_negative` | `instrument/guitar_tone_negative.flac` | 0.55s | Mixkit (Envato) | The guitar equivalent of error_soft: a tone that says no without buzzing. |
| `guitar_tone_quick` | `instrument/guitar_tone_quick.flac` | 0.52s | Mixkit (Envato) | Half a second of guitar. For a bullet or a caption landing in a film with acoustic music. |
| `guitar_tones_melodic` | `instrument/guitar_tones_melodic.flac` | 15.65s | Mixkit (Envato) | Seventeen seconds of melodic guitar — an interlude rather than an effect. Fade it under narration. |
| `kalimba_bright` | `instrument/kalimba_bright.flac` | 5.00s | local-renders | A bright kalimba pluck. Warmer than success_chime and unmistakably played rather than designed. |
| `kalimba_low` | `instrument/kalimba_low.flac` | 4.99s | local-renders | The low kalimba, ringing for a second and a half. The one that can carry a section change on its own. |
| `kalimba_mid` | `instrument/kalimba_mid.flac` | 4.89s | local-renders | A mid-register kalimba note around 525 Hz. Retrigger it for a sequence of points appearing. |
| `music_box_a` | `instrument/music_box_a.flac` | 2.48s | Sonic Bat | A music-box phrase. Childhood, memory and the passing of time — it carries all three whether you want them or not. |
| `music_box_b` | `instrument/music_box_b.flac` | 1.68s | Sonic Bat | A second music box with a different tune. |
| `music_box_c` | `instrument/music_box_c.flac` | 1.82s | Sonic Bat | A third. Rotate the three so a recurring motif does not repeat exactly. |
| `music_box_wind_up` | `instrument/music_box_wind_up.flac` | 1.58s | Sonic Bat | The mechanism being wound. Use it before one of the melodies. |
| `singing_bowl` | `instrument/singing_bowl.flac` | 3.87s | local-renders | A struck singing bowl around 540 Hz. Calm, tuned, and long enough to sit under a fade. |
| `singing_bowl_long` | `instrument/singing_bowl_long.flac` | 5.00s | local-renders | The same bowl held for nearly two seconds. For an ending, or for silence that needs a floor under it. |
| `singing_bowl_low` | `instrument/singing_bowl_low.flac` | 4.87s | local-renders | A low bowl near 130 Hz — felt more than heard on small speakers. Check it on something with bass response. |
| `trumpet_fanfare` | `instrument/trumpet_fanfare.flac` | 5.60s | Mixkit (Envato) | A brass fanfare. Announcement, ceremony, or a deliberately overblown reveal. |

## magic

| id | file | length | source | use |
|---|---|---|---|---|
| `christmas_bells_magic` | `magic/christmas_bells_magic.flac` | 17.47s | 344 Audio | Designed sleigh bells with a shimmer on them. Festive, and openly synthetic. |
| `death_whistle` | `magic/death_whistle.flac` | 1.56s | InMotionAudio | The dry whistle at pitch. The cleanest of the three, and the one to use if the film explains what it is. |
| `death_whistle_distorted` | `magic/death_whistle_distorted.flac` | 1.65s | InMotionAudio | An Aztec death whistle with distortion on it. It sounds like a scream, which is what it was made to do. |
| `death_whistle_loop` | `magic/death_whistle_loop.flac` | 12.00s | InMotionAudio | The death whistle held as a drone. Ritual, folklore and dread — a real instrument, not a synthesiser. |
| `evil_spell_loop` | `magic/evil_spell_loop.flac` | 30.00s | 344 Audio | A designed malevolent ambience. Folklore, horror and myth; it is a composition, so keep music off it. |
| `magic_astonishment` | `magic/magic_astonishment.flac` | 18.58s | Alexander Kopeikin | Nineteen seconds of rising designed energy. For a reveal that is meant to feel impossible. |
| `magic_creation` | `magic/magic_creation.flac` | 2.93s | CB_Sounddesign | Something being conjured into existence. Three seconds, bright. |
| `magic_flow_loop` | `magic/magic_flow_loop.flac` | 24.00s | Alexander Kopeikin | Continuous designed energy flow. A bed for a dream, a memory or a passage that has left the real world. |
| `magic_onslaught` | `magic/magic_onslaught.flac` | 36.56s | Alexander Kopeikin | A designed malevolent gesture, thirty-eight seconds long. It arrives and builds — treat it as a cue, not a texture. |
| `magic_tension_loop` | `magic/magic_tension_loop.flac` | 30.00s | Alexander Kopeikin | A spellbound tension drone. Where drone_low_loop is neutral, this one is telling you something is wrong. |
| `shimmer_bells_loop` | `magic/shimmer_bells_loop.flac` | 24.00s | Epic Stock Media | Small bells and metal taps, shimmering. Delicate, and a rare thing: a magical bed that is not dark. |
| `spell_light` | `magic/spell_light.flac` | 3.56s | Epic Stock Media | A bright enchantment. Five seconds of tonal shimmer. |

## place

| id | file | length | source | use |
|---|---|---|---|---|
| `airfield_loop` | `place/airfield_loop.flac` | 30.00s | Mixkit (Envato) | Airports, heliports and anywhere aviation is the industry on screen. Continuous engine wash with a rotor working through it. |
| `bar_interior_loop` | `place/bar_interior_loop.flac` | 30.00s | Sonic Bat | Ten minutes of a bar interior, looped to thirty seconds. Calmer than pub_walla_busy_loop and easier to sit under speech. |
| `birds_morning_loop` | `place/birds_morning_loop.flac` | 30.00s | Mixkit (Envato) | The tighter dawn chorus: fewer birds, closer, with air between the calls. Use where dawn_chorus_loop is too busy to sit under narration. |
| `brook_birds_loop` | `place/brook_birds_loop.flac` | 30.00s | Mixkit (Envato) | A small watercourse in open country, birds over it. Between stream_loop and river_flow_loop in scale. |
| `bus_interior_loop` | `place/bus_interior_loop.flac` | 15.00s | Mixkit (Envato) | Inside a moving bus with passengers. Engine drone plus muffled voices — a commuting bed for interviews shot on the move. |
| `bus_station_loop` | `place/bus_station_loop.flac` | 18.00s | Mixkit (Envato) | Transport interiors and forecourts. Engines idling with announcements and footsteps around them. |
| `bus_station_wide_loop` | `place/bus_station_wide_loop.flac` | 18.00s | Mixkit (Envato) | The wider terminal: more room, less engine. Pairs with station_hall_loop where the transport is road rather than rail. |
| `cauldron_fire_loop` | `place/cauldron_fire_loop.flac` | 30.00s | 344 Audio | Fire crackling and popping, close. Busier than fireplace_loop — more pops per second, which suits a cooking fire or a forge. |
| `church_bells_far_loop` | `place/church_bells_far_loop.flac` | 30.00s | Ivo Vicic | The same bells from a hillside, with spring countryside around them. The one to use under narration — distance turns the bells into landscape. |
| `church_bells_near_loop` | `place/church_bells_near_loop.flac` | 30.00s | Ivo Vicic | Three bells rung from inside the tower. Close and loud; the bells are the subject, so expect to hear them recur. |
| `city_day_loop` | `place/city_day_loop.flac` | 30.00s | Mixkit (Envato) | Street-level city daytime: traffic, footsteps and voices at distance. Closer and busier than city_distant_loop — for a shot that is in the street rather than above it. |
| `city_distant_loop` | `place/city_distant_loop.flac` | 30.00s | Epic Stock Media | Urban interiors and skyline shots. A calm courtyard with the traffic a street away, so there are no sirens or horns to date a shot. |
| `city_nightlife_loop` | `place/city_nightlife_loop.flac` | 30.00s | Epic Stock Media | A city street at night: voices and traffic, no music. The evening counterpart to city_day_loop. |
| `city_soft_loop` | `place/city_soft_loop.flac` | 30.00s | Sonik Sound Library | A city with the traffic soft and birds still audible, recorded in 5.1 and folded to stereo. The gentlest urban bed here. |
| `construction_street_loop` | `place/construction_street_loop.flac` | 30.00s | Epic Stock Media | Heavy street construction. Infrastructure, housing and urban-change subjects. |
| `crickets_field_loop` | `place/crickets_field_loop.flac` | 22.00s | Mixkit (Envato) | Open country at dusk. Drier and more even than night_forest_loop, with no trees in it — the loop point is invisible. |
| `crowd_murmur_loop` | `place/crowd_murmur_loop.flac` | 30.00s | Sonic Bat | Conferences, museums, lobbies. A large hall's worth of voices at distance — the window was picked for the lowest event prominence, which is what keeps a single foreground sentence out of it. |
| `cruise_ship_loop` | `place/cruise_ship_loop.flac` | 30.00s | Epic Stock Media | Distant adults and children on a cruise ship. Holiday, tourism and leisure industry. |
| `dawn_chorus_loop` | `place/dawn_chorus_loop.flac` | 30.00s | Mixkit (Envato) | Spring morning exteriors and the opening of a rural film. A full dawn chorus at distance — many birds, no single foreground call, which is why a thirty-second window disappears into itself. |
| `dinner_party_loop` | `place/dinner_party_loop.flac` | 30.00s | Sonik Sound Library | A dinner party on a terrace, recorded in 7.1.2 and folded to stereo. Glasses, voices and a toast. Warmer and more domestic than crowd_murmur_loop. |
| `downtown_traffic_loop` | `place/downtown_traffic_loop.flac` | 30.00s | Epic Stock Media | A working downtown: traffic, distant machinery and voices. Busier than city_day_loop and more American in character. |
| `factory_hall_loop` | `place/factory_hall_loop.flac` | 30.00s | Victor Ermakov | A busy factory hall with an alarm and voices in it. Where factory_roomtone_loop is the empty building, this one has people working in it. |
| `farmyard_morning_loop` | `place/farmyard_morning_loop.flac` | 30.00s | Mixkit (Envato) | Farm exteriors. A yard waking up: hens, distant cattle, a cockerel somewhere off-mic. The bed for agriculture, food-system and rural-history films. |
| `film_crew_walla_loop` | `place/film_crew_walla_loop.flac` | 30.00s | Sonik Sound Library | Thirty people working in a warehouse: voices, ladders, construction. The bed for a workplace where something is being built. |
| `fire_burn_loop` | `place/fire_burn_loop.flac` | 8.00s | Epic Stock Media | A tight fire-crackle loop. Short window, so it repeats often — keep it low or use cauldron_fire_loop for a long hold. |
| `fireplace_loop` | `place/fireplace_loop.flac` | 24.00s | Ivo Vicic | Interior warmth, historical pieces, cosy segments. A real close-miked fire; the crackles are sparse, which is why the loop is long. |
| `fireworks_dense_loop` | `place/fireworks_dense_loop.flac` | 30.00s | Ivo Vicic | Dense fireworks with whistles. A display in progress; the events are constant enough to hold a loop. |
| `fireworks_distant_loop` | `place/fireworks_distant_loop.flac` | 30.00s | Sonic Bat | Fireworks heard from far off. Almost weather — a soft irregular thud with no crowd in it. |
| `fireworks_loop` | `place/fireworks_loop.flac` | 30.00s | Sonic Bat | The middle-distance display: closer than fireworks_distant_loop, calmer than fireworks_dense_loop. |
| `fireworks_near` | `place/fireworks_near.flac` | 22.64s | Ivo Vicic | Powerful explosions in a row, close. Twenty-four seconds — a burst rather than a bed. |
| `forest_day_loop` | `place/forest_day_loop.flac` | 28.00s | 344 Audio | Nature documentary bed. Deciduous forest with birds and insects; the loop is long to hide the sparse calls. |
| `frogs_wetland_loop` | `place/frogs_wetland_loop.flac` | 30.00s | Mixkit (Envato) | Wetland, marsh and pond exteriors. Croaking frogs over a still forest floor; the calls are dense enough that no single one gives the wrap away. |
| `garden_morning_loop` | `place/garden_morning_loop.flac` | 30.00s | Mixkit (Envato) | Domestic exteriors — a back garden, a terrace, a suburban street at breakfast. Birds with a faint town wash behind them, which is what places it as inhabited rather than wild. |
| `glacial_lake_loop` | `place/glacial_lake_loop.flac` | 30.00s | Just Sound Effects | Small waves on rock slabs at a glacial lake. Recorded in quad and folded to stereo. Cold, still water — the counterpart to lake_shore_loop with mountains behind it. |
| `goat_herd_close_loop` | `place/goat_herd_close_loop.flac` | 20.00s | Mixkit (Envato) | The same herd close up, in a pen rather than a field. More individual voices — keep it under speech. |
| `goat_herd_loop` | `place/goat_herd_loop.flac` | 30.00s | Mixkit (Envato) | Pastoral farming: a goat herd on the move, bells and bleats spread across the field. Long window because the calls are sparse. |
| `ice_field_loop` | `place/ice_field_loop.flac` | 30.00s | Alexander Kopeikin | An ice field cracking and drifting, recorded wide. The bed for polar, glacial and climate subjects, and one of the few sounds here that cannot be faked. |
| `jungle_night_loop` | `place/jungle_night_loop.flac` | 30.00s | Epic Stock Media | Synthesised jungle night: birds and bug chirps. Its author calls it synthesised, so do not present it as a field recording. |
| `jungle_rain_birds_loop` | `place/jungle_rain_birds_loop.flac` | 30.00s | Mixkit (Envato) | Tropical exteriors under rain. Mono at source, so it sits centred in the mix — an advantage under a stereo music bed rather than a defect. |
| `jungle_river_loop` | `place/jungle_river_loop.flac` | 30.00s | Mixkit (Envato) | Tropical waterway: fast water with jungle life around it. The busiest of the water beds — keep it under narration, not beside it. |
| `lake_shore_loop` | `place/lake_shore_loop.flac` | 30.00s | Epic Stock Media | Still water: ripples against a dock. The quiet water bed — use it under maps and reflective passages where ocean_waves_loop would be too much movement. |
| `meadow_birds_loop` | `place/meadow_birds_loop.flac` | 28.00s | Mixkit (Envato) | Grassland with birds and nothing man-made. The companion to meadow_day_loop when the shot wants birds more than wind. |
| `meadow_day_loop` | `place/meadow_day_loop.flac` | 30.00s | Just Sound Effects | Open country rather than woodland: pipits, humming insects and wind through grass. The neutral outdoor bed for a map or a landscape hold. |
| `metro_hall_loop` | `place/metro_hall_loop.flac` | 30.00s | Epic Stock Media | A metro entrance hall: door dings, walla, footsteps. Between station_hall_loop and bus_station_loop in scale. |
| `night_crickets_loop` | `place/night_crickets_loop.flac` | 22.00s | generated | Night exteriors. The crickets are even, so the loop point is invisible. |
| `night_forest_loop` | `place/night_forest_loop.flac` | 28.00s | Mixkit (Envato) | Night exteriors in woodland. Denser and more wooded than night_crickets_loop: insects plus the faint movement of trees. |
| `ocean_waves_loop` | `place/ocean_waves_loop.flac` | 40.00s | Just Sound Effects | Coastal establishing shots and calm reflective passages. Soft waves against cliffs on the Norwegian coast; the loop is long because a wave set is long. |
| `owl_forest_loop` | `place/owl_forest_loop.flac` | 30.00s | Mixkit (Envato) | Night woodland with an owl in it. The calls are the point, so this reads as a scene rather than a neutral bed — for a plain night bed use night_crickets_loop. |
| `paintball_match_loop` | `place/paintball_match_loop.flac` | 30.00s | Epic Stock Media | A paintball firefight at close range. Sport, training and anything that needs shots without a weapon library. |
| `park_wind_trees_loop` | `place/park_wind_trees_loop.flac` | 30.00s | Epic Stock Media | Wind in foliage with a city wash behind it. The bed for an urban park or a tree-lined street — motion without weather. |
| `pebble_beach_loop` | `place/pebble_beach_loop.flac` | 30.00s | Just Sound Effects | Medium waves on a pebble beach. The stones are the difference: this is a Northern European coast, not a sandy one. |
| `pigsty_herd_loop` | `place/pigsty_herd_loop.flac` | 30.00s | Mixkit (Envato) | Intensive livestock interiors. A herd close and constant — the sound of a modern piggery, which is a specific and unmistakable place. |
| `pigsty_loop` | `place/pigsty_loop.flac` | 20.00s | Mixkit (Envato) | The calmer sty: fewer animals, more room. Use under narration where pigsty_herd_loop would fight it. |
| `plane_interior_loop` | `place/plane_interior_loop.flac` | 30.00s | Epic Stock Media | Inside a small plane at cruise. The bed for aviation, travel and anything shot in the air. |
| `playground_loop` | `place/playground_loop.flac` | 18.00s | Mixkit (Envato) | Schools, parks and family scenes. Children at play at middle distance; individual shouts are audible, so keep it low under narration. |
| `pub_walla_busy_loop` | `place/pub_walla_busy_loop.flac` | 30.00s | Jake Fielding | A busier pub than pub_walla_loop, from the same library. Use it where the room should feel full. |
| `pub_walla_loop` | `place/pub_walla_loop.flac` | 30.00s | Jake Fielding | Bar and restaurant interiors. Closer and warmer than crowd_murmur_loop, with glass and room in it; individual voices are audible, so keep it under narration rather than beside it. |
| `river_birds_loop` | `place/river_birds_loop.flac` | 30.00s | Mixkit (Envato) | Riverbank exteriors. Running water with birdsong over it — the two elements documentaries pair for "somewhere green and alive" without naming a country. |
| `river_flow_loop` | `place/river_flow_loop.flac` | 30.00s | Mixkit (Envato) | The plain river bed: moving water with a quiet bank behind it. Use under maps, chronologies and reflective passages where stream_loop is too small a body of water. |
| `station_hall_loop` | `place/station_hall_loop.flac` | 30.00s | InMotionAudio | Transit and institutional interiors: a big reverberant concourse. The long tail on every footstep is what sells the size of a room a shot only implies. |
| `stream_loop` | `place/stream_loop.flac` | 22.00s | generated | Woodland and countryside scenes; also a neutral water bed under maps. |
| `swimming_pool_break_loop` | `place/swimming_pool_break_loop.flac` | 30.00s | Sonic Bat | The same pool during a break — quieter, and closer to a public swimming session. |
| `swimming_pool_loop` | `place/swimming_pool_loop.flac` | 30.00s | Sonic Bat | An indoor pool during a match presentation: water, tiled reverb and a crowd. Sport, leisure and public buildings. |
| `town_street_loop` | `place/town_street_loop.flac` | 30.00s | Sonik Sound Library | A big-town street in daylight: distant traffic, a brush cutter, people. Smaller than a city and busier than a village. |
| `traffic_passing` | `place/traffic_passing.flac` | 18.28s | SoundBits | Eighteen seconds of cars passing. A roadside cutaway where a full city bed would be too much. |
| `train_interior_busy_loop` | `place/train_interior_busy_loop.flac` | 30.00s | InMotionAudio | The busier carriage: more passengers, more movement. Keep it under narration rather than beside it. |
| `train_interior_loop` | `place/train_interior_loop.flac` | 30.00s | InMotionAudio | Inside a UK train carriage. The bed for a journey, a commute or an interview on the move. |
| `train_platform_loop` | `place/train_platform_loop.flac` | 30.00s | The Noisery | A German railway platform with a whistle in it. Outdoor rail, where train_station_loop is the concourse. |
| `train_station_loop` | `place/train_station_loop.flac` | 14.00s | Mixkit (Envato) | Railway platforms. Brighter and more open than station_hall_loop, with rolling stock rather than reverberant footsteps. |
| `water_flowing_loop` | `place/water_flowing_loop.flac` | 30.00s | Mixkit (Envato) | Neutral moving-water texture with nothing else in it. The one to reach for when the picture is water and the film is about something else. |
| `woodland_birds_loop` | `place/woodland_birds_loop.flac` | 28.00s | Mixkit (Envato) | Deciduous woodland in daylight, birds in the canopy. Close in use to forest_day_loop; this one has no insects, so it works in a cooler season. |

## room-tone

| id | file | length | source | use |
|---|---|---|---|---|
| `room_tone_hum_loop` | `room-tone/room_tone_hum_loop.flac` | 20.00s | Jake Fielding | Domestic and small-room interiors: a deep appliance hum, which is most of what an indoor room tone actually is. Patch silence between takes with it and keep it very low. |
| `room_tone_office_loop` | `room-tone/room_tone_office_loop.flac` | 20.00s | generated | Patch silence between interview takes so cuts do not go dead. Keep it very low. |

## scifi

| id | file | length | source | use |
|---|---|---|---|---|
| `alien_alert_swell` | `scifi/alien_alert_swell.flac` | 6.01s | Epic Stock Media | A six-second alert that swells into something strange. Science fiction rather than an interface. |
| `electric_arc` | `scifi/electric_arc.flac` | 1.04s | InMotionAudio | A designed electrical arc. One second of discharge. |
| `electric_arc_powerup` | `scifi/electric_arc_powerup.flac` | 7.48s | InMotionAudio | An arc charging up over seven seconds. For something coming online. |
| `emf_thermometer` | `scifi/emf_thermometer.flac` | 1.68s | InMotionAudio | The electromagnetic field of a thermometer, recorded directly. Not what the device sounds like — what it radiates. A texture for anything about invisible signals. |
| `glitch_tones_loop` | `scifi/glitch_tones_loop.flac` | 30.00s | Cinematic Sound Design | Glitched screeching tones. A system failing over a long passage — glitch_data is the single event. |
| `jet_encounter_loop` | `scifi/jet_encounter_loop.flac` | 30.00s | 344 Audio | A designed aircraft drone with nothing recognisable in it. For a passage about something in the sky nobody can identify — it is a sound-design piece, not a recording of a plane. |
| `respirator_loop` | `scifi/respirator_loop.flac` | 24.00s | 344 Audio | Mechanical breathing. Medical intensive care, or science fiction — it is designed, and it sits between the two. |
| `robot_servo` | `scifi/robot_servo.flac` | 0.97s | Epic Stock Media | A servo motor with a thump under it. Robotics and automation. |
| `ship_reactor_loop` | `scifi/ship_reactor_loop.flac` | 30.00s | Epic Stock Media | A layered synthetic reactor hum. Science fiction, or a deliberately unreal machine. |
| `spaceship_roomtone_loop` | `scifi/spaceship_roomtone_loop.flac` | 30.00s | Cinematic Sound Design | A muted interior room tone for a vehicle that does not exist. Also a good abstract room tone for anything sealed and humming. |

## transition

| id | file | length | source | use |
|---|---|---|---|---|
| `air_woosh` | `transition/air_woosh.flac` | 1.90s | Mixkit (Envato) | A wide air rush for a full scene change. Longer and softer than whoosh_soft — land the cut about two thirds of the way through. |
| `arrow_whoosh` | `transition/arrow_whoosh.flac` | 0.41s | Mixkit (Envato) | A tight pass-by with a point to it. Good on a wipe that travels across the frame rather than through it. |
| `cartoon_bass_down` | `transition/cartoon_bass_down.flac` | 3.15s | Cinematic Sound Design | A descending cartoon transition. Comic, and it lands low. |
| `cartoon_pull_swoosh` | `transition/cartoon_pull_swoosh.flac` | 1.84s | Cinematic Sound Design | A cartoon pull-swoosh into a readout. Motion graphics with a sense of humour. |
| `glitch_transition` | `transition/glitch_transition.flac` | 1.53s | local-renders | A digital tear across a cut. Use where the picture itself glitches; over a clean cut it reads as a mistake. |
| `intro_transition` | `transition/intro_transition.flac` | 6.85s | Mixkit (Envato) | An opening build into a title card. Designed to start a film rather than to join two scenes. |
| `metal_whoosh_pass` | `transition/metal_whoosh_pass.flac` | 1.19s | 344 Audio | A metal object passing with a rattle in it. A transition with material in it rather than filtered air. |
| `orchestra_transition` | `transition/orchestra_transition.flac` | 6.62s | Mixkit (Envato) | An orchestral swell used as a transition. It is scored material — do not run it under a music bed. |
| `record_scratch` | `transition/record_scratch.flac` | 0.26s | local-renders | The comic full stop: everything halts. One per film at most, and only in a film with a sense of humour. |
| `riser_bright` | `transition/riser_bright.flac` | 1.22s | local-renders | A two-second bright build into a reveal. Brighter and more insistent than riser_short — for a statistic that is meant to land hard. |
| `riser_short` | `transition/riser_short.flac` | 2.95s | generated | Three-second build into a reveal or a statistic. |
| `rocket_whoosh` | `transition/rocket_whoosh.flac` | 4.08s | Mixkit (Envato) | A launch: acceleration away from the frame. Big enough to carry a title, so keep it for one. |
| `shaker_snap` | `transition/shaker_snap.flac` | 1.39s | Cinematic Sound Design | A frantic shaker into a snap. Fast, dry, and good for a quick wipe. |
| `sweep_documentary` | `transition/sweep_documentary.flac` | 3.12s | generated | Restrained transition for factual pieces; sits under voiceover without pulling focus. |
| `sweep_small` | `transition/sweep_small.flac` | 0.73s | Mixkit (Envato) | Sub-second sweep for a small element moving on screen. The one to use when swish_fast is still too big. |
| `swish_deploy` | `transition/swish_deploy.flac` | 2.42s | Epic Stock Media | A layered swish for something being deployed. Quick and directional. |
| `swish_fast` | `transition/swish_fast.flac` | 1.60s | generated | Quick wipes and fast-paced social edits where whoosh_soft is too long. |
| `swoosh_heartbeat` | `transition/swoosh_heartbeat.flac` | 7.82s | Mixkit (Envato) | Trailer-grade: a swoosh over a heartbeat pulse. For the single most dramatic transition in a film, if it has one. |
| `tech_slide` | `transition/tech_slide.flac` | 0.59s | Mixkit (Envato) | A synthetic slide for a dashboard or a UI panel changing state. Drier and shorter than slide_transition. |
| `tunnel_woosh` | `transition/tunnel_woosh.flac` | 6.32s | Mixkit (Envato) | Long reverberant sweep for a change of act. Leave it room — nearly seven seconds, and cutting it short throws away the arrival. |
| `vacuum_swoosh` | `transition/vacuum_swoosh.flac` | 2.15s | Mixkit (Envato) | A swoosh with suction on the tail. Reads as leaving somewhere rather than arriving. |
| `vibrato_spin_transition` | `transition/vibrato_spin_transition.flac` | 2.22s | Cinematic Sound Design | A spinning transition with a snap on the end. Time the snap to the cut. |
| `whoosh_debris` | `transition/whoosh_debris.flac` | 1.99s | Cinematic Sound Design | A whoosh with debris in it. Rougher than whoosh_soft — for a cut to something wrecked. |
| `whoosh_deep` | `transition/whoosh_deep.flac` | 3.40s | generated | Bigger scene or chapter change; pair with impact_cinematic on the landing. |
| `whoosh_fast_cinematic` | `transition/whoosh_fast_cinematic.flac` | 1.30s | Mixkit (Envato) | The quick cinematic cut-masker: one and a third seconds, all movement, no tail. For a run of short scenes where whoosh_deep would smear. |
| `whoosh_fire` | `transition/whoosh_fire.flac` | 2.26s | Epic Stock Media | A whoosh with fire in it. Big, and it will read as heat. |
| `whoosh_glass` | `transition/whoosh_glass.flac` | 2.45s | Epic Stock Media | A whoosh made of glass fragments. Bright and sharp — good over a data reveal. |
| `whoosh_reverse` | `transition/whoosh_reverse.flac` | 2.37s | Cinematic Sound Design | Swell into a hard cut; align the stop exactly on the cut frame. A sweep that arrives rather than departs. |
| `whoosh_soft` | `transition/whoosh_soft.flac` | 1.04s | 344 Audio | Default cut-masker. A recorded air rush rather than filtered noise. Land the cut ~60% through the whoosh. |
| `windy_swoosh` | `transition/windy_swoosh.flac` | 2.89s | Mixkit (Envato) | An airy swoosh with weather in it. Fits a cut between exteriors where a synthetic whoosh would sound indoors. |
| `zoom_in_out` | `transition/zoom_in_out.flac` | 1.11s | Mixkit (Envato) | Push in and pull back in one gesture. For a map or a diagram that zooms to a detail and returns. |
| `zoom_vacuum` | `transition/zoom_vacuum.flac` | 0.49s | Mixkit (Envato) | A pull-in rather than a sweep: something being drawn towards the camera. Time the end of it to the frame the new element lands on. |

## ui

| id | file | length | source | use |
|---|---|---|---|---|
| `accept_boing` | `ui/accept_boing.flac` | 0.97s | Cinematic Sound Design | An accept with a boing in it. Comic confirmation. |
| `accept_glassy` | `ui/accept_glassy.flac` | 0.38s | Cinematic Sound Design | A glassy accept. Crisper than success_chime and half as long. |
| `alarm_retro_loop` | `ui/alarm_retro_loop.flac` | 20.00s | Mixkit (Envato) | A repeating emergency alarm as a bed. Loop it under a crisis sequence; a single cycle is too short to read as an alarm. |
| `announce_tones` | `ui/announce_tones.flac` | 3.76s | Mixkit (Envato) | The two-tone public-address chime. Use before an announcement, a warning card or a chapter that changes register. |
| `bird_flute_toon` | `ui/bird_flute_toon.flac` | 0.94s | CB_Sounddesign | A flute figure standing in for a bird. Light, comic and openly artificial. |
| `button_arp` | `ui/button_arp.flac` | 1.48s | Cinematic Sound Design | A button press with an arpeggio on it. For a control that does something pleasant. |
| `cartoon_bubbles` | `ui/cartoon_bubbles.flac` | 0.56s | Cinematic Sound Design | Short cartoon bubbles. For something small appearing playfully. |
| `cartoon_dizzy` | `ui/cartoon_dizzy.flac` | 1.06s | Cinematic Sound Design | The confused-and-dizzy cue. Broad comedy only. |
| `cartoon_pops` | `ui/cartoon_pops.flac` | 1.23s | Cinematic Sound Design | A random sequence of pops. For several things appearing at once, where pop_small would need retriggering. |
| `chart_reveal` | `ui/chart_reveal.flac` | 0.73s | Cinematic Sound Design | A bar chart drawing in or a figure resolving. Time the last note of the arpeggio to the final frame of the animation. |
| `click_double_vintage` | `ui/click_double_vintage.flac` | 0.17s | Epic Stock Media | A vintage double click. Mechanical where click_soft is a light switch and select_modern is software. |
| `click_soft` | `ui/click_soft.flac` | 0.12s | InMotionAudio | Cursor clicks in a screen recording or a product demo. A real light switch: mid-focused, so it reads over speech without the 4 kHz spike a bright UI click puts there. |
| `coins_ting` | `ui/coins_ting.flac` | 1.06s | Cinematic Sound Design | Coins tinging. Money, reward, transaction. |
| `collect_scifi` | `ui/collect_scifi.flac` | 0.31s | Epic Stock Media | A short electronic collect. Something acquired or counted. |
| `confetti_spill` | `ui/confetti_spill.flac` | 1.89s | Cinematic Sound Design | Confetti spilling, with a pluck under it. A celebration cue for a result landing. |
| `counter_tick` | `ui/counter_tick.flac` | 0.23s | Epic Stock Media | The mechanical sibling of tick_light: a counting-machine detent, drier and shorter. For fast count-ups where a clock tick would ring. |
| `data_ticks` | `ui/data_ticks.flac` | 4.16s | Cinematic Sound Design | Number count-ups and data streaming in. A continuous readout — fade it under narration rather than cutting it. |
| `deny_dark` | `ui/deny_dark.flac` | 0.51s | Cinematic Sound Design | A dark, low deny. The unfriendly sibling of error_soft. |
| `entry_happy` | `ui/entry_happy.flac` | 0.39s | Cinematic Sound Design | A short happy entry. Something good arriving on screen. |
| `error_soft` | `ui/error_soft.flac` | 0.45s | Cinematic Sound Design | Something did not work. A muted deny — deliberately matter-of-fact, not a buzzer. |
| `game_over_trombone` | `ui/game_over_trombone.flac` | 4.93s | Mixkit (Envato) | The comic failure sting. It is a joke — use it in a film that has already made one. |
| `glitch_data` | `ui/glitch_data.flac` | 5.41s | The Noisery | Data corrupting, a feed dropping out, a system failing. The hard version of error_soft, for the beat where something breaks rather than merely refuses. |
| `glitch_high_electronic` | `ui/glitch_high_electronic.flac` | 24.18s | The Noisery | Twenty-four seconds of high electronic glitching. The long form of glitch_data — for a whole sequence of a system failing. |
| `interface_back` | `ui/interface_back.flac` | 1.26s | Mixkit (Envato) | Going back a step in an interface. Pairs with page_change, which goes forward. |
| `loading_scifi` | `ui/loading_scifi.flac` | 2.63s | Mixkit (Envato) | A system coming up. For a boot sequence, a model starting or a process beginning on screen. |
| `medical_beep` | `ui/medical_beep.flac` | 0.24s | InMotionAudio | A medical thermometer beep. A quarter of a second — clinical rather than designed. |
| `medical_button_beep` | `ui/medical_button_beep.flac` | 1.24s | InMotionAudio | A button press and its beep, together. Clinical interfaces. |
| `notify_flute` | `ui/notify_flute.flac` | 3.68s | Mixkit (Envato) | A short rising flute figure for a positive state change. Reads warmer than success_chime and takes longer. |
| `notify_flute_long` | `ui/notify_flute_long.flac` | 6.97s | Mixkit (Envato) | Seven seconds of melodic flute — a notification that is really a small cue. Fade it rather than cutting it. |
| `notify_guitar` | `ui/notify_guitar.flac` | 1.54s | Mixkit (Envato) | A plucked-string notification. The organic alternative to notify_soft when the film has acoustic music in it. |
| `notify_harp` | `ui/notify_harp.flac` | 3.07s | Mixkit (Envato) | A harp figure for something arriving on screen with a little mystery to it. Warmer and slower than notify_soft. |
| `notify_retro` | `ui/notify_retro.flac` | 0.57s | Mixkit (Envato) | An eight-bit notification. For a film about games, software history or anything that earns the reference. |
| `notify_soft` | `ui/notify_soft.flac` | 0.58s | Cinematic Sound Design | A message or notification arriving on screen. A short bright pluck — kept whole, because the gesture is the sound. |
| `page_change` | `ui/page_change.flac` | 0.39s | Cinematic Sound Design | Turning to a new page, slide or chapter — a real page turn, which is the literal cue and reads instantly. |
| `ping_down` | `ui/ping_down.flac` | 0.65s | Cinematic Sound Design | A descending sci-fi ping. State going the wrong way. |
| `pop_hard` | `ui/pop_hard.flac` | 0.38s | Mixkit (Envato) | A hard pop for an element that lands with weight. Louder and drier than pop_in. |
| `pop_in` | `ui/pop_in.flac` | 0.15s | Cinematic Sound Design | Something pops onto screen: an icon, a callout, a pin dropping on a map. |
| `pop_long` | `ui/pop_long.flac` | 0.48s | Mixkit (Envato) | A rounder, slower pop. For a bubble, a pin or a soft callout appearing. |
| `pop_small` | `ui/pop_small.flac` | 0.90s | generated | Bullet points appearing one by one. Quieter sibling of pop_in. |
| `power_up` | `ui/power_up.flac` | 1.50s | Epic Stock Media | A bright power-up. Something charging or becoming available. |
| `select_modern` | `ui/select_modern.flac` | 0.09s | Mixkit (Envato) | Half a second of modern interface select. The bright sibling of click_soft, for screen recordings where the click must be seen as well as heard. |
| `slide_transition` | `ui/slide_transition.flac` | 0.19s | Cinematic Sound Design | A graph, panel or dashboard view sliding to the next state. Short, dry and made for infographics. |
| `snap_percussive` | `ui/snap_percussive.flac` | 0.52s | Cinematic Sound Design | A dry percussive snap. The most neutral accent in the ui set. |
| `success_chime` | `ui/success_chime.flac` | 0.33s | CB_Sounddesign | A step completing or a checkmark landing. Three ascending kalimba notes: tuned, warm, and over in under a second. |
| `tick_light` | `ui/tick_light.flac` | 0.20s | 344 Audio | Step-by-step counters and timelines. One tick of a real mechanical clock — retrigger it; do not use data_ticks for slow counts. |
| `tile_reveal` | `ui/tile_reveal.flac` | 2.17s | Mixkit (Envato) | A tile or card flipping to show what is under it. For a quiz, a comparison or a fact being uncovered. |
| `timer_alert` | `ui/timer_alert.flac` | 1.06s | Mixkit (Envato) | A kitchen-timer alert: time is up. For a countdown resolving or a deadline card. |
| `trailer_alarm_half` | `ui/trailer_alarm_half.flac` | 20.00s | Federico Soler | A trailer alarm on half notes — the slowest of the three. A tension pulse for a countdown; it is scored, so it will fight music. |
| `trailer_alarm_quarter` | `ui/trailer_alarm_quarter.flac` | 20.00s | Federico Soler | The same alarm at twice the rate. Use it where the deadline is closer. |
| `trailer_alarm_whole` | `ui/trailer_alarm_whole.flac` | 30.00s | Federico Soler | The sparsest of the three: one hit per bar. Delivered as a fixed thirty-second render and kept whole, because a window sweep cannot find a bar-aligned wrap in a pattern this sparse — it measured a 17.9 dB seam and a 33.5 dB event when it tried. Repeat it on a bar line in the edit rather than butt-joining it. |
| `type_key` | `ui/type_key.flac` | 0.25s | 344 Audio | Typewriter text reveals. An actual typewriter key. Retrigger per character at low level. |
| `whoosh_ui_short` | `ui/whoosh_ui_short.flac` | 1.40s | generated | Interface panels sliding in. Sub-second whoosh for motion graphics. |
| `xylophone_ringtone` | `ui/xylophone_ringtone.flac` | 3.00s | CB_Sounddesign | A xylophone ringtone. Four seconds — a phone ringing on screen, or a soft alert. |
| `zoom_out_ui` | `ui/zoom_out_ui.flac` | 1.08s | Mixkit (Envato) | Pulling back from a detail to the whole. The counterpart to zoom_vacuum in a motion-graphics sequence. |

## vehicle

| id | file | length | source | use |
|---|---|---|---|---|
| `boat_diesel_loop` | `vehicle/boat_diesel_loop.flac` | 18.00s | Epic Stock Media | A boat's diesel idling. Fishing, ferries, ports — the steady one that runs under a whole scene. |
| `bus_departure` | `vehicle/bus_departure.flac` | 8.59s | Mixkit (Envato) | A bus pulling away from a stand. Nine seconds — enough to cover a whole departure shot. |
| `bus_passing` | `vehicle/bus_passing.flac` | 13.99s | Mixkit (Envato) | A bus passing the camera in a street. The doppler is the point; place it on the frame the vehicle crosses. |
| `car_cup_holder` | `vehicle/car_cup_holder.flac` | 0.79s | 344 Audio | A cup holder springing open. Small, and instantly reads as inside a car. |
| `car_door` | `vehicle/car_door.flac` | 2.69s | SoundBits | A car door opened and closed. Arrival and departure in three seconds. |
| `car_driving_town_alt_loop` | `vehicle/car_driving_town_alt_loop.flac` | 30.00s | InMotionAudio | A second take of the same drive. Use it where car_driving_town_loop has already been heard. |
| `car_driving_town_loop` | `vehicle/car_driving_town_loop.flac` | 30.00s | InMotionAudio | Inside a car driving through a town. The bed for an interview shot on the move, or a journey sequence. |
| `car_fan_dial` | `vehicle/car_fan_dial.flac` | 1.70s | 344 Audio | A climate-control dial turned fast. Car-interior detail. |
| `car_light_switch` | `vehicle/car_light_switch.flac` | 0.53s | 344 Audio | A headlight stalk flashed. Half a second. |
| `car_pedals` | `vehicle/car_pedals.flac` | 2.24s | SoundBits | Gas and brake pedals worked. Interior mechanical detail. |
| `car_slow_drive_loop` | `vehicle/car_slow_drive_loop.flac` | 22.00s | SoundBits | A car driving slowly, from outside. Pairs with car_driving_town_loop, which is the interior. |
| `crane_ride_loop` | `vehicle/crane_ride_loop.flac` | 30.00s | Victor Ermakov | Riding on a working crane: motors and squeaks. Heavy industry, shipbuilding and ports. |
| `helicopter_close` | `vehicle/helicopter_close.flac` | 1.79s | Mixkit (Envato) | A rotor close up. Two seconds and very loud — a cutaway, not a bed. |
| `helicopter_distant_loop` | `vehicle/helicopter_distant_loop.flac` | 20.00s | Mixkit (Envato) | A helicopter working at distance, even enough to loop. Aerial surveys, emergency services, conflict reporting. |
| `helicopter_synthetic` | `vehicle/helicopter_synthetic.flac` | 2.63s | Mixkit (Envato) | A designed rather than recorded rotor. Use it where a real helicopter would sound too specific. |
| `jet_blast_off` | `vehicle/jet_blast_off.flac` | 8.51s | 344 Audio | A jet leaving: clean acceleration with no runway around it. Aviation, logistics and anything about speed. |
| `motocross_engine_loop` | `vehicle/motocross_engine_loop.flac` | 12.00s | Mixkit (Envato) | Motocross engine, even enough to loop under a sequence. |
| `motorcycle_arrive` | `vehicle/motorcycle_arrive.flac` | 12.98s | SoundBits | A bike approaching fast and stopping. Sixteen seconds, so it covers the whole arrival. |
| `motorcycle_depart` | `vehicle/motorcycle_depart.flac` | 17.46s | SoundBits | Engine start and ride away, cleanly. The undramatic departure. |
| `motorcycle_gears_long` | `vehicle/motorcycle_gears_long.flac` | 14.81s | Mixkit (Envato) | Fifteen seconds of riding through the gears. A whole ride, not an accent. |
| `motorcycle_gearshift` | `vehicle/motorcycle_gearshift.flac` | 3.43s | Mixkit (Envato) | One gear change under acceleration. Short enough to cut to picture. |
| `motorcycle_horn` | `vehicle/motorcycle_horn.flac` | 0.31s | SoundBits | A single short horn. |
| `motorcycle_horn_long` | `vehicle/motorcycle_horn_long.flac` | 1.27s | SoundBits | A long horn. Traffic and warning. |
| `motorcycle_idle` | `vehicle/motorcycle_idle.flac` | 2.61s | Mixkit (Envato) | A motorcycle idling. The neutral one — no performance in it. |
| `motorcycle_keys` | `vehicle/motorcycle_keys.flac` | 1.49s | SoundBits | Keys in and out of an ignition. Small, and it reads as a beginning. |
| `motorcycle_passby` | `vehicle/motorcycle_passby.flac` | 10.31s | SoundBits | A fast pass-by, recorded mono so it sits centred. Place it on the frame the bike crosses. |
| `motorcycle_racing` | `vehicle/motorcycle_racing.flac` | 7.24s | Mixkit (Envato) | A racing engine held near the limit. Motorsport. |
| `motorcycle_speeding` | `vehicle/motorcycle_speeding.flac` | 5.47s | Mixkit (Envato) | Acceleration away from the camera. Pairs with motorcycle_racing across a cut. |
| `motorcycle_start_skid` | `vehicle/motorcycle_start_skid.flac` | 15.94s | SoundBits | Engine start, then tyres skidding away. The departure counterpart to motorcycle_arrive. |
| `propellers` | `vehicle/propellers.flac` | 1.79s | Mixkit (Envato) | Propellers turning. Light aircraft, boats and drones. |
| `quadcopter_loop` | `vehicle/quadcopter_loop.flac` | 16.00s | Sonik Sound Library | A toy quadcopter held close to the microphone. Aerial filming, hobby technology and surveillance. |
| `quadcopter_startup` | `vehicle/quadcopter_startup.flac` | 11.90s | Sonik Sound Library | Turn on, sync, calibrate, turn off — with the beeps. A whole gesture in twelve seconds. |
| `sportbike_engine` | `vehicle/sportbike_engine.flac` | 14.12s | Mixkit (Envato) | Fourteen seconds of a sports bike being revved. Louder and cleaner than motocross_engine_loop. |
| `sportbike_idle` | `vehicle/sportbike_idle.flac` | 8.27s | Mixkit (Envato) | The same bike ticking over. For a shot of a stationary machine. |
| `tank_engine` | `vehicle/tank_engine.flac` | 15.36s | Mixkit (Envato) | A heavy tracked diesel. Military and heavy-industry subjects. |
| `tire_skids_gravel` | `vehicle/tire_skids_gravel.flac` | 33.15s | SoundBits | Thirty seconds of tyres skidding on gravel. Rally, farm tracks and rough roads. |
| `train_passby` | `vehicle/train_passby.flac` | 11.91s | SoundBits | A train passing. Twelve seconds — the standard rail cutaway. |
| `train_passby_close` | `vehicle/train_passby_close.flac` | 44.86s | The Noisery | An electric passenger train passing close. Forty-five seconds and very loud. |
| `train_passby_subway` | `vehicle/train_passby_subway.flac` | 44.04s | Epic Stock Media | A long slow subway pass. Forty-five seconds, so it can cover a whole platform shot. |
| `tram_passby_long` | `vehicle/tram_passby_long.flac` | 17.46s | SoundBits | A tram passing, longer and slower than tram_passing. Bed level, so it can run under a street shot. |
| `tram_passing` | `vehicle/tram_passing.flac` | 11.15s | Mixkit (Envato) | A tram passing slowly. Rail on street — the sound that places a shot in a European city without a caption. |
| `truck_passby` | `vehicle/truck_passby.flac` | 5.13s | SoundBits | A freight truck passing. Logistics and haulage. |

## voice

| id | file | length | source | use |
|---|---|---|---|---|
| `anime_attack_shout` | `voice/anime_attack_shout.flac` | 0.92s | 344 Audio | An attack cry. Stylised — it will read as animation wherever you put it. |
| `anime_boxer_shout` | `voice/anime_boxer_shout.flac` | 5.20s | 344 Audio | A performed fighting shout. Animation and game material — it is acting, not a person on location. |
| `anime_elf_yell` | `voice/anime_elf_yell.flac` | 1.30s | 344 Audio | An aggressive fantasy yell. The loudest of the performed voices here. |
| `anime_fight_exhale` | `voice/anime_fight_exhale.flac` | 0.60s | 344 Audio | A short fight exhale. |
| `anime_warrior_breath` | `voice/anime_warrior_breath.flac` | 0.91s | 344 Audio | A performed effort breath. Pairs with the yells rather than with human's real breaths. |
| `anime_yell_short` | `voice/anime_yell_short.flac` | 0.43s | 344 Audio | Half a second of male battle yell. |
| `announcer_countdown` | `voice/announcer_countdown.flac` | 1.71s | Epic Stock Media | A processed game announcer counting down. Performed for a game — it carries that with it. |
| `announcer_kill` | `voice/announcer_kill.flac` | 1.05s | Epic Stock Media | A one-word announcer call. Game material. |
| `announcer_laugh` | `voice/announcer_laugh.flac` | 3.39s | Epic Stock Media | The announcer laughing. Broad, and unmistakably a game. |
| `announcer_objective` | `voice/announcer_objective.flac` | 1.27s | Epic Stock Media | "Objective completed", dry. Short enough to use as a stinger with words in it. |
| `detective_line` | `voice/detective_line.flac` | 4.65s | Epic Stock Media | Five seconds of scripted character narration in English. Dialogue, not a sound effect. |
| `elf_line_short` | `voice/elf_line_short.flac` | 0.54s | Cinematic Sound Design | A character line: "it shall be done". Performed fantasy dialogue — treat it as dialogue. |
| `emergency_broadcast` | `voice/emergency_broadcast.flac` | 15.15s | Epic Stock Media | Fifteen seconds of a scripted emergency announcement. Performed for a game — never present it as archive. |
| `police_line_backup` | `voice/police_line_backup.flac` | 1.89s | Epic Stock Media | A scripted emergency line. Performed — never present it as a real transmission. |
| `police_radio_burglary` | `voice/police_radio_burglary.flac` | 7.39s | 344 Audio | A police-radio update, calmly delivered. It is scripted performance, not a real transmission — never present it as one. |
| `police_radio_standing_by` | `voice/police_radio_standing_by.flac` | 2.31s | 344 Audio | "Standing by" — the short one, for a beat rather than a line. |
| `police_radio_theft` | `voice/police_radio_theft.flac` | 5.87s | 344 Audio | A second radio update, relaxed in tone. |
| `spectator_questions` | `voice/spectator_questions.flac` | 17.80s | 344 Audio | Eighteen seconds of a spectator asking questions. Scripted performance in English — treat it as dialogue, not atmosphere. |
| `tannoy_announcement` | `voice/tannoy_announcement.flac` | 13.52s | InMotionAudio | A station tannoy announcement in a big hall. Real reverberant public address — the one piece of speech here that is atmosphere rather than dialogue. |

## weapon

| id | file | length | source | use |
|---|---|---|---|---|
| `arrow_hit` | `weapon/arrow_hit.flac` | 0.94s | Cinematic Sound Design | An arrow striking and rattling. Also usable as a comic hit. |
| `blade_hit_metal` | `weapon/blade_hit_metal.flac` | 2.65s | David Dumais Audio | A swing landing on metal, with a resonant tail. |
| `blade_swing_scrape` | `weapon/blade_swing_scrape.flac` | 1.82s | David Dumais Audio | A long blade swung, with scrape in it. |
| `shield_spin` | `weapon/shield_spin.flac` | 34.31s | 344 Audio | A metal shield spinning to rest on a floor. Thirty-six seconds, and the decay is the whole point. |
| `spear_impact` | `weapon/spear_impact.flac` | 11.27s | 344 Audio | Wooden shafts striking. Historical and combat material. |
| `sword_cuts` | `weapon/sword_cuts.flac` | 12.78s | 344 Audio | Metallic sword slides and cuts. Thirteen seconds of takes in one file. |
| `weapon_swing_impact` | `weapon/weapon_swing_impact.flac` | 4.00s | David Dumais Audio | One heavy swing-to-impact, cut from a sixty-second reel of takes. Cap the reel rather than gate it, so the whole gesture survives. |
| `whip_crack` | `weapon/whip_crack.flac` | 1.26s | David Dumais Audio | A whip crack. The sharpest transient in the library. |

## weather

| id | file | length | source | use |
|---|---|---|---|---|
| `breeze_trees_loop` | `weather/breeze_trees_loop.flac` | 8.00s | Mixkit (Envato) | Light air moving through foliage. The gentlest movement bed in the library; use it where silence would read as a dropout. |
| `chimney_wind_loop` | `weather/chimney_wind_loop.flac` | 30.00s | InMotionAudio | Wind in a chimney, heard from inside. The interior wind bed — it places the weather outside the room. |
| `city_storm_loop` | `weather/city_storm_loop.flac` | 30.00s | Sonic Bat | A storm over a city square. Between rain_city_loop and storm_bed_loop — rain on stone with a town around it. |
| `forest_storm_loop` | `weather/forest_storm_loop.flac` | 26.00s | Mixkit (Envato) | Rain through a canopy: the drops land on leaves rather than on ground or glass, which is the detail that places the shot in woodland. |
| `hail_window_loop` | `weather/hail_window_loop.flac` | 24.00s | Jake Fielding | Interior under hail. Harder and more granular than rain_window_loop; good for a turn in a story that rain is too gentle for. |
| `heavy_rain_thunder_loop` | `weather/heavy_rain_thunder_loop.flac` | 14.00s | Mixkit (Envato) | Hard rainfall, thunder kept in the background. The wettest bed here that still leaves room for narration. |
| `heavy_storm_rain_loop` | `weather/heavy_storm_rain_loop.flac` | 12.00s | Mixkit (Envato) | Dense rainfall with no thunder at all — pair it with thunder_distant or thunder_strike where you want the rolls placed by hand. |
| `hurricane_vents_loop` | `weather/hurricane_vents_loop.flac` | 30.00s | The Noisery | Hurricane-force gusts through vents. The most violent weather bed in the library. |
| `jungle_rain_loop` | `weather/jungle_rain_loop.flac` | 26.00s | Mixkit (Envato) | Warm rain with birds still calling through it. Softer than jungle_storm_loop — rain as weather, not as event. |
| `jungle_storm_loop` | `weather/jungle_storm_loop.flac` | 30.00s | Mixkit (Envato) | Tropical downpour with the forest still audible under it. The wet-season bed for anything shot in the tropics. |
| `rain_city_loop` | `weather/rain_city_loop.flac` | 30.00s | The Noisery | Rain over a street: splatter on concrete with a distant traffic wash under it. Use where the shot is urban and rain_light_loop would sound like open country. |
| `rain_drops_loop` | `weather/rain_drops_loop.flac` | 9.00s | Mixkit (Envato) | Big individual drops on a hard surface. Short window, so check it under a long shot; it is the most rhythmic of the rain beds. |
| `rain_hail_interior_loop` | `weather/rain_hail_interior_loop.flac` | 30.00s | The Noisery | Light rain turning to hail, heard from inside. Between rain_window_loop and hail_window_loop, with both in one bed. |
| `rain_heavy_short_loop` | `weather/rain_heavy_short_loop.flac` | 5.00s | Mixkit (Envato) | A five-second downpour loop for a brief cutaway where a thirty-second bed is more file than the shot needs. |
| `rain_light_loop` | `weather/rain_light_loop.flac` | 24.00s | InMotionAudio | General wet-weather bed. The safest rain: real garden rainfall, no thunder to clash with narration and no traffic to place it in a city. |
| `rain_light_soft_loop` | `weather/rain_light_soft_loop.flac` | 12.00s | Mixkit (Envato) | Drizzle. Quieter than rain_light_loop and with no traffic or garden behind it: the neutral wet bed for an interior cutaway. |
| `rain_thunder_loop` | `weather/rain_thunder_loop.flac` | 12.00s | Mixkit (Envato) | General storm bed: rain plus rolling thunder, no location markers. The safest of the thunderstorms. |
| `rain_window_loop` | `weather/rain_window_loop.flac` | 22.00s | Jake Fielding | Interior scene with weather outside. Heavy rain on real glass, so it is muffled where a synthesised rain is only quiet — dialogue sits over it easily. |
| `storm_background_loop` | `weather/storm_background_loop.flac` | 6.00s | Mixkit (Envato) | Weather happening somewhere else — heard through a wall or from far off. Very low in level by design. |
| `storm_bed_loop` | `weather/storm_bed_loop.flac` | 30.00s | InMotionAudio | Heavy-weather bed. Continuous by design — the window was chosen for evenness, so no thunder clap is baked in; add thunder_distant on top where you want one. |
| `storm_clear_rain_loop` | `weather/storm_clear_rain_loop.flac` | 30.00s | Mixkit (Envato) | Clean heavy rainfall with thunder behind it — less low-end than thunderstorm_rain_loop, so it sits under speech more easily. |
| `storm_dark_loop` | `weather/storm_dark_loop.flac` | 30.00s | Mixkit (Envato) | Heavy weather with menace in it: low wind and pressure rather than rainfall. For the turn in a story, where storm_bed_loop would only report the weather. |
| `storm_rain_steady_loop` | `weather/storm_rain_steady_loop.flac` | 18.00s | Mixkit (Envato) | Steady storm rainfall, thunder well back. Use where rain_thunder_loop draws too much attention to itself. |
| `texas_storm_loop` | `weather/texas_storm_loop.flac` | 30.00s | Epic Stock Media | A Texas storm with the initial crash in it. The most dramatic weather bed here — the clap is inside the loop, so use it where the storm is the subject. |
| `thunder_distant` | `weather/thunder_distant.flac` | 16.50s | Jake Fielding | Drop over storm_bed_loop wherever you want a thunder roll. Kept separate on purpose: a clap baked into a loop announces the repeat every cycle. |
| `thunder_rumble_loop` | `weather/thunder_rumble_loop.flac` | 30.00s | Mixkit (Envato) | Continuous distant thunder under light rain. The bed for the minutes before a storm arrives. |
| `thunder_strike` | `weather/thunder_strike.flac` | 5.75s | Mixkit (Envato) | A close strike, crack and roll together. The hard sibling of thunder_distant — one per storm, on the cut you want to punctuate. |
| `thunderstorm_rain_loop` | `weather/thunderstorm_rain_loop.flac` | 30.00s | Mixkit (Envato) | The full storm: steady rain with thunder rolling through it. Unlike storm_bed_loop the thunder is baked in, so use it where the storm is the subject and not the backdrop. |
| `wind_blowing_loop` | `weather/wind_blowing_loop.flac` | 30.00s | Mixkit (Envato) | Exposed exteriors: a steady blow with slow gust swells. Less violent than wind_open_loop, so it survives under speech. |
| `wind_gust_pass` | `weather/wind_gust_pass.flac` | 4.46s | Mixkit (Envato) | A single gust passing the microphone. Drop it over a wind bed on a cutaway to something exposed. |
| `wind_metal_rattle_loop` | `weather/wind_metal_rattle_loop.flac` | 30.00s | The Noisery | Strong wind rattling metal. The urban storm bed — it needs a building in shot to make sense. |
| `wind_open_loop` | `weather/wind_open_loop.flac` | 30.00s | 344 Audio | Exteriors, landscapes, aerials. A real storm-force wind recorded in the open; gust swells are slow enough to read as one movement. |
| `wind_storm_loop` | `weather/wind_storm_loop.flac` | 11.00s | Mixkit (Envato) | Gale-force wind with a howl in it. Short window and deliberately dramatic — for a single shot, not a whole act. |
| `wind_whipping_loop` | `weather/wind_whipping_loop.flac` | 4.00s | Epic Stock Media | Turbulent wind through a constriction. A four-second loop — for a gap, a vent or a doorway, not an open landscape. |

