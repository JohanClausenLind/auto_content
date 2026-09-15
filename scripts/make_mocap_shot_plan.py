"""Build a ShotPlan whose characters are driven by baked CMU mocap clips."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLIPS = Path("/mnt/fast/models/blender-assets/clips")

# What each rig looks like once drawn, the SAME description in every shot: the bare mesh renders as
# a grey mannequin, so clothing continuity lives here or nowhere. Descriptions, not negations.
APPEARANCE: dict[str, str] = {
    "man_01": "a man in his thirties, dark cropped hair, a charcoal wool overcoat over a grey"
    " crew-neck, dark trousers, brown leather boots",
    "woman_01": "a woman in her thirties, shoulder-length auburn hair, a rust-red belted coat,"
    " dark jeans, tan ankle boots",
    "woman_02": "a woman in her sixties, short silver hair, a deep green quilted jacket, navy"
    " trousers, grey walking shoes",
}

# Six shots of two people from the affection half of the library. `azimuth_deg` is the camera's
# bearing relative to the pair's own facing; `body_fraction` the taller figure's target height.
SHOTS = [
    {
        "clip": "cmu_18_19_01",
        "cast": [("man", "man_01", "a"), ("woman", "woman_01", "b")],
        "azimuth_deg": 35.0,
        "elevation_deg": 6.0,
        "body_fraction": 0.62,
        "lens_mm": 40.0,
        "description": "they meet and shake hands",
    },
    {
        "clip": "cmu_20_21_02",
        "cast": [("man", "man_01", "a"), ("woman", "woman_01", "b")],
        "azimuth_deg": 115.0,
        "elevation_deg": 3.0,
        "body_fraction": 0.55,
        "lens_mm": 50.0,
        "description": "arms linked, walking together",
    },
    {
        "clip": "cmu_22_23_04",
        "cast": [("man", "man_01", "b"), ("woman", "woman_01", "a")],
        "azimuth_deg": -60.0,
        "elevation_deg": 8.0,
        "body_fraction": 0.78,
        "lens_mm": 65.0,
        "description": "one hand on her shoulder, comforting her",
    },
    {
        "clip": "cmu_22_23_03",
        "cast": [("man", "man_01", "b"), ("woman", "woman_01", "a")],
        "azimuth_deg": 150.0,
        "elevation_deg": 14.0,
        "body_fraction": 0.72,
        "lens_mm": 55.0,
        "description": "she sits with her face in her hands; he kneels to comfort her",
    },
    {
        "clip": "cmu_22_23_08",
        "cast": [("man", "man_01", "a"), ("woman", "woman_01", "b")],
        "azimuth_deg": -25.0,
        "elevation_deg": 4.0,
        "body_fraction": 0.66,
        "lens_mm": 45.0,
        "description": "holding hands, swinging their arms as they walk",
    },
    {
        "clip": "cmu_22_23_10",
        "cast": [("man", "man_01", "a"), ("woman", "woman_02", "b")],
        "azimuth_deg": 75.0,
        "elevation_deg": 10.0,
        "body_fraction": 0.80,
        "lens_mm": 70.0,
        "description": "he shelters her from something out of frame",
    },
]

WIDTH, HEIGHT, FPS = 1024, 576, 24
SENSOR_MM = 36.0
FIGURE_HEIGHT_M = 1.76  # man_01.asset.json


def load(name: str) -> dict:
    path = CLIPS / f"{name}.json"
    if not path.is_file():
        raise SystemExit(f"clip not baked: {path}. Run mocap/bake_library.py first.")
    return json.loads(path.read_text())


def pair_bounds(clip: dict) -> tuple[tuple[float, float], float, float]:
    """``(centre_xy, radius, subject_top_z)`` of everything the pair does, in clip-offset space."""
    origin = clip["origin"]
    xs: list[float] = []
    ys: list[float] = []
    hip_top = 0.0
    standing_hip = 0.98  # root height of a standing adult on this rig
    for actor in clip["actors"]:
        for f in actor["frames"]:
            r = f["root_translation"]
            xs.append(r[0] - origin[0])
            ys.append(r[1] - origin[1])
            hip_top = max(hip_top, r[2] + clip["ground_offset"])
    cx, cy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0
    radius = max(max(abs(x - cx) for x in xs), max(abs(y - cy) for y in ys))
    head_above_hip = FIGURE_HEIGHT_M - standing_hip
    return (cx, cy), radius, hip_top + head_above_hip


def solve_camera(clip: dict, shot: dict) -> dict:
    """Place the camera so the subject fills ``body_fraction`` of the frame height, whole."""
    (cx, cy), radius, top = pair_bounds(clip)
    lens = float(shot["lens_mm"])
    sensor_h = SENSOR_MM * HEIGHT / WIDTH
    d = lens * top / (shot["body_fraction"] * sensor_h)
    # Both people must fit horizontally: half-width the sensor sees at distance d.
    half_w_at_d = d * (SENSOR_MM / 2.0) / lens
    needed = radius + 0.55  # 0.55 m of body width plus a little air
    if needed > half_w_at_d:
        d *= needed / half_w_at_d
    az = math.radians(shot["azimuth_deg"])
    el = math.radians(shot["elevation_deg"])
    look_z = top / 2.0
    pos = (
        cx + d * math.cos(el) * math.cos(az),
        cy + d * math.cos(el) * math.sin(az),
        look_z + d * math.sin(el),
    )
    return {
        "preset": "static",
        "sensor_width_mm": SENSOR_MM,
        "clip_start": 0.05,
        "clip_end": 100.0,
        "keyframes": [
            {
                "frame_index": 0,
                "position": [round(v, 4) for v in pos],
                "look_at": [round(cx, 4), round(cy, 4), round(look_z, 4)],
                "lens_mm": lens,
                "focus_distance": round(d, 4),
                "easing_to_next": "ease_in_out",
                "rotation_euler_deg": None,
            }
        ],
    }


def build() -> dict:
    shots = []
    for order, shot in enumerate(SHOTS):
        clip = load(shot["clip"])
        frames = int(clip["frame_count"])
        camera = solve_camera(clip, shot)
        characters = [
            {
                "id": cid,
                "asset": asset,
                "appearance": APPEARANCE[asset],
                "transform": {"position": [0.0, 0.0, 0.0], "yaw_deg": 0.0, "scale": 1.0},
                "pose": {
                    "kind": "segments",
                    "name": shot["clip"],
                    "actor": actor,
                    "speed": 1.0,
                    "offset_frames": 0,
                    "loop": False,
                },
                "seg_id": i + 1,
                "reference_image_sha256": [],
            }
            for i, (cid, asset, actor) in enumerate(shot["cast"])
        ]
        shots.append(
            {
                "schema_version": 1,
                "shot_id": f"sht_mocap_{order:04d}",
                "order": order,
                "beat_id": f"bea_mocap_take{order:02d}",
                "seed": 1000 + order,
                "frame_count": frames,
                "fps": FPS,
                "width": WIDTH,
                "height": HEIGHT,
                "camera": camera,
                "characters": characters,
                "props": [],
                "environment": {
                    "background_color": [0.07, 0.08, 0.11],
                    "ground": {
                        "enabled": True,
                        "color": [0.3, 0.3, 0.33],
                        "size": 60.0,
                        "seg": False,
                    },
                    "walls": None,
                },
                "lighting": {
                    "preset": "exterior_dusk",
                    "key_azimuth_deg": round(shot["azimuth_deg"] + 140.0, 1),
                    "key_elevation_deg": 22.0,
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
                    "depth_range": "auto",
                    "canny": {"low": 100, "high": 200},
                    "frames": [0, max(0, frames // 2), frames - 1],
                },
                "anchor_frames": [0, max(1, frames // 2)] if frames > 2 else [0],
                "motion_prompt": shot["description"],
                "description": f"{shot['description']} (mocap {shot['clip']})",
            }
        )
    return {
        "schema_version": 1,
        "plan_id": "shp_two_hander_mocap",
        "deliverable_id": "dlv_two_hander_mocap",
        "story_plan_hash": None,
        "planner": "fixture",
        "planner_version": "0.1.0",
        "shots": shots,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="fixtures/shots/two_hander_mocap.json")
    args = ap.parse_args()
    plan = build()

    from content_factory.schemas.shots import SegmentClipPose, ShotPlan

    validated = ShotPlan.model_validate(plan)
    out = REPO / args.out
    out.write_text(
        json.dumps(json.loads(validated.model_dump_json()), indent=1, sort_keys=True) + "\n"
    )

    print(f"wrote {out} ({len(validated.shots)} shots)")
    for s in validated.shots:
        cam = s.camera.keyframes[0]
        d = cam.focus_distance or 0.0
        sensor_h = SENSOR_MM * s.height / s.width
        lead = s.characters[0].pose
        if not isinstance(lead, SegmentClipPose):
            msg = f"{s.shot_id}: expected a cf.clip.v2 segment pose, got {lead.kind}"
            raise TypeError(msg)
        _, _, top = pair_bounds(load(lead.name))
        frac = (cam.lens_mm or 50.0) * top / (d * sensor_h)
        cast = ", ".join(
            f"{c.id}={c.pose.name}:{c.pose.actor}"
            if isinstance(c.pose, SegmentClipPose)
            else f"{c.id}={c.pose.kind}"
            for c in s.characters
        )
        print(
            f"  {s.shot_id} {s.frame_count:4d}f  d={d:5.2f}m lens={cam.lens_mm:.0f}mm "
            f"predicted body height {frac:.2f}  {cast}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
