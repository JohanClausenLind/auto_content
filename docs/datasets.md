# Datasets on this host

What data is here, what reads it, and what it cannot give you. Written 2026-09-12 after an audit
that started as "why doesn't our output look real" and turned up 39 GB of assembled data of which
one library was reachable by the pipeline.

The machine-readable version is `python/content_factory/datasets/registry.py`; this file is the
part a person needs. `content-factory datasets list` prints the registry, `… link` builds the
`datasets/<category>/<Name>` tree, and `… check` says what is absent and what nothing reads.

```
datasets/                      git-ignored, built by `content-factory datasets link`
  human_interaction/           -> /mnt/fast/reference/*
  staging/                     -> /mnt/fast/models/blender-assets
  audio/                       -> /mnt/fast/sound-libraries/*
  training/                    -> /mnt/fast/datasets/*
```

The layout mirrors `models/<category>/<Name>`, deliberately: the weight store has had a declared
registry and a symlink index since `models/weights.py`, and data had neither. That is most of why
three of these four libraries went unread without anyone noticing.

---

## human_interaction — 20 GB, 15,789 indexed clips

Seven sources behind one SQLite index at `_index/index.sqlite` (FTS5 + BM25). The index is the only
part the pipeline talks to.

| Source | Size | What it gives | What it cannot give |
|---|---|---|---|
| CMU-Mocap | 6.7 GB | 2,514 trials, 110 two-person with A/B pairs frame-synchronised | No hugging, cuddling, head-on-shoulder or parent-child. Its AVIs are stick figures, not footage |
| SBU-Kinect | 6.1 GB | 282 sequences, 8 labelled actions, both skeletons, RGB + depth | Length — sequences average 24 frames, so these are gestures |
| Harmony4D | 4.2 GB | Two hug takes, 22 calibrated cameras, per-frame SMPL for both people | Any usable *image*: tripods stand between the lens and the subjects in most views |
| MotionHub | 1.9 GB | SMPL-H motions with hierarchical text captions | Two-person interaction — it has none; the subsets it was fetched for do not exist upstream |
| UT-Interaction | 584 MB | 20 continuous sequences, 120 segmented clips | Affection — only 2 of 6 classes are not aggression or pointing |
| TV-Human-Interactions | 166 MB | 300 broadcast clips, 6,978 hug / 5,401 kiss frames | Resolution: 624×352 |
| Stock | 17 MB | Hand-picked Pexels footage | Volume, on purpose — Pexels forbids bulk copying |

**Mostly pose, not pixels.** Only 441 of the 15,789 clips are tagged `pixels_usable`; 15,348 carry
pose. The path this library is good for is *staging* — retarget a captured contact onto your own
characters and render the control passes from whatever angle the shot wants — not cutting footage.

### Three beats matter, and only one of them stages

```
find_reference   → searches the index per story beat, writes reference/selection.json
plan_shots       → stages a character per performer, from a beat's matched clip
compile_controls → renders the passes the image model is conditioned on
```

The middle step only fires for a **retargeted** clip: one with a baked `cf.clip.v2` JSON under
`datasets/staging/Blender-Assets/clips`. There are 58 of those against 15,789 indexed, so "matched
well" and "can be staged" come apart constantly:

```
"She takes his hand and they walk out together."      → cmu_22_23_08 …   10/10 stageable
"They shake hands, and then he pulls her into a hug." → cmu_18_19_04 …    8/10 stageable
"Two people meet again and hold each other."          → sbu_s01s02_06 …  0/10 stageable
"The pair push each other, one of them walks away."   → sbu_s01s02_02 …  0/10 stageable
```

A beat whose best matches are SBU sequences has matched correctly and can stage nothing. Before
2026-09-12 both outcomes were reported as `selected: 0`, indistinguishable from an absent library —
which is why the three runs that ever called this stage looked broken and were not. They were a
Rayleigh-scattering explainer ("Sunlight is white. It carries every colour at once."), and a
two-person interaction library correctly has nothing for it. `selection.json` now carries a
`reason` and a `stageable_beats` count.

**To extend it:** retarget more takes into `Blender-Assets/clips`. The SBU actions are the obvious
gap, and they are the ones a story about people meeting will match first.

---

## staging — 614 MB

`Blender-Assets`: 4 MakeHuman characters (`man_01/02`, `woman_01/02`), 58 retargeted two-person CMU
takes, 3 poses, and the MakeHuman CC0 asset pack (hair, skins, clothes, eyes, proxymeshes).

Read by `controls/blender.py`, `shots/planner.py`, `models/video_stack.py` and
`scripts/consistency_probe.py`. Also indexed as a weight family at
`models/characters/Blender-Assets` because the installer put it there; the two paths are the same
directory.

**`controls.compiler` defaults to `motion_plan`, not `blender`.** For a long time the only
Blender runs on this host had staged an unclothed default-A-pose mannequin alone in a grey void,
because no mocap had been applied and no environment staged — real passes of nothing worth
drawing. That is no longer what it produces.

Staged 2026-09-12 through `scripts/consistency_probe.py`, three two-person takes (`cmu_18_19_01`
walk-and-shake-hands, `cmu_20_21_04` synchronised walk, `cmu_22_23_08` hold-hands-and-walk), six
frames each: **2 people and 36 joints in every one of the 18 frames**, figures at 74-80 % of frame
height, and the interaction legible in the raster — the pair's width closes from 45 % of frame to
18 % and opens again exactly where the take says they meet. The camera is solved from the clip's
own geometry by `scripts/make_mocap_shot_plan.solve_camera`, and the `body_fraction` it takes is
the setting that matters: the same planner records that HiDream ignores a pose skeleton at 33 %
body height, and the first batch of single frames came back with the figures too small to read.

What still gates the default is the four preconditions nothing states (see STATUS): a story at the
clip's fps, approved character assets, an `appearance` on every staged figure, and
`CF__CONTROLS__BLENDER_BIN=/snap/bin/blender` — `/usr/bin/blender` is an apt 4.0.2 that cannot
read assets built with 5.x and it wins on PATH.

---

## audio — 12 GB raw, 940 MB curated

`99Sounds` (11 packs, 669 files) and `mixkit` (311 files, 15 categories) plus 20 local renders.

**This is the one library that reaches the pipeline**, and it does so through a build step rather
than at run time: `skills/audio/music/build_library.py` curates it into `assets/sfx` — 670
loudness-measured, provenance-tracked sounds with a manifest (294 mixkit, 346 recorded, 19 local
renders, 11 generated). `settings.media_library.sfx_dir` points there.

The bed library beside it, `assets/music`, is 22 generated beds in six categories. It was unread
until 2026-09-12: `media_library.music_dir` pointed at `fixtures/music`, which holds one
four-second synthesised sine chord, and so all 29 runs that ever selected music selected
`calm_bed_a` — it was the only track there was. The library simply had no `tracks.json` in the
shape `stage_select_music` validates. `scripts/build_music_index.py` writes one.

---

## training — 6 GB

`romsketch`: 82 image + caption pairs of two-person affection **drawn as pen-and-ink and
watercolour sketches**, captioned in the style the trainer wanted —

> `romsketch style. An adult couple hugging tightly after meeting again, natural emotional posture, train station.`

— and the HiDream-O1 LoRA trained from them: `romsketch_ho1_v1` (rank 32, 4 checkpoints) and
`romsketch_ho1_v1b` (12 epochs, 175 MB), with 5.3 GB of optimizer state and a test render.

It is a **style** adapter, not a realism one — the captions only half say so, and rendering it on
2026-09-12 settled it: pen-and-ink line with watercolour wash, on paper, with a visible drawn
border. "rom" + "sketch". Its trigger is `romsketch style.` at the head of the prompt. Do not reach
for it to make people look photographed; reach for it to draw them.

It had nowhere to plug in until 2026-09-12: the HiDream skill could not load an adapter at all.
`skills/image/hidream/lora.py` merges one now, and `local_services.hidream_lora` points at it.

**It was trained against the `full` weights** (`--dit …/hidream-o1-comfy/… --model_type full`) and
`local_services.hidream_model_type` is `dev`. The module tree is identical, so every key would
match and the merge would succeed silently onto distilled weights it was never fit against — the
server compares `ss_base_model_version` and refuses instead. Using it means moving the server to
the full weights too.

---

## Not here

`external/InterGen` (39 MB) and `external/Inter-X` (14 MB) are the code only. InterGen's `data/`
holds `global_mean.npy` and `global_std.npy`; Inter-X's `datasets/` holds the split `.txt` files.
Neither dataset's motion data was ever fetched.

## Licences

Provenance is recorded, licences gate nothing — the operator's decision, recorded in
`/mnt/fast/reference/README.md` so nobody re-adds it. `INVENTORY.json` names every source and
`_index/measured/` records where each excerpt came from, so anything here can be traced and re-cut.
`assets/music`'s manifest carries the MiniMax-Music3 community terms, which require disclosing AI
generation on publicly distributed output; that string travels into destination packages with the
mix.
