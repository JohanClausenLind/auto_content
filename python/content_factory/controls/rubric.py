"""What a story pipeline needs from its 3D objects, scored from measurements."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

RUBRIC_VERSION = "1.0.0"

TARGET = 8.5
"""The bar. A criterion below this is worth another iteration; the overall score is what matters."""


@dataclass(frozen=True)
class Criterion:
    key: str
    weight: float
    why: str


CRITERIA: tuple[Criterion, ...] = (
    Criterion("readability", 2.0, "the figure is large enough for its pose to be read"),
    Criterion("pose_legibility", 2.0, "the joints project to a spread, not a collapsed line"),
    Criterion("silhouette_separation", 1.5, "the figure is separable from its ground"),
    Criterion("identity_distinctness", 1.5, "two characters differ by silhouette alone"),
    Criterion("anatomical_plausibility", 1.5, "authored joint angles are inside human range"),
    Criterion("camera_variety", 1.0, "consecutive shots read as different shots"),
    Criterion("frame_discipline", 0.5, "the subject is inside the frame with margin"),
)
_WEIGHT_TOTAL = sum(c.weight for c in CRITERIA)


def _ramp(value: float, floor: float, good: float) -> float:
    """0 at or below ``floor``, 10 at or above ``good``, linear between."""
    if good == floor:
        return 10.0 if value >= good else 0.0
    return round(max(0.0, min(1.0, (value - floor) / (good - floor))) * 10, 2)


def _plateau(value: float, floor: float, good: float, ceiling: float) -> float:
    """Ramp up, hold, then fall away: for quantities where more is not better."""
    if value <= good:
        return _ramp(value, floor, good)
    return _ramp(ceiling - value, 0.0, ceiling - good)


def score_readability(body_fraction: float) -> float:
    """Fraction of the frame's short side a body occupies."""
    return _ramp(body_fraction, 0.10, 0.45)


def score_pose_legibility(joint_spread: float, visible_fraction: float) -> float:
    """Spread of projected joints (normalised) times how many of them are visible."""
    return min(_ramp(joint_spread, 0.04, 0.22), _ramp(visible_fraction, 0.5, 1.0))


def score_silhouette_separation(contrast: float) -> float:
    """Mean absolute luma difference between the figure's pixels and the ground behind it."""
    return _ramp(contrast, 6.0, 45.0)


def score_identity_distinctness(silhouette_delta: float, characters: int) -> float:
    """How differently two characters occupy space."""
    if characters < 2:
        return 10.0
    return _ramp(silhouette_delta, 0.02, 0.20)


def score_anatomical_plausibility(worst_joint_deg: float) -> float:
    """Largest authored rotation on any single bone."""
    return _ramp(120.0 - worst_joint_deg, 0.0, 40.0)


def score_camera_variety(median_delta: float) -> float:
    """Median camera movement between consecutive shots, in metres."""
    return _plateau(median_delta, 0.05, 1.2, 6.0)


def score_frame_discipline(worst_margin: float) -> float:
    """Smallest gap between the subject's bounding box and the frame edge, normalised."""
    return _ramp(worst_margin, 0.0, 0.06)


@dataclass(frozen=True)
class Score:
    key: str
    value: float
    weight: float
    why: str
    measured: float | None = None

    @property
    def meets_target(self) -> bool:
        return self.value >= TARGET


def score_report(scores: list[Score], per_shot: dict[str, list[Score]] | None = None) -> dict:
    """Weighted overall score plus what is holding it back."""
    if not scores:
        msg = "nothing to score"
        raise ValueError(msg)
    overall = sum(s.value * s.weight for s in scores) / sum(s.weight for s in scores)
    weakest = sorted(scores, key=lambda s: s.value)[:3]
    report = {
        "rubric_version": RUBRIC_VERSION,
        "target": TARGET,
        "overall": round(overall, 2),
        "meets_target": overall >= TARGET,
        "criteria": {
            s.key: {"score": s.value, "measured": s.measured, "why": s.why} for s in scores
        },
        "weakest": [
            {"criterion": s.key, "score": s.value, "why": s.why}
            for s in weakest
            if not s.meets_target
        ],
    }
    if per_shot is not None:
        rows = {}
        for shot_id, shot_scores in per_shot.items():
            if not shot_scores:
                continue
            shot_overall = sum(x.value * x.weight for x in shot_scores) / sum(
                x.weight for x in shot_scores
            )
            rows[shot_id] = {
                "overall": round(shot_overall, 2),
                "meets_target": shot_overall >= TARGET,
                "criteria": {x.key: x.value for x in shot_scores},
            }
        report["per_shot"] = rows
        # The shots to look at first, worst last-place first. A film can meet the target with one
        # unusable shot in it, and this is where that shows.
        report["worst_shots"] = [
            shot_id
            for shot_id, row in sorted(rows.items(), key=lambda kv: kv[1]["overall"])
            if not row["meets_target"]
        ][:5]
    return report


def joint_spread(joints: dict[str, dict]) -> float:
    """Radius of the projected joint cloud: how much of the frame the pose actually occupies."""
    xs = [j["x"] for j in joints.values() if j.get("in_frame")]
    ys = [j["y"] for j in joints.values() if j.get("in_frame")]
    if len(xs) < 3:
        return 0.0
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    return round(sum(math.dist((x, y), (cx, cy)) for x, y in zip(xs, ys, strict=True)) / len(xs), 4)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
