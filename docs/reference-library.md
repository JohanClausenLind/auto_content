# The reference library, and how to lay out many editable workflows

Written 2026-09-07. What exists on disk today, the decisions behind it, and the choices still open.
The long design with every measurement is `/mnt/fast/reference/_index/PLAN-2026-09-07.md`; the
session log is the STATUS entry of the same date.

## What is on disk

`/mnt/fast/reference` holds 19 GB of two-person interaction material across seven sources, with
`INVENTORY.json` as the machine-readable truth and `README.md` as the prose. `/mnt/fast/models/blender-assets/clips`
holds 55 `cf.clip.v2` motion clips baked from the CMU two-person trials. Neither is in the repo:
both are host-specific and large.

The single most useful thing there is not a dataset, it is a shape. **For every source on disk that
ships poses, the reference is 3D.** So the answer to "would a 2D video reference be smarter when the
scene does not need 3D" is: not for this material. Drive the Blender path with real mocap instead,
and keep 2D reference for footage that has no pose data — of which we have one Pexels clip, and no
pose estimator installed to process it.

## The three layers, and what each is actually for

| Layer | Sources | Gives | Does not give |
| --- | --- | --- | --- |
| **Motion** | CMU (2514 trials, 110 two-person), MotionHub EgoBody + GRAB | Anatomically correct bodies in real contact, re-renderable from any angle | The affection vocabulary. CMU has no hug, no cuddle, no head on a shoulder, no slow dance, no parent and child |
| **Geometry with cameras** | Harmony4D hugging, 22 calibrated cameras, per-frame SMPL for both people | The contact geometry of a real embrace plus full calibration | Usable pixels. Tripods stand between lens and subject in most views. And one camera ring at chest height, so azimuth only |
| **Labelled reference** | SBU (282 seqs, 8 actions), UT (120 clips, 6 classes), TV-HI (300 clips, 6978 hug and 5401 kiss frames with head orientation) | Cheap labelled coverage, and TV-HI is the only kissing and the only real broadcast framing | Resolution, and mostly aggression in UT and half of SBU |

The gap is deliberate and worth stating: nothing on disk covers cuddling on a sofa, a head resting
on a shoulder, stroking hair, or a parent carrying a child. Retrieval is built to *report* that gap
rather than answer it with a near-miss, which turns the gap into a shooting list.

## Layout: three options

**Option A — store outside, index inside (what is built).** Bytes live under `/mnt/fast/reference`,
the repo keeps contracts, ingesters, the synonym lexicon and tests, plus one git-ignored symlink for
browsing. Derived metadata lives beside the bytes in `_index/`.

- Cheap, and matches how model weights are already handled.
- The index is not reproducible on another machine without re-downloading 19 GB.
- Recommended, and what is built.

**Option B — store outside, commit the derived index.** As A, but `_index/clips/*.json` and the
lexicon are committed, so retrieval is reproducible from a checkout and only rendering needs the
bytes.

- Makes retrieval testable in CI and reviewable in a diff.
- Adds a few MB of generated JSON to git and a rebuild step that can drift from the bytes.
- Worth doing later, once the ingesters have settled. It is a superset of A, not a fork.

**Option C — normalise everything into one homogeneous dataset.** One frame layout, one joint
convention, one directory per interaction.

- Simplest thing to query.
- Rejected. The sources genuinely differ in what they can be trusted for, and flattening them
  destroys exactly the distinction that keeps a lab tripod out of a tender scene. A layered library
  says "geometry only" where that is the truth.

## Workflows: one file each, and the canvas generated from it

**The problem, measured.** A workflow used to be defined in two hand-written places, and they had
drifted. The canvas held nine templates and the local runner five; only three ids appeared in both;
the same film was called `handdrawn-love-story` on the canvas and `handdrawn-two-hander` in the
runner; and the runner lane carried the two human review gates the canvas template had no nodes for,
so a canvas run of that film silently shipped without anyone looking at it. Adding one workflow
touched eight places in six files.

**What is built.** One committed file per workflow at `workflows/<id>.yaml`, validated by the
`WorkflowTemplate` contract, with everything else generated:

| Piece | Where |
| --- | --- |
| The contract | `python/content_factory/schemas/workflow_template.py` |
| The loader both front doors read | `python/content_factory/workflows/catalog.py` |
| The definitions | `workflows/*.yaml`, fifteen of them |
| The node catalogue the validator checks against | `fixtures/schema/node_catalog.json`, generated from `catalog.ts` by esbuild |
| The canvas data | `apps/web/src/workspace/generated/workflowTemplates.ts` plus a tracked JSON twin |
| The commands | `content-factory workflows list|show|validate|new` and `content-factory make` |

Adding a workflow is now two commands:

```bash
uv run content-factory workflows new <id> --from narrated-video
# edit workflows/<id>.yaml
uv run python scripts/export_workflows.py && uv run content-factory workflows validate
```

The validator refuses a widget value the node does not declare, a wire whose slot names or types do
not match, and an `order` that is not a topological order of the wires. It also refuses a lane that
uses a stage with no executor unless the file admits that in `caveat`. That last rule is what keeps
the catalogue honest rather than aspirational.

Order and values key on **node key**, not on stage. The old runner keyed on stage and silently
collapsed a lane that used one stage twice.

**The three decisions, settled.**

1. The two-hander lane keeps one id, `picture-story`. It is named for what it does to the material.
   Nothing in it is specific to a romance, and the same lane makes a fable or a product story from a
   different script.
2. The stills variant is **not** a second workflow. It is `generate_video.motion: hold` on
   `picture-story`. Two definitions differing by one widget value is the duplication this refactor
   exists to remove, and a test now fails if any two lanes run the same stages in the same order.
3. Research and copywriting belong to `data-story-video` alone. Giving them to `hybrid-video` too
   made its first fourteen steps a copy of that lane, so they went back to the one lane where
   drafting a script from sources is the point. Hybrid's own thing is the router.

**The catalogue, fifteen lanes.** Six other consolidations happened on the way: two single-image
templates became one lane with a model widget, and `blender-controlled-video`,
`hybrid-shot-router-video`, `stitch-sequence-video`, `image-to-video-ltx` and `video-to-social` were
renamed to say what they do rather than which model does it.

| Lane | Category | For |
| --- | --- | --- |
| `single-image` | image | One still. The model is a widget, not a second workflow |
| `image-set` | image | A set of stills that must look like each other |
| `image-upscale` | utility | Bigger and cleaner stills, no generation |
| `picture-story` | video | Drawings cut into a film with a recorded voice |
| `photo-sequence-video` | video | Stills cut into a video, silent |
| `image-to-video` | video | One still becomes a moving shot |
| `scene-controlled-video` | video | 3D-staged cinematography, no sound |
| `hybrid-video` | video | Data scenes and staged scenes spliced into one narrated timeline |
| `narrated-video` | video | Typeset cards with narration. The cheapest and most reliable lane |
| `data-story-video` | video | Researched explainer with charts. The only lane still incomplete |
| `silent-video` | video | Music and effects, no voice |
| `single-clip-post` | social | One short clip finished for posting |
| `voice-over-track` | audio | A clean narration track and captions, no picture |
| `audio-restore` | audio | Clean up a recording |
| `video-finish` | utility | Fix, upscale and interpolate a video that already exists |

## Running a production in one call

The token cost of making something should be one command, not an agent driving fifteen stages by
hand. `content-factory make <id>` resolves the definition, preflights what the lane needs, runs
every stage, prints one line per stage, and ends with a single JSON object holding `passed`, the
project directory and the path to `run.json`.

```bash
uv run content-factory workflows list                 # the catalogue, one line each
uv run content-factory make picture-story --plan      # what would run, no run
uv run content-factory make picture-story --subject "two people, one small moment"
```

The preflight refuses a lane whose stages have no executor or whose weights are absent, because
finding that out fifteen minutes into a render costs more than finding it out now. `--force` runs
anyway. `--set node.key=value` overrides one widget without editing the file, which is how the
stills variant is reached: `--set motion.motion=hold`.

`.claude/skills/produce/SKILL.md` tells an agent exactly this and nothing more, so producing
something costs one tool call and one short read.

## Retrieval: why there are no embeddings

A script asks in words and gets ranked clips back. That is SQLite FTS5 with BM25 over closed
vocabularies plus a checked-in synonym lexicon — not embeddings. The corpus is about 5500 rows, the
repo pins no torch, tests must run with no GPU and no network, and BM25 lets a test assert an exact
score, which an embedding cannot. A phrase is matched longest-first and consumed, so "head on
shoulder" cannot also fire a bare "head".

Measured, not projected. Asking

```
content-factory reference search \
  "She sits beside him, rests her head on his shoulder and he puts his arm around her." \
  --affection affection --people 2
```

returns, in order: `cmu_22_23_04` (one hand on a shoulder), `cmu_22_23_05` and `cmu_22_23_06` (both
hands on shoulders), `cmu_22_23_07`, `cmu_22_23_03` (kneeling to comfort someone sitting with their
face in their hands), `cmu_20_21_02` (link arms, walk) and `cmu_18_19_12` (friends meet, one sits and
the other joins). All seven are baked mocap clips, so all seven can drive the rig.

It also reports `absent: head_on_shoulder`. The lexicon understood the phrase, the vocabulary
declares the tag, and nothing on disk has one. Six tags are declared and deliberately empty
(`head_on_shoulder`, `cuddle`, `slow_dance`, `carry_child`, `stroke_hair`, `hold_face`), so a query
for them comes back empty and names the gap rather than handing back a near-miss. That list is the
shooting list.

The index holds 15,789 clips from nine sources and builds in about 16 seconds. It is built from the
clip documents on disk rather than from memory, so it is reproducible from the tree, and its
`manifest_sha256` is over the ordered documents rather than the sqlite bytes, because the file
depends on the host's libsqlite3 build.

## What retrieval does to a film

`picture-story` runs `find_reference` before `plan_shots`, and `plan_shots` has a third planner,
`reference`. Against `fixtures/story/love_story.json` it staged two of six beats from real captured
takes: beat 2 from *link arms, walk*, and beat 4, "You just held out your hand", from *walk, shake
hands*. Each staged shot carries two characters pointing at the same clip with actors `a` and `b`,
so the contact on screen is the contact that was recorded.

The four beats with no match keep the preset single-character staging. That fallback is the
important half: inventing a two-person staging from nothing is what produced the interpenetrating
hands this path exists to avoid.

The camera is re-solved for every staged shot rather than inherited, by `shots/framing.py`. That is
not a refinement, it is a requirement: the preset planner aims at one subject standing at the
origin, and a mocap clip moves the pair, so an inherited camera let a walking character pass the
near plane and killed the render on a NaN. Solving it from the clip's own travel and height also
measures better on the thing that decides whether the pose is read at all.

| | Retrieval-staged | Preset |
| --- | --- | --- |
| Visible fraction | 1.00 | 0.67 |
| Skeleton joints in frame | 18/18 | 14/18 |

## When to use 3D and when to use a 2D reference

Evaluated per shot before anything renders, first match winning. The threshold that matters is
**subject scale, not shot type**: pose conditioning was honoured when the figures were large in
frame and ignored at 33 % body height, where the model invented its own street scene.

| Test | Route |
| --- | --- |
| The chosen reference has no pose data and no pose weights are installed | 3D |
| Two people and a matched `cf.clip.v2` clip exists | 3D, driven by the mocap clip |
| Two or more camera angles of the same staging are needed | 3D |
| The camera moves more than 0.15 m between keyframes | 3D |
| Measured subject height below 0.33 of frame | Track the camera on the cast; report the shot if that does not lift it |
| Two people, no clip covers it, pose weights present | 2D pose video through Wan-Animate-2 |
| A character recurs across shots | 3D plus a styled character sheet as the identity reference |

Two traps are worth repeating because both were measured. Keying the camera test on *number* of
keyframes fires on all 30 shots of the existing film and makes every row below it unreachable, so
the test is on magnitude. And under an overhead camera the layout box's height is the subject's
front-to-back footprint, not its height, so a framing predictor must say which quantity it
predicted.

Three more, all measured, and the first one is why the row above changed. **The 33 % cliff is
reached by the aspect ratio, not by the action.** With one camera covering everywhere the cast
walks, no clip in the library falls under it in 16:9, two do in 1:1, and thirty of fifty-seven do
in 9:16 — twenty-nine of those two-person, and one of them travels only 1.54 m. A portrait frame is
narrow enough that holding two bodies apart retreats the camera on its own. So a vertical short is
where framing has to be solved, and `plan_shots_from_reference` keyframes the camera on the whole
cast when one static camera measures under the cliff, keeping the tracking camera only when it
actually lifts the number.

**Predict nothing you can measure.** The framing number in a shot's description and the
`underframed` fact both come from measuring the finished camera against the clip's geometry, not
from re-running the solve. Twice a predicted number and a measured one disagreed and the gap hid a
real failure: a solve that sizes for the tallest the cast ever stands claimed 0.33 where frame 0
delivered 0.26, and sampling only camera keyframes claimed 0.42 where the rendered box fell to
0.216 by the last anchor. Sample both ends and every anchor.

**The measurement is optimistic by about 0.02**, because it models a hip plus a standing head while
the render measures the mesh's bounding box. `ESTIMATE_OPTIMISM` is added before the cliff is
tested. Without it a shot estimated at 0.34 renders at 0.318 and is called legible.

## Licences are deliberately not enforced here

The operator's decision, recorded so nobody re-adds it: licences do not gate anything in this
project. There is no licence field on a reference clip, no licence class on a contract, and no check
before delivery. Provenance is still recorded where it helps you re-cut something, which is a
different thing: `INVENTORY.json` names every source and `_index/measured/` records where each
excerpt came from.

`docs/licensing.md` is maintained by a different session and is left alone. It is a record, not a
gate.
