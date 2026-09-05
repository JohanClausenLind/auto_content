"""Bake a CMU trial into a ``cf.clip.v2`` motion clip.

``cf.clip.v1`` stores a quaternion per bone per frame. That was measured to be the wrong thing to
store: quaternions solved on ``man_01`` are wrong on the other three MPFB characters by a mean of
11.4 degrees and up to 22.6 degrees, because MPFB fits the rig to each mesh, so rest bone
orientations differ per character. A clip baked for one body is not a clip.

``cf.clip.v2`` stores, per frame, the **world direction of each anatomical segment** plus the root
translation and a ground offset. Directions are a property of the motion, not of the skeleton
performing it, so one clip drives every character; the aim solve happens at render time against
whatever rig is loaded (``bl/retarget.py``). It is also smaller: 24 segments x 3 floats beats 35
bones x 4.

Two-person clips are baked as one file. CMU's A/B subjects are the same take recorded twice and
share one world frame, so ``actors`` holds both and their contact is preserved exactly as captured
rather than reconstructed by staging two clips next to each other.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from mocap.asf_amc import Posed, Skeleton, fk, parse_amc, parse_asf, to_blender, trial_paths

CLIP_SCHEMA = "cf.clip.v2"
BAKER_VERSION = "0.1.0"

# The anatomical segments a clip carries. These are CMU bone names, chosen because every one of
# them has a direction that a humanoid rig can be aimed along. Fingers, thumbs and toes are left
# out: CMU's hand data is a single rigid "lhand" and the MPFB hand has 20 bones, so aiming them
# from this source would invent detail. Segment -> the MPFB bones it drives lives in bl/retarget.py.
SEGMENTS: tuple[str, ...] = (
    "lowerback",
    "upperback",
    "thorax",
    "lowerneck",
    "upperneck",
    "head",
    "lclavicle",
    "lhumerus",
    "lradius",
    "lwrist",
    "lhand",
    "rclavicle",
    "rhumerus",
    "rradius",
    "rwrist",
    "rhand",
    "lhipjoint",
    "lfemur",
    "ltibia",
    "lfoot",
    "rhipjoint",
    "rfemur",
    "rtibia",
    "rfoot",
)

FEET = ("lfoot", "rfoot", "ltoes", "rtoes")


@dataclass(frozen=True)
class ActorTrack:
    """One performer's motion: per-frame segment directions, root translation and root rotation."""

    actor_id: str
    subject: str
    trial: str
    frames: list[dict[str, Any]]
    ground_offset: float
    """Metres to ADD to z so the lowest foot in the whole clip sits on z=0. Negative lowers a
    figure that hovers, positive lifts one that sinks; either way it is one constant per clip, so
    applying it cannot introduce a bob that the capture did not have."""


def _round3(v: np.ndarray) -> list[float]:
    return [round(float(x), 5) for x in v]


def _root_yaw_deg(posed: Posed) -> float:
    """Facing direction in the Blender XY plane, degrees, from the pelvis-to-shoulder frame.

    Stored so a retrieval query can ask for a camera angle relative to where the actor faces
    without loading the whole clip.
    """
    forward = posed.rotation["root"] @ np.array([0.0, 0.0, 1.0])
    f = to_blender(forward)
    return round(math.degrees(math.atan2(f[1], f[0])), 2)


def bake_actor(
    skeleton: Skeleton,
    frames: list[dict[str, list[float]]],
    *,
    actor_id: str,
    subject: str,
    trial: str,
    step: int,
) -> ActorTrack:
    """Evaluate every ``step``-th source frame into world segment directions (Blender Z-up)."""
    out: list[dict[str, Any]] = []
    lows: list[float] = []
    for index in range(0, len(frames), step):
        posed = fk(skeleton, frames[index])
        directions: dict[str, list[float]] = {}
        for segment in SEGMENTS:
            if segment not in posed.head:
                continue
            d = posed.direction(segment)
            if float(np.linalg.norm(d)) < 1e-9:
                continue
            directions[segment] = _round3(to_blender(d))
        root = to_blender(posed.head["root"])
        lows.append(min(to_blender(posed.tail[b])[2] for b in FEET if b in posed.tail))
        out.append(
            {
                "source_frame": index,
                "directions": directions,
                "root_translation": _round3(root),
                "root_yaw_deg": _root_yaw_deg(posed),
            }
        )
    return ActorTrack(
        actor_id=actor_id,
        subject=subject,
        trial=trial,
        frames=out,
        ground_offset=round(-min(lows), 5) if lows else 0.0,
    )


def source_fps(descriptions_root: Path | None, subject: str, default: int = 120) -> int:
    """CMU's frame rate is not in the AMC file, only in the web index. 14 subjects are 60 fps."""
    if descriptions_root is None:
        return default
    page = Path(descriptions_root) / f"subject_{subject}.html"
    if not page.is_file():
        return default
    import re

    rates = {
        int(m) for m in re.findall(r"<TD>\s*(60|120)\s*</TD>", page.read_text(errors="replace"))
    }
    if not rates:
        return default
    # A subject listing both rates is baked from the higher one; decimation handles the rest.
    return max(rates)


def bake_trial(
    mocap_root: str | Path,
    pairs: list[tuple[str, str]],
    trial: str,
    *,
    fps: int = 24,
    descriptions_root: str | Path | None = None,
    description: str = "",
    name: str | None = None,
    loop: bool = False,
) -> dict[str, Any]:
    """One ``cf.clip.v2`` document from one CMU trial.

    ``pairs`` is ``[(actor_id, subject), ...]`` — one entry for a solo clip, two for a two-person
    trial where subject A and subject B are the same take. Every actor is decimated with the same
    step so the two tracks stay frame-aligned.
    """
    if not pairs:
        raise ValueError("bake_trial needs at least one (actor_id, subject) pair")
    root = Path(mocap_root)
    desc_root = Path(descriptions_root) if descriptions_root else None

    loaded: list[tuple[str, str, Skeleton, list[dict[str, list[float]]], int]] = []
    for actor_id, subject in pairs:
        asf, amc = trial_paths(root, subject, trial)
        skeleton = parse_asf(asf)
        frames = parse_amc(amc)
        loaded.append((actor_id, subject, skeleton, frames, source_fps(desc_root, subject)))

    rates = {rate for *_, rate in loaded}
    if len(rates) != 1:
        raise ValueError(f"trial {trial}: actors disagree on source fps: {sorted(rates)}")
    src_fps = rates.pop()
    step = max(1, round(src_fps / fps))
    counts = {len(frames) for _, _, _, frames, _ in loaded}
    if len(counts) != 1:
        raise ValueError(f"trial {trial}: actors have different frame counts {sorted(counts)}")

    tracks = [
        bake_actor(skeleton, frames, actor_id=actor_id, subject=subject, trial=trial, step=step)
        for actor_id, subject, skeleton, frames, _ in loaded
    ]
    # One ground offset for the whole clip, so a two-person clip cannot have its actors on
    # different floors.
    ground = round(max(t.ground_offset for t in tracks), 5)
    # One origin for the whole clip: the mean of the actors' frame-0 roots, projected to the
    # floor. Root translation is consumed as a displacement FROM this point, never as an absolute
    # position, for two reasons. A capture's absolute coordinates are wherever the CMU lab put its
    # origin, so applying them would teleport the characters and discard the shot's own staging.
    # And both actors must be measured from the SAME origin, or the distance between them - the
    # whole point of a two-person clip - changes with where each one happened to start.
    # The z component matters as much as x and y, and for a different reason: a rig already stands
    # with its root at hip height, so adding the capture's absolute hip height on top lifts the
    # figure a metre off the floor - which is exactly what the first render showed, legs filling
    # the frame and heads cut off. Anchoring z at the frame-0 hip height makes the vertical channel
    # a CHANGE in hip height: about zero while standing, negative when the actor sits or kneels.
    first = [t.frames[0]["root_translation"] for t in tracks]
    origin = [
        round(sum(p[0] for p in first) / len(first), 5),
        round(sum(p[1] for p in first) / len(first), 5),
        round(sum(p[2] for p in first) / len(first) + ground, 5),
    ]
    subjects = "_".join(s for _, s in pairs)
    return {
        "schema": CLIP_SCHEMA,
        "name": name or f"cmu_{subjects}_{trial}",
        "fps": fps,
        "loop": loop,
        "frame_count": len(tracks[0].frames),
        "ground_offset": ground,
        "origin": origin,
        "up_axis": "z",
        "units": "m",
        "segments": list(SEGMENTS),
        "actors": [
            {
                "actor_id": t.actor_id,
                "subject": t.subject,
                "trial": t.trial,
                "frames": t.frames,
            }
            for t in tracks
        ],
        "source": {
            "dataset": "cmu-mocap",
            "subjects": [s for _, s in pairs],
            "trial": trial,
            "source_fps": src_fps,
            "decimation_step": step,
            "description": description,
        },
        "baker": {"module": "mocap.clip", "version": BAKER_VERSION},
    }


def write_clip(doc: dict[str, Any], clips_dir: str | Path) -> Path:
    """Write ``<clips_dir>/<name>.json``. Deterministic bytes, so a rebake is a no-op or a diff."""
    out = Path(clips_dir) / f"{doc['name']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return out


def contact_gap(doc: dict[str, Any], a: str, b: str, segment_a: str, segment_b: str) -> list[float]:
    """Per-frame distance between two actors' segment tails. Used to verify a contact clip.

    Positions are reconstructed from root translation plus the chained directions, which is exactly
    what the renderer will do, so a small gap here means a small gap on screen.
    """
    actors = {act["actor_id"]: act for act in doc["actors"]}
    fa, fb = actors[a]["frames"], actors[b]["frames"]
    return [
        float(
            np.linalg.norm(
                np.array(x["root_translation"])
                + np.array(x["directions"].get(segment_a, [0, 0, 0]))
                - np.array(y["root_translation"])
                - np.array(y["directions"].get(segment_b, [0, 0, 0]))
            )
        )
        for x, y in zip(fa, fb, strict=True)
    ]
