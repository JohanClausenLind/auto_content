"""Pose data (pure Python): cf.pose.v1 / cf.clip.v1 documents and clip sampling."""

from __future__ import annotations

from typing import Any

from camera import slerp

Quat = tuple[float, float, float, float]

IDENTITY: Quat = (1.0, 0.0, 0.0, 0.0)


def bone_rotations(pose: dict[str, Any]) -> dict[str, Quat]:
    bones = pose.get("bones", {})
    return {name: tuple(b.get("rotation_quaternion", IDENTITY)) for name, b in bones.items()}  # type: ignore[misc]


def sample_clip(
    clip: dict[str, Any],
    frame: int,
    *,
    fps: int,
    speed: float = 1.0,
    offset_frames: int = 0,
    loop: bool = True,
) -> dict[str, Quat]:
    """Pose at ``frame`` for a clip authored at ``clip['fps']`` with per-frame bone rotations.
    Slerps between neighbouring clip frames; loops or holds the last frame."""
    frames = clip["frames"]
    n = len(frames)
    if n == 0:
        return {}
    clip_fps = float(clip.get("fps", fps))
    t = (frame + offset_frames) * speed * clip_fps / fps
    if loop:
        t = t % n
    else:
        t = min(t, n - 1)
    i0 = int(t)
    i1 = (i0 + 1) % n if loop else min(i0 + 1, n - 1)
    f = t - i0
    a, b = bone_rotations(frames[i0]), bone_rotations(frames[i1])
    out: dict[str, Quat] = {}
    for name in sorted(set(a) | set(b)):
        qa, qb = a.get(name, IDENTITY), b.get(name, IDENTITY)
        out[name] = slerp(qa, qb, f) if f > 0.0 else qa
    return out
