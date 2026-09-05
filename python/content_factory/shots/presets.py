"""Camera presets: pure arithmetic from (preset, frame_count, subject height, frame shape).

Conventions: metres, Blender Z-up. The subject stands at the origin facing -Y (Blender's front
view), so a camera on the -Y side sees its face. Two keyframes (first and last frame) are enough
for every preset because the easing lives on the keyframe; ``static`` needs one.

The distances here were calibrated by eye on a 16:9 frame, and a fixed multiple of subject height
is only a framing on the aspect it was chosen for. A 35 mm lens sees much more vertically in a
portrait frame, so the same distance leaves the figure at a fraction of the height it had in
widescreen: measured on a rendered vertical film, every preset shot opened at 0.216 of frame
height, under the 0.33 where the image model stops reading the pose skeleton. So the whole camera
rig is scaled about its own look-at point by :func:`aspect_scale`. Scaling about the look-at point
rather than the distance alone is what keeps a preset a preset: every angle, every sweep and every
crane arc is preserved exactly, and only the distance changes. 16:9 scales by one, so nothing on
that aspect moves at all.
"""

from __future__ import annotations

import math

from content_factory.schemas.shots import CameraKeyframe, CameraPreset

PLANNER_VERSION = "0.1.0"

# Scene kind (schemas/scenes.py) -> camera preset. Kinds not listed fall back to ``static``.
SCENE_KIND_PRESET: dict[str, CameraPreset] = {
    "title": CameraPreset.slow_push_in,
    "section_intro": CameraPreset.slow_push_in,
    "chapter_transition": CameraPreset.crane_down,
    "big_number": CameraPreset.static,
    "chart": CameraPreset.slow_pull_out,
    "ranking": CameraPreset.pan_right,
    "comparison": CameraPreset.pan_left,
    "data_table": CameraPreset.static,
    "timeline": CameraPreset.pan_right,
    "map": CameraPreset.crane_up,
    "flow_diagram": CameraPreset.orbit_right,
    "relationship_diagram": CameraPreset.orbit_left,
    "image": CameraPreset.slow_push_in,
    "screenshot": CameraPreset.static,
    "quote": CameraPreset.slow_push_in,
    "definition": CameraPreset.static,
    "bullet_sequence": CameraPreset.pan_right,
    "callout": CameraPreset.slow_push_in,
    "source_card": CameraPreset.static,
    "manim_asset": CameraPreset.static,
    "outro": CameraPreset.slow_pull_out,
}

_DEFAULT_LENS_MM = 35.0

_REFERENCE_ASPECT = 16.0 / 9.0
"""The aspect the distances below were chosen on. Everything else is corrected relative to it."""


def aspect_scale(width: int, height: int) -> float:
    """Factor to scale a camera's offset from its look-at point by, for a ``width`` x ``height``
    frame.

    A subject of height ``h`` at distance ``d`` through lens ``f`` on a sensor of height
    ``sensor_h`` fills ``f * h / (d * sensor_h)`` of the frame, and ``sensor_h`` is the sensor width
    times ``height / width``. Holding that fraction constant while the aspect changes means scaling
    ``d`` by the ratio of sensor heights, which reduces to this. One on 16:9, about 0.32 on 9:16.
    """
    if width <= 0 or height <= 0:
        msg = "frame width and height must be positive"
        raise ValueError(msg)
    return (width / height) / _REFERENCE_ASPECT


def _r(v: float) -> float:
    # Fixed 4-decimal grid: stable hashes, no float-formatting drift between platforms.
    return round(v, 4)


def _kf(
    frame: int, pos: tuple[float, float, float], target: tuple[float, float, float], lens: float
) -> CameraKeyframe:
    return CameraKeyframe(
        frame_index=frame,
        position=(_r(pos[0]), _r(pos[1]), _r(pos[2])),
        look_at=(_r(target[0]), _r(target[1]), _r(target[2])),
        lens_mm=lens,
        focus_distance=_r(math.dist(pos, target)),
    )


def camera_keyframes(
    preset: CameraPreset,
    frame_count: int,
    *,
    subject_height_m: float = 1.75,
    lens_mm: float = _DEFAULT_LENS_MM,
    width: int = 1024,
    height: int = 576,
) -> tuple[CameraKeyframe, ...]:
    """Keyframes for ``preset`` framing a subject of ``subject_height_m`` standing at the origin,
    in a ``width`` x ``height`` frame.

    The frame shape defaults to 16:9, which is the aspect these distances were chosen on, so a
    caller that does not care gets exactly what this function has always returned.
    """
    if frame_count < 1:
        msg = "frame_count must be >= 1"
        raise ValueError(msg)
    h = subject_height_m
    eye = 0.9 * h
    target = (0.0, 0.0, 0.6 * h)
    d_wide, d_mid, d_close = 2.6 * h, 1.8 * h, 1.15 * h
    last = frame_count - 1
    scale = aspect_scale(width, height)

    def _fit(pos: tuple[float, float, float]) -> tuple[float, float, float]:
        """Move a camera position towards its look-at point so the framing survives the aspect.

        Scaling the whole offset, not just the ground distance, is the point: it holds the camera's
        elevation and azimuth exactly, so a crane still cranes through the same arc and a pan still
        pans through the same angle. Only how far away it does it changes.
        """
        return tuple(target[i] + (pos[i] - target[i]) * scale for i in range(3))  # type: ignore[return-value]

    def two(
        a: tuple[float, float, float], b: tuple[float, float, float]
    ) -> tuple[CameraKeyframe, ...]:
        a, b = _fit(a), _fit(b)
        if last == 0:
            return (_kf(0, a, target, lens_mm),)
        return (_kf(0, a, target, lens_mm), _kf(last, b, target, lens_mm))

    if preset == CameraPreset.static:
        return (_kf(0, _fit((0.0, -d_wide, eye)), target, lens_mm),)
    if preset == CameraPreset.slow_push_in:
        return two((0.0, -d_wide, eye), (0.0, -d_close, eye))
    if preset == CameraPreset.slow_pull_out:
        return two((0.0, -d_close, eye), (0.0, -d_wide, eye))
    if preset == CameraPreset.pan_left:
        return two((0.6 * d_mid, -d_mid, eye), (-0.6 * d_mid, -d_mid, eye))
    if preset == CameraPreset.pan_right:
        return two((-0.6 * d_mid, -d_mid, eye), (0.6 * d_mid, -d_mid, eye))
    if preset in (CameraPreset.orbit_left, CameraPreset.orbit_right):
        # 60 degree arc around the subject, centred on the front view.
        sign = -1.0 if preset == CameraPreset.orbit_left else 1.0
        a0, a1 = math.radians(-30.0 * sign), math.radians(30.0 * sign)
        start = (d_mid * math.sin(a0), -d_mid * math.cos(a0), eye)
        end = (d_mid * math.sin(a1), -d_mid * math.cos(a1), eye)
        return two(start, end)
    if preset == CameraPreset.crane_up:
        return two((0.0, -d_wide, 0.5 * h), (0.0, -d_wide, 1.6 * h))
    if preset == CameraPreset.crane_down:
        return two((0.0, -d_wide, 1.6 * h), (0.0, -d_wide, 0.5 * h))
    msg = f"unknown camera preset {preset}"  # pragma: no cover - enum is exhaustive
    raise ValueError(msg)


_WHOLE_FIGURE_FRACTION = 0.30
"""How far into a distance-changing move the figure is still whole, as a fraction of the shot.

Measured off rendered layout boxes, not chosen: on ``slow_push_in`` the box stops fitting inside
the frame at 0.36 of the move in 16:9 and 0.32 in 9:16, and it reaches the full frame height by the
end. This sits under both. It is tied to this preset's own ``d_close`` and to smoothstep easing, so
re-measure it if either changes.
"""

_CLOSES_IN = frozenset({CameraPreset.slow_push_in})
"""Presets that end nearer the subject than they start, so their last frame is the cropped one.

``slow_pull_out`` has the same crop at the other end and is deliberately not here: its close end is
frame 0, which ``ShotSpec`` requires to be an anchor, so no choice of anchors can avoid it. That
one needs a camera change or nothing.
"""


def anchor_frames_for(preset: CameraPreset, frame_count: int) -> tuple[int, ...]:
    """Which frames of a ``preset`` shot should get a generated anchor image.

    The last frame is the obvious second anchor and it is the wrong one for a push-in: the move
    ends closer than a whole figure fits, so the anchor handed to the image model has a cropped
    body and fewer joints for the pose skeleton to place. The camera is left alone - a push-in that
    ends tight is a real choice, and the tail of the shot is the video model's job anyway - and the
    anchor is placed back where the whole figure is still in frame.
    """
    if frame_count < 1:
        msg = "frame_count must be >= 1"
        raise ValueError(msg)
    if frame_count == 1:
        return (0,)
    last = frame_count - 1
    if preset not in _CLOSES_IN:
        return (0, last)
    at = int(last * _WHOLE_FIGURE_FRACTION)
    return (0, at) if at > 0 else (0,)
