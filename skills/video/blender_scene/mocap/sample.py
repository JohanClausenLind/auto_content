"""Sample a ``cf.clip.v2`` document at a render frame.

The v1 sampler in ``posing.sample_clip`` slerps quaternions. This one interpolates *directions*,
which is the same idea one representation down: the shortest arc between two unit vectors, so a
sampled direction is still a unit vector and a limb never changes length between frames. Root
translation is interpolated linearly and yaw along the shortest way round.

A two-actor clip is sampled per actor. Both actors share one timeline, so sampling them at the
same render frame keeps captured contact intact; sampling them independently would not.
"""

from __future__ import annotations

import math
from typing import Any

Vec3 = tuple[float, float, float]


def _nlerp(a: list[float], b: list[float], t: float) -> Vec3:
    """Shortest-arc interpolation between two unit vectors, renormalised.

    Uses the great-circle path when the vectors are far apart and a straight line when they are
    close, for the same reason slerp does: the linear form loses precision as the angle shrinks,
    and the trigonometric form loses it as the angle vanishes.
    """
    d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
    d = max(-1.0, min(1.0, d))
    if d > 0.9995 or d < -0.9995:
        v = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)
    else:
        theta0 = math.acos(d)
        s = math.sin(theta0)
        w0 = math.sin((1.0 - t) * theta0) / s
        w1 = math.sin(t * theta0) / s
        v = (a[0] * w0 + b[0] * w1, a[1] * w0 + b[1] * w1, a[2] * w0 + b[2] * w1)
    n = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if n < 1e-12:
        return (a[0], a[1], a[2])
    return (v[0] / n, v[1] / n, v[2] / n)


def _lerp3(a: list[float], b: list[float], t: float) -> Vec3:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _lerp_angle(a: float, b: float, t: float) -> float:
    delta = (b - a + 180.0) % 360.0 - 180.0
    return a + delta * t


def clip_position(
    clip: dict[str, Any],
    frame: int,
    *,
    fps: int,
    speed: float = 1.0,
    offset_frames: int = 0,
    loop: bool = False,
) -> tuple[int, int, float]:
    """``(index_before, index_after, blend)`` into the clip's own frame list."""
    frames = clip["frame_count"]
    if frames <= 0:
        raise ValueError("clip has no frames")
    clip_fps = float(clip.get("fps", fps))
    t = (frame + offset_frames) * speed * clip_fps / fps
    if loop:
        t = t % frames
        i0 = int(t)
        i1 = (i0 + 1) % frames
    else:
        t = max(0.0, min(t, frames - 1))
        i0 = int(t)
        i1 = min(i0 + 1, frames - 1)
    return i0, i1, t - i0


def sample_actor(
    clip: dict[str, Any],
    actor_id: str,
    frame: int,
    *,
    fps: int,
    speed: float = 1.0,
    offset_frames: int = 0,
    loop: bool = False,
) -> dict[str, Any]:
    """Directions, root translation and yaw for one actor at ``frame``."""
    actors = {a["actor_id"]: a for a in clip["actors"]}
    if actor_id not in actors:
        raise KeyError(f"clip {clip.get('name')} has no actor {actor_id!r}: {sorted(actors)}")
    seq = actors[actor_id]["frames"]
    i0, i1, blend = clip_position(
        clip, frame, fps=fps, speed=speed, offset_frames=offset_frames, loop=loop
    )
    a, b = seq[i0], seq[i1]
    if blend <= 0.0:
        directions = {k: tuple(v) for k, v in a["directions"].items()}
        root = tuple(a["root_translation"])
        yaw = a["root_yaw_deg"]
    else:
        directions = {}
        for name in a["directions"]:
            other = b["directions"].get(name)
            directions[name] = (
                _nlerp(a["directions"][name], other, blend)
                if other is not None
                else tuple(a["directions"][name])
            )
        root = _lerp3(a["root_translation"], b["root_translation"], blend)
        yaw = _lerp_angle(a["root_yaw_deg"], b["root_yaw_deg"], blend)
    ground = float(clip.get("ground_offset", 0.0))
    origin = clip.get("origin") or [0.0, 0.0, 0.0]
    absolute = (root[0], root[1], root[2] + ground)
    return {
        "directions": directions,
        "root_translation": absolute,
        # What the renderer should actually apply: displacement from the clip's shared origin, so
        # the shot's own staging still decides where the pair stands while the capture decides how
        # they move and how far apart they are.
        "root_offset": (absolute[0] - origin[0], absolute[1] - origin[1], absolute[2] - origin[2]),
        "root_yaw_deg": yaw,
        "clip_frames": (i0, i1, blend),
    }


def actor_ids(clip: dict[str, Any]) -> list[str]:
    return [a["actor_id"] for a in clip["actors"]]
