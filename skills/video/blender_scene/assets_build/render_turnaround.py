"""Render a character's turnaround (structure references for HiDream subject conditioning).

    uv run --project skills/video/blender_scene python skills/video/blender_scene/assets_build/render_turnaround.py \
        <character_name> [--assets ROOT] [--size 768x768]

Nine one-frame shots (front, left45, left90, back, right45, right90, closeup_face, wide_full,
t_pose) rendered
with the normal skill runner into <assets>/characters/<name>/turnaround/<view>/, plus index.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]


def _views(height: float) -> dict[str, dict]:
    eye, target = 0.62 * height, (0.0, 0.0, 0.55 * height)
    d_full, d_close = 2.6 * height, 0.9 * height

    def cam(angle_deg: float, dist: float, z: float, lens: float, look=target) -> dict:
        a = math.radians(angle_deg)
        return {
            "keyframes": [
                {
                    "frame_index": 0,
                    "position": [
                        round(dist * math.sin(a), 4),
                        round(-dist * math.cos(a), 4),
                        round(z, 4),
                    ],
                    "look_at": list(look),
                    "lens_mm": lens,
                }
            ]
        }

    return {
        "front": {"camera": cam(0, d_full, eye, 50), "pose": "stand_relaxed"},
        "left45": {"camera": cam(-45, d_full, eye, 50), "pose": "stand_relaxed"},
        "left90": {"camera": cam(-90, d_full, eye, 50), "pose": "stand_relaxed"},
        "back": {"camera": cam(180, d_full, eye, 50), "pose": "stand_relaxed"},
        "right45": {"camera": cam(45, d_full, eye, 50), "pose": "stand_relaxed"},
        # Both profiles, not one. With only left90 the asset review's profile_coverage check fails
        # by design: half the body is never looked at, and an asymmetry on the unseen side - a
        # collapsed shoulder, a foot rotated the wrong way - passes a bilateral-symmetry test done
        # on the t-pose's keypoints while being plainly visible in a render nobody made.
        "right90": {"camera": cam(90, d_full, eye, 50), "pose": "stand_relaxed"},
        "closeup_face": {
            "camera": cam(0, d_close, 0.9 * height, 85, (0.0, 0.0, 0.9 * height)),
            "pose": "stand_relaxed",
        },
        "wide_full": {"camera": cam(-20, 1.4 * d_full, eye, 35), "pose": "stand_relaxed"},
        "t_pose": {"camera": cam(0, d_full, eye, 50), "pose": "t_pose"},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument(
        "--assets", default=os.environ.get("CF_BLENDER_ASSETS", "/mnt/fast/models/blender-assets")
    )
    ap.add_argument("--size", default="768x768")
    args = ap.parse_args(argv)
    assets = Path(args.assets)
    char_dir = assets / "characters" / args.name
    meta = json.loads((char_dir / f"{args.name}.asset.json").read_text())
    height = float(meta["height_m"])
    w, h = (int(v) for v in args.size.split("x"))
    turn_dir = char_dir / "turnaround"
    turn_dir.mkdir(exist_ok=True)
    index: dict[str, dict] = {}
    for view, cfg in _views(height).items():
        spec = {
            "shot_id": f"shot_turn{hashlib.sha256(f'{args.name}:{view}'.encode()).hexdigest()[:10]}",
            "order": 0,
            "seed": 1,
            "frame_count": 1,
            "fps": 24,
            "width": w,
            "height": h,
            "camera": cfg["camera"],
            "characters": [
                {
                    "id": "subject",
                    "asset": args.name,
                    "pose": {"kind": "library", "name": cfg["pose"]},
                }
            ],
            "props": [],
            "environment": {
                "ground": {"enabled": True, "size": 20.0, "color": [0.4, 0.4, 0.4]},
                "background_color": [0.12, 0.12, 0.13],
            },
            "lighting": {"preset": "studio", "key_azimuth_deg": 30.0, "key_elevation_deg": 40.0},
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
            },
            "anchor_frames": [0],
            "motion_prompt": f"{args.name} turnaround: {view}",
        }
        out = turn_dir / view
        spec_path = turn_dir / f"{view}.spec.json"
        spec_path.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n")
        cmd = [
            sys.executable,
            str(SKILL_DIR / "render.py"),
            str(spec_path),
            str(out),
            "--assets",
            str(assets),
            "--timeout",
            "600",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        summary = json.loads(proc.stdout.strip().splitlines()[-1]) if proc.stdout.strip() else {}
        if proc.returncode != 0 or not summary.get("ok"):
            raise SystemExit(f"{view}: render failed: {summary.get('error') or proc.stderr[-600:]}")
        rgb = out / "rough_rgb" / "frames" / "0000.png"
        index[view] = {
            "dir": str(out.relative_to(char_dir)),
            "rgb": str(rgb.relative_to(char_dir)),
            "rgb_sha256": hashlib.sha256(rgb.read_bytes()).hexdigest(),
            "pose": cfg["pose"],
            "camera": cfg["camera"]["keyframes"][0],
        }
    (turn_dir / "index.json").write_text(
        json.dumps(
            {
                "schema": "cf.turnaround.v1",
                "character": args.name,
                "height_m": height,
                "size": [w, h],
                "views": index,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n"
    )
    print(json.dumps({"ok": True, "character": args.name, "views": sorted(index)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
