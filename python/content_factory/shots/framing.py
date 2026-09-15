"""Place a camera so the subject is legible, from the clip's own geometry."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

FIGURE_HEIGHT_M = 1.76
"""The built MPFB characters' height, from man_01.asset.json."""

STANDING_HIP_M = 0.98
"""Root height of a standing adult on that rig."""

BODY_WIDTH_MARGIN_M = 0.55
"""Half a body plus a little air, so a pair is not framed edge to edge."""


@dataclass(frozen=True)
class Framing:
    """Everything a ``CameraKeyframe`` needs, plus the numbers behind it."""

    position: tuple[float, float, float]
    look_at: tuple[float, float, float]
    lens_mm: float
    distance_m: float
    subject_height_m: float
    predicted_body_fraction: float
    """Fraction of frame height the subject should fill. Compare against the rendered layout's
    ``box.h`` to find out whether the solve was right for this shot."""


def clip_extent(clip: dict[str, Any]) -> tuple[tuple[float, float], float, float]:
    """``(centre_xy, radius, subject_top_z)`` of everything the actors do, in clip-offset space."""
    origin = clip.get("origin") or [0.0, 0.0, 0.0]
    ground = float(clip.get("ground_offset", 0.0))
    xs: list[float] = []
    ys: list[float] = []
    hip_top = 0.0
    for actor in clip.get("actors", []):
        for frame in actor.get("frames", []):
            root = frame["root_translation"]
            xs.append(root[0] - origin[0])
            ys.append(root[1] - origin[1])
            hip_top = max(hip_top, root[2] + ground)
    if not xs:
        return (0.0, 0.0), 0.0, FIGURE_HEIGHT_M
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    radius = max(max(abs(x - cx) for x in xs), max(abs(y - cy) for y in ys))
    return (cx, cy), radius, hip_top + (FIGURE_HEIGHT_M - STANDING_HIP_M)


def group_extent_at(
    clip: dict[str, Any], frame: int, *, actor_ids: tuple[str, ...] | None = None
) -> tuple[tuple[float, float], float, float]:
    """``(centre_xy, radius, subject_top_z)`` of some actors at one clip frame, in offset space."""
    origin = clip.get("origin") or [0.0, 0.0, 0.0]
    ground = float(clip.get("ground_offset", 0.0))
    actors = {str(a.get("actor_id")): a for a in clip.get("actors", [])}
    wanted = tuple(actors) if actor_ids is None else actor_ids
    missing = [a for a in wanted if a not in actors]
    if missing:
        msg = f"clip {clip.get('name')} has no actor {missing[0]!r}: {sorted(actors)}"
        raise KeyError(msg)
    roots = []
    for actor_id in wanted:
        frames = actors[actor_id].get("frames") or []
        if frames:
            roots.append(frames[max(0, min(frame, len(frames) - 1))]["root_translation"])
    if not roots:
        return (0.0, 0.0), 0.0, FIGURE_HEIGHT_M
    xs = [r[0] - origin[0] for r in roots]
    ys = [r[1] - origin[1] for r in roots]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    radius = max(max(abs(x - cx) for x in xs), max(abs(y - cy) for y in ys))
    top = max(r[2] for r in roots) + ground + (FIGURE_HEIGHT_M - STANDING_HIP_M)
    return (cx, cy), radius, top


def _place(
    centre: tuple[float, float],
    top: float,
    radius: float,
    *,
    width: int,
    height: int,
    lens_mm: float,
    sensor_width_mm: float,
    body_fraction: float,
    azimuth_deg: float,
    elevation_deg: float,
) -> Framing:
    """Solve the camera distance for a subject ``top`` tall and ``radius`` wide, and place it."""
    cx, cy = centre
    sensor_h = sensor_width_mm * height / width
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    # A standing figure seen from above is shorter in frame than it is in the world: its height
    # projects as top * cos(elevation).
    apparent = top * math.cos(el)
    distance = lens_mm * apparent / (max(body_fraction, 0.05) * sensor_h)
    half_width_at_d = distance * (sensor_width_mm / 2.0) / lens_mm
    needed = radius + BODY_WIDTH_MARGIN_M
    if needed > half_width_at_d:
        distance *= needed / half_width_at_d
    look_z = top / 2.0
    position = (
        cx + distance * math.cos(el) * math.cos(az),
        cy + distance * math.cos(el) * math.sin(az),
        look_z + distance * math.sin(el),
    )
    return Framing(
        position=(round(position[0], 4), round(position[1], 4), round(position[2], 4)),
        look_at=(round(cx, 4), round(cy, 4), round(look_z, 4)),
        lens_mm=lens_mm,
        distance_m=round(distance, 4),
        subject_height_m=round(top, 4),
        predicted_body_fraction=round(lens_mm * apparent / (distance * sensor_h), 4),
    )


def solve_framing(
    clip: dict[str, Any],
    *,
    width: int,
    height: int,
    lens_mm: float = 50.0,
    sensor_width_mm: float = 36.0,
    body_fraction: float = 0.65,
    azimuth_deg: float = 35.0,
    elevation_deg: float = 6.0,
) -> Framing:
    """Where to put one fixed camera so this clip's actors fill ``body_fraction`` of the frame."""
    centre, radius, top = clip_extent(clip)
    return _place(
        centre,
        top,
        radius,
        width=width,
        height=height,
        lens_mm=lens_mm,
        sensor_width_mm=sensor_width_mm,
        body_fraction=body_fraction,
        azimuth_deg=azimuth_deg,
        elevation_deg=elevation_deg,
    )


def solve_framing_tracking(
    clip: dict[str, Any],
    frames: tuple[int, ...],
    *,
    actor_ids: tuple[str, ...] | None = None,
    width: int,
    height: int,
    lens_mm: float = 50.0,
    sensor_width_mm: float = 36.0,
    body_fraction: float = 0.65,
    azimuth_deg: float = 35.0,
    elevation_deg: float = 6.0,
) -> tuple[Framing, ...]:
    """One framing per clip frame in ``frames``, each solved on."""
    if not frames:
        msg = "solve_framing_tracking needs at least one frame"
        raise ValueError(msg)
    out = []
    for frame in frames:
        centre, radius, top = group_extent_at(clip, frame, actor_ids=actor_ids)
        out.append(
            _place(
                centre,
                top,
                radius,
                width=width,
                height=height,
                lens_mm=lens_mm,
                sensor_width_mm=sensor_width_mm,
                body_fraction=body_fraction,
                azimuth_deg=azimuth_deg,
                elevation_deg=elevation_deg,
            )
        )
    return tuple(out)


def body_fraction_for(
    centre: tuple[float, float],
    top: float,
    *,
    lens_mm: float,
    camera_position: tuple[float, float, float],
    width: int,
    height: int,
    sensor_width_mm: float = 36.0,
) -> float:
    """How much of the frame height a subject ``top`` tall at ``centre`` fills."""
    aim = (centre[0], centre[1], top / 2.0)
    dx, dy, dz = (camera_position[i] - aim[i] for i in range(3))
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    if distance <= 1e-6:
        return 0.0
    # Matches the solve: a figure seen from above projects as top * cos(elevation).
    apparent = top * math.sqrt(max(0.0, 1.0 - (dz / distance) ** 2))
    sensor_h = sensor_width_mm * height / width
    return round(lens_mm * apparent / (distance * sensor_h), 4)


def achieved_body_fraction(
    clip: dict[str, Any],
    frame: int,
    *,
    actor_ids: tuple[str, ...] | None = None,
    lens_mm: float,
    camera_position: tuple[float, float, float],
    width: int,
    height: int,
    sensor_width_mm: float = 36.0,
) -> float:
    """The inverse of the solve for a mocap-staged shot."""
    centre, _radius, top = group_extent_at(clip, frame, actor_ids=actor_ids)
    return body_fraction_for(
        centre,
        top,
        lens_mm=lens_mm,
        camera_position=camera_position,
        width=width,
        height=height,
        sensor_width_mm=sensor_width_mm,
    )


def standing_extent(
    positions: tuple[tuple[float, float, float], ...],
) -> tuple[tuple[float, float], float, float]:
    """``(centre_xy, radius, top)`` for figures standing at ``positions``, in world space."""
    if not positions:
        return (0.0, 0.0), 0.0, FIGURE_HEIGHT_M
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    radius = max(max(abs(x - cx) for x in xs), max(abs(y - cy) for y in ys))
    return (cx, cy), radius, max(p[2] for p in positions) + FIGURE_HEIGHT_M
