"""Build a ShotPlan of one runner, thirty stills, a different camera on every one.

    uv run python scripts/make_runner_shot_plan.py [--clip cmu_35_18] [--shots 30]

Two decisions here are measurements rather than taste.

The motion is captured, not authored. The hand-written stride poses this replaces put a figure in
a plausible-looking pose per frame and nothing connected them, so the legs read as a mannequin
being posed. ``cmu_35_18`` is a CMU sprint trial baked to ``cf.clip.v2``: 35 frames sustaining
3.18 m/s, which is the only kind of run in the library - the two-person contact set's fastest
"running" clip is a 1.8 m/s scramble for a chair, measured, and looks like one.

The camera tracks the body instead of covering its path. One camera that must keep the whole 4.45 m
route in shot sits 7.71 m out and predicts 0.577 of frame height in 16:9 - legible, but far short of
the 0.82-0.86 the rubric's pose-legibility criterion asks for, and it falls to 0.182 in 9:16, under
the 0.33 where HiDream was measured to stop honouring the pose skeleton and invent its own scene. So
every shot solves its camera against where the runner actually is on the frame it samples, through
``content_factory.shots.framing``, which is the same solver the reference planner uses rather than a
second copy of the arithmetic.

The camera walks the circle in even steps rather than jumping across it. Azimuth by the golden
angle covered thirty directions beautifully and scored 0.0 on the rubric's camera-variety
criterion: consecutive cameras were a median 13.1 m apart, past the 6 m where the criterion says
nothing reads as one place any more. Even coverage of a circle and continuity between neighbours
are in tension, and continuity wins - thirty views of one place beats thirty places. A 12-degree
step still comes all the way round in thirty shots.

Framing is solved twice. The first pass places each camera analytically; the second reads the
body fraction Blender actually delivered from the layout pass and corrects the distance by the
ratio. One pass is not enough because the solve models a standing figure while a sprinter throws
its limbs out, and the residual ran to +0.12 of frame height - enough to clip a head at the top of
the range and to cost the pose-legibility score at the bottom.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))

from content_factory.shots.framing import solve_framing_tracking  # noqa: E402

CLIPS = Path("/mnt/fast/models/blender-assets/clips")

WIDTH, HEIGHT, FPS = 1024, 576, 24
HOLD_FRAMES = 24
"""How long each still is held by the hold cut: one second per drawing at 24 fps."""

ELEVATIONS = (4.0, 14.0, 26.0, 8.0, 38.0, 18.0)
"""Ground level to well above the head, cycling on a different period to the azimuth so the pairs
do not repeat inside thirty shots."""

LENSES = (40.0, 50.0, 58.0, 45.0, 55.0)
"""A narrow set on purpose. A 28-105 mm spread moved the camera 3 to 17 m out, and a shot that
jumps eleven metres from the one before it is a different place rather than a different angle -
median consecutive movement was 13.1 m against the 6 m the rubric calls the limit. These five
change the perspective without changing where the camera stands."""

BODY_FRACTIONS = (0.82, 0.86)
"""Target height of the figure in frame, measured. Both are above the 0.33 where the pose skeleton
stopped being honoured, and near the top of what fits: joint spread runs a consistent 0.242 of
body fraction on this rig, so the rubric's pose-legibility criterion needs a tall figure, while its
frame-discipline criterion needs 6 % of margin - which caps a centred figure at 0.88 and is why
the second framing pass exists to actually land in the band."""

AZIMUTH_STEP_DEG = 12.0
"""360 degrees over thirty shots. Every angle, and each one adjacent to its neighbour."""


def load(name: str) -> dict:
    path = CLIPS / f"{name}.json"
    if not path.is_file():
        raise SystemExit(f"clip not baked: {path}. Run mocap/bake_library.py first.")
    return json.loads(path.read_text(encoding="utf-8"))


def build(clip_name: str, count: int, *, actor: str, asset: str, beat_id: str) -> dict:
    clip = load(clip_name)
    available = int(clip["frame_count"])
    if count > available:
        raise SystemExit(
            f"{clip_name} has {available} frames and {count} stills were asked for. Sampling it"
            " twice would repeat poses; pick a longer clip or fewer stills."
        )
    shots = []
    for i in range(count):
        azimuth = (i * AZIMUTH_STEP_DEG) % 360.0
        elevation = ELEVATIONS[i % len(ELEVATIONS)]
        lens = LENSES[i % len(LENSES)]
        body_fraction = BODY_FRACTIONS[i % len(BODY_FRACTIONS)]
        (framing,) = solve_framing_tracking(
            clip,
            (i,),
            actor_ids=(actor,),
            width=WIDTH,
            height=HEIGHT,
            lens_mm=lens,
            body_fraction=body_fraction,
            azimuth_deg=azimuth,
            elevation_deg=elevation,
        )
        shots.append(
            {
                "schema_version": 1,
                "shot_id": f"sht_run_{i:04d}",
                "order": i,
                "beat_id": beat_id,
                "seed": 4000 + i,
                # The still is frame 0 of the shot; offset_frames is what advances the run, so
                # shot i is clip frame i and thirty shots are thirty poses of one sprint.
                "frame_count": HOLD_FRAMES,
                "fps": FPS,
                "width": WIDTH,
                "height": HEIGHT,
                "camera": {
                    "preset": "static",
                    "sensor_width_mm": 36.0,
                    "clip_start": 0.05,
                    "clip_end": 100.0,
                    "keyframes": [
                        {
                            "frame_index": 0,
                            "position": list(framing.position),
                            "look_at": list(framing.look_at),
                            "lens_mm": framing.lens_mm,
                            "focus_distance": framing.distance_m,
                            "easing_to_next": "ease_in_out",
                            "rotation_euler_deg": None,
                        }
                    ],
                },
                "characters": [
                    {
                        "id": "runner",
                        "asset": asset,
                        "transform": {"position": [0.0, 0.0, 0.0], "yaw_deg": 0.0, "scale": 1.0},
                        "pose": {
                            "kind": "segments",
                            "name": clip_name,
                            "actor": actor,
                            "speed": 1.0,
                            "offset_frames": i,
                            "loop": False,
                        },
                        "seg_id": 1,
                        "reference_image_sha256": [],
                    }
                ],
                "props": [],
                "environment": {
                    "background_color": [0.07, 0.08, 0.11],
                    "ground": {
                        "enabled": True,
                        # Much darker than the two-hander's 0.30 grey, because a raised camera
                        # fills the frame with floor: at 38 degrees elevation the 0.30 ground came
                        # back within 6 luma of the body and the figure stopped separating from
                        # it. 0.16 lifted the worst separation to 25 luma and 0.09 to over 45,
                        # which is where the rubric's silhouette criterion stops docking marks.
                        "color": [0.09, 0.1, 0.12],
                        "size": 60.0,
                        "seg": False,
                    },
                    "walls": None,
                },
                "lighting": {
                    # A control pass has one job: show the body. A dusk key 140 degrees off the
                    # camera put five of the thirty stills in near-silhouette, which throws away
                    # the pose signal the whole Blender layer exists to provide. So the key sits
                    # over the camera's shoulder on every shot and the dusk comes from the prompt,
                    # where a look belongs, instead of from the geometry.
                    "preset": "studio",
                    "key_azimuth_deg": round((azimuth + 35.0) % 360.0, 1),
                    "key_elevation_deg": 42.0,
                    "intensity": 1.0,
                    "shadows": True,
                },
                "render": {
                    "engine": "workbench",
                    "passes": [
                        "rough_rgb",
                        "depth",
                        "normals",
                        "segmentation",
                        "pose_skeleton",
                        "layout_boxes",
                    ],
                    "frames": [0],
                    "depth_range": "auto",
                    "canny": {"low": 100, "high": 200},
                },
                "anchor_frames": [0],
                "motion_prompt": "the runner keeps running at the same pace",
                "description": (
                    f"a runner mid-stride, {clip_name} frame {i}, camera at {azimuth:.0f} deg"
                    f" azimuth / {elevation:.0f} deg elevation on a {lens:.0f} mm lens,"
                    f" framed at {framing.predicted_body_fraction:.2f} body height"
                ),
            }
        )
    return {
        "schema_version": 1,
        "plan_id": "shp_runner_mocap",
        "deliverable_id": "dlv_runner_film",
        "story_plan_hash": None,
        "planner": "fixture",
        "planner_version": "0.1.0",
        "shots": shots,
    }


def correct(plan: dict, controls_dir: Path) -> tuple[dict, dict]:
    """Second framing pass: pull each camera in or out by the ratio Blender actually delivered.

    Two things get corrected, and both are measured rather than modelled.

    *Size.* ``predicted_body_fraction`` assumes a standing figure of known height; a sprinter's
    limbs change its projected extent by up to 0.15 of frame height either way. Body fraction is
    inversely proportional to distance, so the correction is exactly ``distance * measured /
    target`` - one multiplication, no search.

    *Height in frame.* The solve aims at half the subject's standing height, which put every
    runner high in the frame: top margins of 0.000 to 0.025 against bottom margins near 0.10, and
    one head actually clipped. A body in flight is not centred on half its standing height. So the
    aim is shifted by however far the measured box centre sits from the middle of the frame,
    converted to metres through the frame's own vertical coverage - and the camera moves with it,
    so the shot keeps its angle.

    Anything the layout pass did not measure keeps its analytic camera.
    """
    stats = {"corrected": 0, "unmeasured": 0, "worst_before": 0.0, "worst_after": 0.0}
    for shot in plan["shots"]:
        layout = controls_dir / shot["shot_id"] / "layout" / "frames" / "0000.json"
        if not layout.is_file():
            stats["unmeasured"] += 1
            continue
        figures = [
            o
            for o in json.loads(layout.read_text(encoding="utf-8"))["objects"]
            if o.get("kind") == "character"
        ]
        if not figures:
            stats["unmeasured"] += 1
            continue
        box = figures[0]["box"]
        measured = float(box["h"])
        target = float(shot["description"].split("framed at ")[1].split(" ")[0])
        keyframe = shot["camera"]["keyframes"][0]
        look = list(keyframe["look_at"])
        scale = measured / target
        position = [
            round(look[k] + (keyframe["position"][k] - look[k]) * scale, 4) for k in range(3)
        ]
        # The frame covers subject_height / measured_fraction metres vertically, and the solve put
        # look_at at half the subject's height, so 2 * look_at.z recovers that height. Image y runs
        # down, so a box centred above the middle needs the aim raised to bring it down.
        centre_y = float(box["y"]) + measured / 2.0
        rise = (0.5 - centre_y) * (2.0 * look[2]) / max(measured, 1e-6)
        stats["worst_before"] = max(stats["worst_before"], abs(measured - target))
        stats["worst_offset_before"] = max(
            stats.get("worst_offset_before", 0.0), abs(0.5 - centre_y)
        )
        keyframe["position"] = [
            round(position[0], 4),
            round(position[1], 4),
            round(position[2] + rise, 4),
        ]
        keyframe["look_at"] = [look[0], look[1], round(look[2] + rise, 4)]
        keyframe["focus_distance"] = round(keyframe["focus_distance"] * scale, 4)
        # Replaced rather than appended, so a second iteration reports where it actually started
        # instead of accumulating a history nobody reads.
        base = shot["description"].split(" [corrected")[0]
        shot["description"] = (
            f"{base} [corrected from a measured {measured:.2f} at centre {centre_y:.3f}]"
        )
        stats["corrected"] += 1
    return plan, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clip", default="cmu_35_18")
    ap.add_argument("--actor", default="a")
    ap.add_argument("--asset", default="man_01")
    ap.add_argument("--shots", type=int, default=30)
    ap.add_argument("--beat-id", default="bea_run_take00")
    ap.add_argument("--out", default="fixtures/shots/runner_mocap.json")
    ap.add_argument(
        "--correct-from",
        default="",
        help="a controls directory rendered from this plan; re-solves each camera against the "
        "body fraction the layout pass measured rather than the one the solve predicted",
    )
    ap.add_argument(
        "--start-from",
        default="",
        help="correct this plan instead of a freshly built one. The correction is a fixed-point "
        "iteration - raising the camera to centre the subject also raises the elevation, which "
        "shrinks it again - so a second pass composes onto the first rather than recomputing it "
        "from the analytic camera, which would discard it.",
    )
    args = ap.parse_args()

    if args.start_from:
        if not args.correct_from:
            raise SystemExit("--start-from is only meaningful with --correct-from")
        plan = json.loads(Path(args.start_from).read_text(encoding="utf-8"))
    else:
        plan = build(
            args.clip, args.shots, actor=args.actor, asset=args.asset, beat_id=args.beat_id
        )
    if args.correct_from:
        plan, stats = correct(plan, Path(args.correct_from))
        print(
            f"corrected {stats['corrected']} cameras from measurements"
            f" ({stats['unmeasured']} had none); worst framing error before was"
            f" {stats['worst_before']:+.3f} of frame height and the worst the subject sat off"
            f" centre was {stats.get('worst_offset_before', 0.0):.3f}"
        )
    out = Path(args.out)
    if not out.is_absolute():
        out = REPO / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    fractions = [s["camera"]["keyframes"][0] for s in plan["shots"]]
    print(f"wrote {len(plan['shots'])} shots to {out}")
    print(
        "camera distance {:.2f}-{:.2f} m, lens {:.0f}-{:.0f} mm".format(
            min(k["focus_distance"] for k in fractions),
            max(k["focus_distance"] for k in fractions),
            min(k["lens_mm"] for k in fractions),
            max(k["lens_mm"] for k in fractions),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
