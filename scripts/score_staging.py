"""Score a rendered set of control passes against the staging rubric.

    uv run python scripts/score_staging.py <controls_dir> [--clip cmu_35_18] [--json report.json]

    # e.g. after `run-local ... --until controls`
    uv run python scripts/score_staging.py \
        output/local-runs/runner-mocap/deliverables/dlv_short0000001/controls

Every criterion is read off what Blender actually rendered, never off the shot plan's intentions:
the layout pass for how big the figure came out and how close it got to the frame edge, the
skeleton pass for whether the pose projects to anything, the segmentation mask against the rough
RGB for whether the figure separates from its ground, and camera.json for whether consecutive
shots are different shots. That distinction is the whole point - a plan that asks for a 0.82 body
fraction and delivers 0.65 scores the 0.65.

``content_factory.controls.rubric`` owns the curves, the weights and the 8.5 bar, and pins its own
version into the report, so a score is always attributable to the thresholds it was measured
against and cannot be quietly re-based by editing this script.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from itertools import pairwise
from pathlib import Path
from statistics import median

import numpy as np
from numpy.typing import NDArray
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))

from content_factory.controls import rubric  # noqa: E402

CLIPS = Path("/mnt/fast/models/blender-assets/clips")


def _angle(a: list[float], b: list[float]) -> float:
    dot = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b, strict=True))))
    return math.degrees(math.acos(dot))


def worst_authored_rotation(plan_path: Path) -> tuple[float, int]:
    """``(largest authored bone rotation in degrees, how many bones were authored)``.

    This is the quantity the rubric's anatomical-plausibility criterion actually names: a rotation
    somebody *typed*, where 140 degrees on one axis is a typo that shows in every frame. A
    ``segments`` or ``clip`` pose authors nothing - the angles come from a capture - so a
    mocap-staged plan has no authored rotation to judge and the criterion does not apply, the same
    way identity distinctness does not apply to a solo scene.

    Feeding captured joint flexion in here instead was wrong and scored 0.9/10 for a sprint whose
    knees reach 116 degrees, which is what a running knee does. What can go wrong with a captured
    pose is the retarget landing it on the wrong rig angles, and that is measured separately by
    :func:`retarget_fidelity`, so the 10 here is not a free pass.
    """
    if not plan_path.is_file():
        return 0.0, 0
    plan = rubric.load_json(plan_path)
    worst, authored = 0.0, 0
    for shot in plan.get("shots", []):
        for character in shot.get("characters", []):
            pose = character.get("pose") or {}
            if pose.get("kind") != "bones":
                continue
            for bone in (pose.get("bones") or {}).values():
                quat = bone.get("rotation_quaternion") or [1.0, 0.0, 0.0, 0.0]
                authored += 1
                w = max(-1.0, min(1.0, float(quat[0])))
                worst = max(worst, math.degrees(2.0 * math.acos(w)))
    return round(worst, 1), authored


def retarget_fidelity(controls_dir: Path, clip_name: str) -> float:
    """Worst disagreement, in degrees, between a rendered elbow's bend and the capture's own.

    Not a rubric criterion - evidence for one. A ``cf.clip.v2`` stores world segment directions, so
    the angle between ``lhumerus`` and ``lradius`` is the elbow, with no rig involved; the rendered
    skeleton's shoulder/elbow/wrist unproject to camera space through camera.json and give the same
    angle. If the aim solve were dropping a twist, these would not agree.

    Read the magnitude against how bent the joint is. An included angle is ill-conditioned near
    straight: on ``cmu_35_18``, a sprint whose elbows sit at 92-116 degrees, this measures 2.9
    degrees worst; on ``cmu_18_19_01``, a handshake whose elbows are at 25-30, it measures 12.5
    from the same code, because a millimetre of projected elbow error swings the angle much
    further when the arm is nearly straight. A large number on a near-straight limb is this
    measurement's precision, not the retarget's error.
    """
    if not clip_name:
        return 0.0
    clip = rubric.load_json(CLIPS / f"{clip_name}.json")
    # Per actor, not actors[0]. Comparing a character playing actor "b" against actor "a"'s frames
    # measured a two-person shot at 29.7 degrees of disagreement where the retarget was fine; the
    # solo runner hid the bug because it has one actor.
    by_actor = {str(a["actor_id"]): a["frames"] for a in clip["actors"]}
    worst = 0.0
    for shot in sorted(d for d in controls_dir.iterdir() if d.is_dir()):
        skeleton = rubric.load_json(shot / "skeleton" / "frames" / "0000.json")
        camera_doc = rubric.load_json(shot / "camera.json")
        intrinsics = camera_doc["frames"][0]["intrinsics"]
        size = (camera_doc["width"], camera_doc["height"])
        # One entry per entity *per rendered frame*, so it has to be keyed on both. Keying on the
        # entity alone kept the last entry and compared the frame-0 skeleton against clip frame
        # 60, which reported a 32-degree retarget error where there was none. The solo runner hid
        # it by rendering a single frame.
        poses = {
            (p["entity"], int(p.get("frame", 0))): p
            for p in rubric.load_json(shot / "metadata.json").get("poses", [])
        }
        for person in skeleton["people"]:
            pose = poses.get((person["character_id"], int(skeleton.get("frame_index", 0))))
            if pose is None or pose.get("clip") != clip_name:
                continue
            frames = by_actor.get(str(pose.get("actor", "a")))
            if not frames:
                continue
            directions = frames[min(int(pose["clip_frames"][0]), len(frames) - 1)]["directions"]
            joints = person["joints"]
            for side, upper, lower in (
                ("l_", "lhumerus", "lradius"),
                ("r_", "rhumerus", "rradius"),
            ):
                if upper not in directions or lower not in directions:
                    continue
                a, b, c = (
                    _unproject(joints[side + n], intrinsics, size)
                    for n in ("shoulder", "elbow", "wrist")
                )
                rendered = _angle(
                    _unit([b[i] - a[i] for i in range(3)]),
                    _unit([c[i] - b[i] for i in range(3)]),
                )
                worst = max(worst, abs(rendered - _angle(directions[upper], directions[lower])))
    return round(worst, 1)


def _unproject(
    joint: dict, intrinsics: list[float], size: tuple[int, int]
) -> tuple[float, float, float]:
    """A skeleton joint's normalised image position plus its depth, back to camera space."""
    fx, fy, cx, cy = intrinsics
    width, height = size
    z = joint["z"]
    return ((joint["x"] * width - cx) * z / fx, (joint["y"] * height - cy) * z / fy, z)


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _silhouette(seg_png: Path, seg_id: int, grid: int = 64) -> NDArray[np.float32] | None:
    """One character's silhouette, cropped to its own bounding box and resized to a fixed grid.

    Cropping is what makes this a shape comparison rather than a position one: two identical
    mannequins standing a metre apart differ enormously as masks and not at all as bodies, and the
    criterion asks whether a viewer could tell them apart, not where they stand.
    """
    seg = np.asarray(Image.open(seg_png))
    mask = seg == seg_id
    if not mask.any():
        return None
    ys, xs = np.nonzero(mask)
    crop = mask[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    small = Image.fromarray((crop * 255).astype("uint8")).resize(
        (grid, grid), Image.Resampling.NEAREST
    )
    return np.asarray(small, dtype=np.float32) / 255.0


def _silhouette_delta(seg_png: Path, seg_ids: list[int]) -> float | None:
    """How differently two characters occupy space, 0 (identical shapes) to 1.

    ``None`` when there is nobody to compare against, which the caller reports as unmeasured
    rather than as zero. Reporting zero was wrong in a way that mattered: it scored every
    two-character film 0.00 on a criterion nothing had measured, and dragged the aggregate below
    what the staging deserved. Measured on the real shots it comes back at 0.19 for man_01 against
    woman_01, which is nearly full marks.

    Read it for what it is: in-scene distinguishability, which is the criterion's stated purpose -
    whether a viewer can keep these two straight from frame to frame. It is not a measure of
    identity on its own, because two characters doing different things differ in silhouette however
    identical their meshes. The stricter test compares the same two assets in the *same* pose, off
    their t-pose turnarounds, and would be the one to reach for before trusting a cast of
    look-alikes.
    """
    if len(seg_ids) < 2:
        return None
    first, second = (_silhouette(seg_png, i) for i in seg_ids[:2])
    if first is None or second is None:
        return None
    return float(np.abs(first - second).mean())


def _figure_contrast(rgb_png: Path, seg_png: Path, seg_id: int) -> float:
    luma = np.asarray(Image.open(rgb_png).convert("L"), dtype=np.float32)
    seg = np.asarray(Image.open(seg_png))
    mask = seg == seg_id
    if not mask.any() or mask.all():
        return 0.0
    return float(abs(luma[mask].mean() - luma[~mask].mean()))


def measure_shot(shot: Path) -> dict | None:
    """Everything one shot's rendered passes say about it. ``None`` if it staged no character."""
    layout = rubric.load_json(shot / "layout" / "frames" / "0000.json")
    figures = [o for o in layout["objects"] if o.get("kind") == "character"]
    if not figures:
        return None
    body_fractions: list[float] = []
    margins: list[float] = []
    contrasts: list[float] = []
    for obj in figures:
        box = obj["box"]
        body_fractions.append(box["h"])
        margins.append(
            min(box["x"], box["y"], 1.0 - (box["x"] + box["w"]), 1.0 - (box["y"] + box["h"]))
        )
        contrasts.append(
            _figure_contrast(
                shot / "rough_rgb" / "frames" / "0000.png",
                shot / "segmentation" / "frames" / "0000.png",
                int(obj["seg_id"]),
            )
        )
    skeleton = rubric.load_json(shot / "skeleton" / "frames" / "0000.json")
    spreads: list[float] = []
    visibles: list[float] = []
    for person in skeleton["people"]:
        joints = person["joints"]
        spreads.append(rubric.joint_spread(joints))
        visibles.append(sum(1 for j in joints.values() if j.get("visible")) / max(1, len(joints)))
    camera = rubric.load_json(shot / "camera.json")["frames"][0]
    return {
        "characters": len(figures),
        "body_fraction_min": round(min(body_fractions), 4),
        "joint_spread_min": round(min(spreads), 4) if spreads else 0.0,
        "visible_min": round(min(visibles), 4) if visibles else 0.0,
        "contrast_min": round(min(contrasts), 2),
        "margin_min": round(min(margins), 4),
        "silhouette_delta_min": _silhouette_delta(
            shot / "segmentation" / "frames" / "0000.png",
            [int(o["seg_id"]) for o in figures],
        ),
        "camera_position": tuple(camera["position"]),
        "lens_mm": float(camera["lens_mm"]),
    }


def measure(controls_dir: Path, plan_path: Path, clip_name: str) -> dict:
    """The film's measurements, plus each shot's own.

    The film takes each criterion on its *worst* shot rather than its average, because one
    unreadable frame in thirty is a frame that gets regenerated. That is the right aggregate for
    "is this shippable" and the wrong one for "did my last change work", so the per-shot rows are
    kept alongside it rather than collapsed away.
    """
    shots = sorted(d for d in controls_dir.iterdir() if d.is_dir())
    if not shots:
        raise SystemExit(f"no shot directories under {controls_dir}")
    per_shot = {shot.name: measure_shot(shot) for shot in shots}
    staged = {name: m for name, m in per_shot.items() if m is not None}
    if not staged:
        raise SystemExit(f"no shot under {controls_dir} staged a character")
    positions = [m["camera_position"] for m in staged.values()]
    deltas = [math.dist(a, b) for a, b in pairwise(positions)] or [0.0]
    authored_deg, authored_bones = worst_authored_rotation(plan_path)
    silhouettes = [
        m["silhouette_delta_min"] for m in staged.values() if m["silhouette_delta_min"] is not None
    ]
    # Camera variety and the authored/retarget evidence are properties of the sequence, not of one
    # still, so every shot carries the film's value for them.
    shared = {
        "authored_rotation_deg": authored_deg,
        "authored_bones": authored_bones,
        "camera_delta_median": round(median(deltas), 3),
    }
    return {
        "shots": len(staged),
        "retarget_worst_deg": retarget_fidelity(controls_dir, clip_name),
        "characters": max(m["characters"] for m in staged.values()),
        "body_fraction_min": min(m["body_fraction_min"] for m in staged.values()),
        "joint_spread_min": min(m["joint_spread_min"] for m in staged.values()),
        "visible_min": min(m["visible_min"] for m in staged.values()),
        "contrast_min": min(m["contrast_min"] for m in staged.values()),
        "margin_min": min(m["margin_min"] for m in staged.values()),
        "silhouette_delta_min": round(min(silhouettes), 4) if silhouettes else None,
        "lens_count": len({m["lens_mm"] for m in staged.values()}),
        **shared,
        "per_shot": {name: {**m, **shared} for name, m in staged.items()},
    }


def criterion_scores(m: dict) -> list[rubric.Score]:
    """One ``Score`` per criterion from one set of measurements, film-wide or single-shot."""
    silhouette_delta = m["silhouette_delta_min"]
    worst_joint, authored = m["authored_rotation_deg"], m["authored_bones"]
    return [
        rubric.Score(
            "readability",
            rubric.score_readability(m["body_fraction_min"]),
            2.0,
            "the figure is large enough for its pose to be read",
            m["body_fraction_min"],
        ),
        rubric.Score(
            "pose_legibility",
            rubric.score_pose_legibility(m["joint_spread_min"], m["visible_min"]),
            2.0,
            "the joints project to a spread, not a collapsed line",
            m["joint_spread_min"],
        ),
        rubric.Score(
            "silhouette_separation",
            rubric.score_silhouette_separation(m["contrast_min"]),
            1.5,
            "the figure is separable from its ground",
            m["contrast_min"],
        ),
        rubric.Score(
            "identity_distinctness",
            rubric.score_identity_distinctness(silhouette_delta or 0.0, m["characters"]),
            1.5,
            "two characters differ by silhouette alone"
            if silhouette_delta is not None
            else "one character, so there is nobody to be confused with",
            silhouette_delta if silhouette_delta is not None else float(m["characters"]),
        ),
        rubric.Score(
            "anatomical_plausibility",
            rubric.score_anatomical_plausibility(worst_joint) if authored else 10.0,
            1.5,
            "authored joint angles are inside human range"
            if authored
            else "no authored rotations: every pose comes from a capture",
            worst_joint if authored else float(authored),
        ),
        rubric.Score(
            "camera_variety",
            rubric.score_camera_variety(m["camera_delta_median"]),
            1.0,
            "consecutive shots read as different shots",
            m["camera_delta_median"],
        ),
        rubric.Score(
            "frame_discipline",
            rubric.score_frame_discipline(m["margin_min"]),
            0.5,
            "the subject is inside the frame with margin",
            m["margin_min"],
        ),
    ]


def score(m: dict, clip_name: str) -> dict:
    """The film's score on its worst shot per criterion, and every shot's own score beside it."""
    report = rubric.score_report(
        criterion_scores(m),
        {name: criterion_scores(shot) for name, shot in m.get("per_shot", {}).items()},
    )
    report["measured"] = {k: v for k, v in m.items() if k != "per_shot"}
    report["clip"] = clip_name
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("controls_dir")
    ap.add_argument("--clip", default="", help="cf.clip.v2 clip the shots were staged from")
    ap.add_argument(
        "--plan",
        default="",
        help="the ShotPlan these controls were compiled from; read for authored rotations "
        "(default: <controls_dir>/../shots/plan.json)",
    )
    ap.add_argument("--json", default="", help="also write the report here")
    args = ap.parse_args()

    controls = Path(args.controls_dir)
    plan = Path(args.plan) if args.plan else controls.parent / "shots" / "plan.json"
    m = measure(controls, plan, args.clip)
    report = score(m, args.clip)
    print(
        f"{report['overall']:.2f}/10 against {report['target']} (rubric {report['rubric_version']})"
    )
    for key, row in report["criteria"].items():
        mark = "ok" if row["score"] >= report["target"] else "!!"
        print(f"  {mark} {key:<24} {row['score']:5.2f}  measured {row['measured']}")
    print(
        f"  evidence: {m['authored_bones']} authored bone rotations"
        f" (worst {m['authored_rotation_deg']} deg),"
        f" retarget within {m['retarget_worst_deg']} deg of the capture"
    )
    for row in report["weakest"]:
        # `why` names the property when it is satisfied, so printing it bare against a low score
        # reads as its own contradiction: "readability at 3.3 - the figure is large enough".
        print(f"  holding it back: {row['criterion']} at {row['score']} — wanted: {row['why']}")
    shots = report.get("per_shot") or {}
    if shots:
        overalls = sorted(row["overall"] for row in shots.values())
        under = report.get("worst_shots") or []
        print(
            f"  per shot: {len(shots)} shots, {overalls[0]:.2f} to {overalls[-1]:.2f},"
            f" {len(shots) - sum(1 for r in shots.values() if r['meets_target'])} under target"
        )
        for shot_id in under:
            row = shots[shot_id]
            worst = min(row["criteria"].items(), key=lambda kv: kv[1])
            print(f"    {shot_id} {row['overall']:5.2f}  weakest {worst[0]} at {worst[1]:.2f}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
