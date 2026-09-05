# Character asset build (MPFB2 / MakeHuman)

Runs inside the system Blender with MPFB2 loaded from the Blender 5.1 user tree
(`BLENDER_USER_EXTENSIONS`, see `run_in_blender.sh`); nothing is installed into the 5.2 config.
Output goes to `/mnt/fast/models/blender-assets/` (`CF_BLENDER_ASSETS`), indexed in the repo as
`models/characters/Blender-Assets`. MPFB2 is GPL: only these recipes and scripts live in git; the
baked `.blend` files and pose documents are derived artifacts under the weight store.

```bash
assets_build/build_all.sh                       # verify, bake poses, build every recipe, turnarounds
assets_build/run_in_blender.sh assets_build/verify_mpfb.py
assets_build/run_in_blender.sh assets_build/build_character.py assets_build/recipes/man_01.json /mnt/fast/models/blender-assets
assets_build/run_in_blender.sh assets_build/bake_poses.py /mnt/fast/models/blender-assets
uv run --project skills/video/blender_scene python assets_build/render_turnaround.py man_01
```

Each character `.blend` holds collection `CH_<name>` with `<name>:body` (Mask modifier hides
MakeHuman helper geometry; custom props `cf_role`, `cf_rig_type`, `cf_kp_anchors`), `<name>:rig`
(MPFB default rig, 163 bones, quaternion rotation mode) and `<name>:kp` (same mesh without the
mask, `hide_render`, used to read the OpenPose-18 joint positions). Anchors are basemesh vertex
indices: MPFB's own OpenPose mapping for nose/neck/ears, and the 8 vertices nearest the rest-pose
bone tails (MakeHuman's joint helper cubes) for the limbs.

Poses (`poses/*.json`, `cf.pose.v1`): `idle` and `stand_relaxed` (MakeHuman rest stance), `t_pose`
(MPFB `default_fk/t-pose.json`). The MPFB walk cycle targets IK helper bones the plain default rig
does not have, so `clips/walk_cycle.json` is skipped until an IK-enabled rig variant is built.

## Identity sheets (`build_identity_sheet.py`)

The mesh is not a usable identity reference and that was measured, not guessed: HiDream-O1's IP
pipeline treats every reference as subject material, so the clay render makes it draw clay people
and an untextured MPFB turnaround makes it draw a nude mannequin (STATUS 1339, 1379–1381). So an
`identity` reference slot sends a **styled sheet** instead — front and three-quarter views of the
same clothed figure, in the film's own style, drawn from this asset's own turnaround renders so it
is the same body.

One sheet per character per style, built once and kept beside the asset:

```
uv run python skills/video/blender_scene/assets_build/build_identity_sheet.py \
    --asset man_01 --style watercolour \
    --appearance "a man in his forties, short dark hair, plain grey work coat"
```

It lands at `<assets>/characters/man_01/sheets/watercolour.png` with a `.done.json` beside it, and
it is cached by `(blend sha256, style, seed, backend, views, prompt version)` — rebuilding the mesh
or changing the film's style rebuilds it, and nothing else does. `--dry-run` prints what it would
build and the exact prompt.

Then approve it. `review_assets` covers the mesh **and** its sheets, so a redrawn sheet is
unreviewed until someone has looked:

```
content-factory assets approve man_01 --as <name>
```

`plan_shots` puts the sheet's digest on each `CharacterSpec.reference_image_sha256`, so a plan says
which image it was made against; a sheet redrawn in the same style is a different picture and is
not served for the old plan. A lane only needs any of this if its `generate_anchor` `references`
value includes `identity` — `review_assets` blocks a lane that asks for the slot and has nothing
approved to fill it, and ignores all of it for a lane that does not.
