"""Bake the CMU clip library named by ``cmu_clips.json``, and measure what came out.

Run it:

    uv run --project skills/video/blender_scene \
        python skills/video/blender_scene/mocap/bake_library.py [--only cmu_22_23_08] [--dry-run]

Every clip is measured as it is written, and the measurements go in ``clips/manifest.json`` beside
them: duration, how far each actor travels, how close the two get, and the smallest wrist-to-wrist
gap. Those are the numbers that say whether a clip is what its description claims - a clip tagged
``hold_hands_walk`` whose wrists never come within arm's reach is mislabelled, and the manifest is
where that shows up instead of on screen.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from mocap.clip import bake_trial, contact_gap, write_clip

RECIPE = Path(__file__).with_name("cmu_clips.json")


def _travel(frames: list[dict[str, Any]]) -> float:
    """Bounding-box diagonal of the root path in the ground plane, metres."""
    xy = np.array([f["root_translation"][:2] for f in frames])
    return float(np.linalg.norm(xy.max(axis=0) - xy.min(axis=0)))


def _speed(frames: list[dict[str, Any]], fps: int) -> dict[str, float]:
    """Ground-plane root speed in m/s: the peak, and the fastest sustained second.

    The posture tag says *running*; this says how fast, which is the difference between a sprint
    and a hurried walk. Both numbers are needed because they disagree in the interesting cases: a
    scramble peaks high for two frames and averages low, a jog does the reverse. Measured over the
    root only, so it is speed across the floor and not limb speed.
    """
    xy = np.array([f["root_translation"][:2] for f in frames])
    if len(xy) < 2:
        return {"peak": 0.0, "sustained_1s": 0.0}
    step = np.linalg.norm(np.diff(xy, axis=0), axis=1) * fps
    window = min(fps, len(step))
    means = np.convolve(step, np.ones(window) / window, mode="valid")
    return {"peak": round(float(step.max()), 3), "sustained_1s": round(float(means.max()), 3)}


def _root_gap(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> tuple[float, float]:
    d = [
        float(np.linalg.norm(np.array(x["root_translation"]) - np.array(y["root_translation"])))
        for x, y in zip(a, b, strict=True)
    ]
    return round(min(d), 4), round(max(d), 4)


def measure(doc: dict[str, Any]) -> dict[str, Any]:
    actors = doc["actors"]
    out: dict[str, Any] = {
        "frames": doc["frame_count"],
        "duration_s": round(doc["frame_count"] / doc["fps"], 3),
        "ground_offset": doc["ground_offset"],
        "travel_m": {a["actor_id"]: round(_travel(a["frames"]), 3) for a in actors},
        "speed_mps": {a["actor_id"]: _speed(a["frames"], doc["fps"]) for a in actors},
        "yaw_range_deg": {
            a["actor_id"]: [
                round(min(f["root_yaw_deg"] for f in a["frames"]), 1),
                round(max(f["root_yaw_deg"] for f in a["frames"]), 1),
            ]
            for a in actors
        },
    }
    if len(actors) == 2:
        lo, hi = _root_gap(actors[0]["frames"], actors[1]["frames"])
        ids = (actors[0]["actor_id"], actors[1]["actor_id"])
        wrists = min(
            min(contact_gap(doc, ids[0], ids[1], x, y))
            for x in ("lwrist", "rwrist")
            for y in ("lwrist", "rwrist")
        )
        out |= {
            "root_gap_m": [lo, hi],
            "closest_wrists_m": round(wrists, 4),
        }
    return out


LOCOMOTION = frozenset({"walking", "running"})
"""Postures that carry the body across the floor, so travel is expected rather than suspicious."""

RUN_MPS = 2.2
"""Sustained speed that separates a run from a walk, read off this library rather than a textbook.
Every clip measured, ranked: the walking clips top out at 1.35 m/s, the two-person clips tagged
running sustain 1.21-1.79 m/s (they are scrambles and football drills, not runs), and the solo
CMU sprint trials sustain 2.72 and 3.18 m/s. The widest gap anywhere above 1 m/s is 1.79 -> 2.53,
so the threshold sits in it. Two clips land above it without a running tag and the flag says so:
a chase in blindfold tag, which is a real run, and the two-person whip, where the root is swung
across the floor at 2.5 m/s by somebody else - the one case this proxy cannot tell apart."""


def flags(entry: dict[str, Any], m: dict[str, Any]) -> list[str]:
    """Advisory only. A flag names what to look at first, it does not veto a clip."""
    out: list[str] = []
    contact = set(entry.get("contact", []))
    posture = set(entry.get("posture", []))
    travel = max(m["travel_m"].values())
    fastest = max(s["sustained_1s"] for s in m["speed_mps"].values())
    if "closest_wrists_m" in m and contact & {"hands"} and m["closest_wrists_m"] > 0.6:
        out.append(f"tagged hand contact but wrists never closer than {m['closest_wrists_m']} m")
    if "root_gap_m" in m and contact & {"torso", "shoulder"} and m["root_gap_m"][0] > 1.0:
        out.append(f"tagged body contact but roots never closer than {m['root_gap_m'][0]} m")
    if posture & LOCOMOTION and travel < 0.5:
        out.append(f"tagged {'/'.join(sorted(posture & LOCOMOTION))} but travels only {travel} m")
    if not posture & LOCOMOTION and travel > 2.0:
        out.append(f"not tagged walking or running but travels {travel} m")
    if "running" in posture and fastest < RUN_MPS:
        out.append(f"tagged running but sustains only {fastest} m/s over a second")
    if "running" not in posture and fastest >= RUN_MPS:
        out.append(f"not tagged running but sustains {fastest} m/s")
    if m["duration_s"] < 1.0:
        out.append(f"very short: {m['duration_s']} s")
    return out


def actors_for(entry: dict[str, Any], pairs: dict[str, str]) -> list[tuple[str, str]]:
    """``[(actor_id, subject), ...]`` for one recipe entry.

    ``pair: "18"`` bakes both halves of an A/B two-person take into one clip. ``subject: "35"``
    bakes one performer alone, which is what a solo action - a sprint, a walk cycle - is: there is
    no second subject to synchronise with, and inventing actor ``b`` would give the renderer an
    actor id that the clip cannot answer for.
    """
    if ("pair" in entry) == ("subject" in entry):
        raise ValueError(
            f"clip entry {entry.get('trial', '?')} must name exactly one of pair or subject"
        )
    if "pair" in entry:
        a = str(entry["pair"])
        if a not in pairs:
            raise ValueError(f"pair {a} is not in the recipe's pairs map")
        return [("a", a), ("b", pairs[a])]
    return [("a", str(entry["subject"]))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--recipe", default=str(RECIPE))
    ap.add_argument("--only", default="", help="comma-separated clip names")
    ap.add_argument("--dry-run", action="store_true", help="measure and report, write nothing")
    ap.add_argument("--clips-dir", default="", help="override the recipe's clips_dir")
    args = ap.parse_args()

    recipe = json.loads(Path(args.recipe).read_text())
    clips_dir = Path(args.clips_dir or recipe["clips_dir"])
    wanted = {n.strip() for n in args.only.split(",") if n.strip()}
    fps = int(recipe["fps"])
    pairs = recipe["pairs"]

    entries: list[dict[str, Any]] = []
    flagged = 0
    for entry in recipe["clips"]:
        cast = actors_for(entry, pairs)
        name = f"cmu_{'_'.join(s for _, s in cast)}_{entry['trial']}"
        if wanted and name not in wanted:
            continue
        doc = bake_trial(
            recipe["mocap_root"],
            cast,
            entry["trial"],
            fps=fps,
            descriptions_root=recipe["descriptions_root"],
            description=entry["description"],
            name=name,
        )
        m = measure(doc)
        fl = flags(entry, m)
        flagged += bool(fl)
        doc["tags"] = {
            "interaction": entry["interaction"],
            "affection": entry["affection"],
            "contact": entry["contact"],
            "posture": entry["posture"],
            "people": len(doc["actors"]),
        }
        doc["measured"] = m
        if not args.dry_run:
            write_clip(doc, clips_dir)
        entries.append(
            {
                "name": name,
                "subjects": [subject for _, subject in cast],
                "trial": entry["trial"],
                "description": entry["description"],
                **doc["tags"],
                "measured": m,
                "flags": fl,
            }
        )
        note = ("  FLAG: " + "; ".join(fl)) if fl else ""
        gap = m.get("closest_wrists_m")
        print(
            f"{name:18s} {m['frames']:4d}f {m['duration_s']:6.2f}s "
            f"travel {max(m['travel_m'].values()):5.2f}m "
            f"{max(s['sustained_1s'] for s in m['speed_mps'].values()):5.2f}m/s "
            f"wrists {gap if gap is not None else '-':>7} "
            f"{entry['affection']:10s} {entry['interaction']}{note}"
        )

    manifest = {
        "$comment": (
            "Written by mocap/bake_library.py from cmu_clips.json. Every number is measured from "
            "the baked clip, not copied from the recipe. Flags are advisory: they name the clips "
            "whose measurements disagree with their tags."
        ),
        "schema": "cf.clip_library.v1",
        "dataset": recipe["dataset"],
        "fps": fps,
        "clips": entries,
        "counts": {
            "clips": len(entries),
            "flagged": flagged,
            "by_affection": {
                k: sum(1 for e in entries if e["affection"] == k)
                for k in sorted({e["affection"] for e in entries})
            },
        },
    }
    if not args.dry_run:
        path = clips_dir / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
        print(f"\nwrote {len(entries)} clips + manifest to {clips_dir}")
    print(f"flagged {flagged}/{len(entries)}; by affection: {manifest['counts']['by_affection']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
