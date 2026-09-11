"""Does a lane hold ONE world and ONE pair of people across a motion?

    uv run python scripts/consistency_probe.py stage                 # Blender passes, 3 takes
    uv run python scripts/consistency_probe.py render --lane flux2   # one lane, all three sets
    uv run python scripts/consistency_probe.py score                 # matrices + contact sheets

The single-frame comparisons this repo has run so far answer "which model draws the best
picture". They cannot answer the question a film actually asks, which is whether frame 5 is the
same two people in the same street as frame 0. `qc.frame_review.consistency_matrix` measures the
world (hue + luma, every pair, not just neighbours) and says plainly that it cannot measure the
subject -- so the sheets exist to be looked at, and the number is the thing you cite afterwards.

Three CMU two-person takes, six frames sampled evenly across each. The takes are chosen for motion
the eye can check: a handshake either happens or it does not.

**Run the lanes grouped by GPU tenant, and restart ComfyUI between flux2 and ideogram.**
`LocalServices.ensure` swaps tenants for you (`exclusive_gpu` is on), so skeleton/hidream ->
flux2 costs one automatic handover. What it cannot see is a model swap *inside* one tenant: FLUX.2
and Ideogram 4 are both ComfyUI, and the first one loaded keeps ~18 GB cached in-process, so the
second OOMs the card. `content-factory services stop --tenant comfyui` between them is the fix.

Wall clock on this box, per frame: HiDream 2m13s (it snaps 1024x576 up to 2560x1440 -- eleven
fixed ~4 MP resolutions, upstream behaviour, not a setting), FLUX.2 turbo ~80 s, Ideogram ~50 s.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from content_factory.sequences.engine import ControlConditioning

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

FRAMES = 6
WIDTH, HEIGHT = 1024, 576

# clip, camera, and what the pair is doing. `body_fraction` is high on purpose: the mocap shot
# planner records that HiDream honours a pose skeleton only when the figures are large in frame
# and ignores it at 33 % body height, and the last batch of single frames came back with the
# figures small and the interaction unreadable. This is that finding applied.
SETS = {
    "A": {
        "clip": "cmu_18_19_01",
        "motion": "walk, shake hands",
        "azimuth_deg": 35.0,
        "elevation_deg": 6.0,
        "body_fraction": 0.78,
        "lens_mm": 40.0,
        "cast": [("man", "man_01", "a"), ("woman", "woman_01", "b")],
        "beats": [
            "they are still a stride apart, both walking forward",
            "they slow as they come together, arms starting to lift",
            "the near arms swing up and the hands reach toward each other",
            "their right hands meet and clasp",
            "they shake hands, shoulders square to each other",
            "the hands part again and fall back to their sides",
        ],
    },
    "B": {
        "clip": "cmu_20_21_04",
        "motion": "synchronized walk",
        "azimuth_deg": 100.0,
        "elevation_deg": 3.0,
        "body_fraction": 0.72,
        "lens_mm": 50.0,
        "cast": [("man", "man_01", "a"), ("woman", "woman_01", "b")],
        "beats": [
            "both are mid-stride, the near legs forward",
            "the stride swaps, the far legs come through",
            "they are both at full stride, walking in step",
            "the weight settles onto the forward feet",
            "the stride swaps back, arms swinging opposite the legs",
            "they walk on in step, further along the pavement",
        ],
    },
    "C": {
        "clip": "cmu_22_23_08",
        "motion": "hold hands, swing arms, walk",
        "azimuth_deg": -25.0,
        "elevation_deg": 4.0,
        "body_fraction": 0.74,
        "lens_mm": 45.0,
        "cast": [("man", "man_01", "a"), ("woman", "woman_01", "b")],
        "beats": [
            "they walk side by side, inner hands joined and hanging low",
            "the joined hands swing forward together",
            "the joined arms reach the top of the forward swing",
            "the joined hands swing back down past their hips",
            "the joined arms reach the back of the swing",
            "the hands swing forward again as they walk on",
        ],
    },
}

LANES = ["skeleton", "hidream", "flux2", "ideogram"]

SUBJECT = (
    "Two people walking on a city pavement beside a weathered brick wall, seen from the side. "
    "On the left a man in his thirties, dark cropped hair, a charcoal wool overcoat over a grey "
    "crew-neck, dark trousers, brown leather boots. On the right a woman in her thirties, "
    "shoulder-length auburn hair, a rust-red belted coat, dark jeans, tan ankle boots."
)


def scene_dir(set_id: str) -> Path:
    return REPO / "output" / "consistency" / f"set{set_id}"


def _plan_dict(set_id: str) -> dict:
    """A one-shot ShotPlan for this take, framed by the mocap planner's own camera solve."""
    import make_mocap_shot_plan as mmsp

    spec = SETS[set_id]
    clip = mmsp.load(spec["clip"])
    total = int(clip["frame_count"])
    picked = [round(i * (total - 1) / (FRAMES - 1)) for i in range(FRAMES)]
    camera = mmsp.solve_camera(clip, spec)
    characters = [
        {
            "id": cid,
            "asset": asset,
            "appearance": mmsp.APPEARANCE[asset],
            "transform": {"position": [0.0, 0.0, 0.0], "yaw_deg": 0.0, "scale": 1.0},
            "pose": {
                "kind": "segments",
                "name": spec["clip"],
                "actor": actor,
                "speed": 1.0,
                "offset_frames": 0,
                "loop": False,
            },
            "seg_id": i + 1,
            "reference_image_sha256": [],
        }
        for i, (cid, asset, actor) in enumerate(spec["cast"])
    ]
    shot = {
        "schema_version": 1,
        "shot_id": f"sht_consist{set_id.lower()}0001",
        "order": 0,
        "beat_id": f"bea_consist{set_id.lower()}0001",
        "seed": 4242,
        "frame_count": total,
        "fps": int(clip.get("fps", 24)),
        "width": WIDTH,
        "height": HEIGHT,
        "camera": camera,
        "characters": characters,
        "props": [],
        "environment": {
            "background_color": [0.07, 0.08, 0.11],
            "ground": {"enabled": True, "color": [0.3, 0.3, 0.33], "size": 60.0, "seg": False},
            "walls": None,
        },
        "lighting": {
            "preset": "exterior_dusk",
            "key_azimuth_deg": round(spec["azimuth_deg"] + 140.0, 1),
            "key_elevation_deg": 22.0,
            "intensity": 1.0,
            "shadows": True,
        },
        "render": {
            "engine": "workbench",
            "passes": ["pose_skeleton", "layout_boxes"],
            "depth_range": "auto",
            "canny": {"low": 100, "high": 200},
            "frames": picked,
        },
        "anchor_frames": [picked[0]],
        "motion_prompt": spec["motion"],
    }
    return {"shot": shot, "picked": picked}


def stage(set_ids: list[str]) -> None:
    """Blender once per take: pose skeletons and layout boxes at the six sampled frames."""
    from content_factory.config import get_settings
    from content_factory.controls.blender import run_blender_scene
    from content_factory.controls.bundle import build_control_bundle
    from content_factory.schemas.shots import ShotSpec
    from content_factory.sequences.control_compile import encode_png, render_openpose_frame

    cfg = get_settings().controls
    for set_id in set_ids:
        got = _plan_dict(set_id)
        shot = ShotSpec.model_validate(got["shot"])
        out = scene_dir(set_id)
        out.mkdir(parents=True, exist_ok=True)
        (out / "picked.json").write_text(json.dumps(got["picked"]))
        shot_dir = out / "controls"
        spec_path = shot_dir / "shot_spec.json"
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(shot.model_dump_json(indent=1))
        print(f"[{set_id}] blender {shot.shot_id} frames={got['picked']}", flush=True)
        run_blender_scene(
            spec_path,
            shot_dir,
            blender_bin=cfg.blender_bin,
            engine=cfg.engine,
            assets_root=Path(cfg.assets_root),
            timeout_s=cfg.timeout_s,
        )
        bundle = build_control_bundle(shot, shot_dir)
        (out / "bundle.json").write_text(bundle.model_dump_json(indent=1))
        skel_dir = out / "skeletons"
        skel_dir.mkdir(exist_ok=True)
        # SubjectTrack.layouts / .poses run the FULL length of the clip and are sparse: only the
        # rendered frames are filled (controls/bundle.py builds them over range(frame_count)).
        # Index by clip frame number, never by position in `picked`.
        boxes: dict[str, dict[str, dict | None]] = {}
        for i, frame in enumerate(got["picked"]):
            poses = [
                s.poses[frame]
                for s in bundle.subjects
                if frame < len(s.poses) and s.poses[frame] is not None
            ]
            img = render_openpose_frame(poses, WIDTH, HEIGHT)
            (skel_dir / f"{i:04d}.png").write_bytes(encode_png(img))
            joints = sum(len(p.joints) for p in poses)
            print(f"  frame {frame:>4} -> {i}: {len(poses)} people, {joints} joints", flush=True)
            boxes[str(i)] = {
                s.subject_id: (
                    s.layouts[frame].model_dump(mode="json")
                    if frame < len(s.layouts) and s.layouts[frame]
                    else None
                )
                for s in bundle.subjects
            }
        (out / "boxes.json").write_text(json.dumps(boxes, indent=1))


def _motion_plan(set_id: str):
    """A six-frame MotionPlan whose boxes are Blender's measured layout of the pair."""
    from content_factory.schemas.sequences import Box, MotionPlan, SubjectKeyframe, TrackedSubject

    boxes = json.loads((scene_dir(set_id) / "boxes.json").read_text())
    keyframes = []
    for i in range(FRAMES):
        per = [b for b in boxes[str(i)].values() if b]
        if per:
            x0 = min(b["x"] for b in per)
            y0 = min(b["y"] for b in per)
            x1 = max(b["x"] + b["w"] for b in per)
            y1 = max(b["y"] + b["h"] for b in per)
            box = Box(x=x0, y=y0, w=min(x1 - x0, 1.0 - x0), h=min(y1 - y0, 1.0 - y0))
        else:
            box = Box(x=0.2, y=0.25, w=0.6, h=0.65)
        keyframes.append(SubjectKeyframe(frame_index=i, layout=box))
    return MotionPlan(
        plan_id=f"mpl_consist{set_id.lower()}0001",
        sequence_id=f"seq_consist{set_id.lower()}0001",
        canvas_width=WIDTH,
        canvas_height=HEIGHT,
        frame_count=FRAMES,
        description=SETS[set_id]["motion"],
        subjects=(
            TrackedSubject(
                subject_id="subj_consist0001", label="the two people", keyframes=tuple(keyframes)
            ),
        ),
    )


def _anchor_prompt(set_id: str) -> str:
    from content_factory.sequences.styles import STYLE_PRESETS

    spec = SETS[set_id]
    return f"{STYLE_PRESETS['cinematic']}. {SUBJECT} {spec['beats'][0].capitalize()}."


BOX_SCALE = 1000
"""Ideogram's bounding boxes are 0-1000 on BOTH axes, not pixels.

Inferred, and worth saying so: the text encoder is Qwen3-VL, whose boxes are normalised to 0-1000,
and every worked example in the ComfyUI docs tops out near 1000 on both axes -- one of them is
`[200, 300, 950, 700]` beside a 1024x576 render, where 700 is past the bottom of the frame in
pixels and sensible as a fraction. Scaling to the pixel height instead squashes every box into the
top 58 % of the canvas."""


def _pair_box(boxes: dict, i: int) -> list[int]:
    """The union of the two figures' layout boxes at frame ``i``, as [x0, y0, x1, y1] of 1000."""
    per = [b for b in boxes[str(i)].values() if b]
    if not per:
        return [180, 60, 860, 940]
    x0 = min(b["x"] for b in per)
    y0 = min(b["y"] for b in per)
    x1 = max(b["x"] + b["w"] for b in per)
    y1 = max(b["y"] + b["h"] for b in per)
    return [round(v * BOX_SCALE) for v in (x0, y0, x1, y1)]


def _ideogram_caption(set_id: str, beat: str, beat_index: int) -> str:
    """Structured, never prose -- see docs/research/2026-09-12-ideogram-4.md.

    The element box is Blender's own measured layout of the pair at that frame, which is the
    first time this repo's `layout_boxes` reach a model as coordinates rather than as a
    rastered rectangle.
    """
    from content_factory.sequences.styles import STYLE_PRESETS

    boxes = json.loads((scene_dir(set_id) / "boxes.json").read_text())
    return json.dumps(
        {
            "high_level_description": f"{SUBJECT} {beat.capitalize()}.",
            "style_description": {"medium": "photograph", "photo": STYLE_PRESETS["cinematic"]},
            "compositional_deconstruction": {
                "background": "a city pavement beside a weathered brick wall, softly out of focus",
                "elements": [
                    {
                        "description": f"two adults in winter coats, {beat}",
                        "bounding_box": _pair_box(boxes, beat_index),
                    }
                ],
            },
        },
        indent=1,
    )


def _backend(lane: str, workdir: Path):
    from content_factory.config import get_settings
    from content_factory.services.local import LocalServices

    services = LocalServices(get_settings().local_services, state_dir=Path(".services"))
    if lane in {"skeleton", "hidream"}:
        from content_factory.sequences.hidream_backend import HiDreamReferenceEditBackend

        return HiDreamReferenceEditBackend(endpoint=services.ensure("hidream"))
    from content_factory.sequences.flux2_backend import Flux2ReferenceBackend

    return Flux2ReferenceBackend(
        workdir=workdir / "_work", endpoint=services.ensure("comfyui"), turbo=True
    )


def _repair_truncated(
    out: Path, plan, lock, backend, *, frame_instructions, conditioning_for, attempts: int = 3
) -> list[int]:
    """Re-draw any frame the model left half-blank, with a fresh seed. Returns what was repaired.

    `drift_report` is deliberately not the place for this. A truncated render is unconditionally
    broken rather than a threshold judgement, so it is tempting to make it a drift failure -- but
    `MockReferenceEditBackend` draws flat rectangles by design, so every mock frame in the test
    suite would fail and regenerate three times. The repo already settled this shape for Ideogram's
    refusals: advisory in `qc.frame_review` where a reviewer sees it, and a caller that knows which
    model it is talking to spends another seed. This is that caller.
    """
    from content_factory.qc.frame_review import BLANK_PANEL_MAX, blank_panel_fraction
    from content_factory.sequences.engine import build_sequence

    repaired: list[int] = []
    for attempt in range(1, attempts + 1):
        broken = {
            i: blank_panel_fraction(f.read_bytes())
            for i in range(FRAMES)
            if (f := out / "frames" / f"{i:04d}.png").exists()
            and blank_panel_fraction(f.read_bytes()) > BLANK_PANEL_MAX
        }
        if not broken:
            break
        for idx, fraction in broken.items():
            print(
                f"  frame {idx}: {fraction:.0%} of the canvas never drew, re-seeding"
                f" (attempt {attempt})",
                flush=True,
            )
            (out / "frames" / f"{idx:04d}.png").unlink()
            (out / "frames" / f"{idx:04d}.done.json").unlink(missing_ok=True)
            if idx not in repaired:
                repaired.append(idx)
        build_sequence(
            plan,
            lock,
            backend,
            out,
            frame_instructions=frame_instructions,
            conditioning_for=conditioning_for,
            max_regen_attempts=1,
            locked_region_similarity_min=0.0,
            style_delta_max=10.0,
            seed_offsets={i: attempt * 37 for i in broken},
        )
    return repaired


def _skeleton_conditioning(
    skeletons: list[bytes],
) -> Callable[[int], ControlConditioning]:
    """Bound in its own scope: a closure over the loop variable would serve set C's skeletons."""
    from content_factory.sequences.engine import ControlConditioning

    def conditioning(idx: int) -> ControlConditioning:
        return ControlConditioning(reference_pngs=(skeletons[idx],))

    return conditioning


def render(lane: str, set_ids: list[str]) -> None:
    import time

    from content_factory.schemas.sequences import GenerationLock
    from content_factory.sequences.engine import build_sequence

    for set_id in set_ids:
        out = scene_dir(set_id) / lane
        out.mkdir(parents=True, exist_ok=True)
        spec = SETS[set_id]
        plan = _motion_plan(set_id)
        lock = GenerationLock(
            workflow_package_id="cns_probe00001",
            workflow_package_version="1.0.0",
            model_revision=lane,
            width=WIDTH,
            height=HEIGHT,
            seed=4242,
            # With ONE reference the HiDream backend forwards this as the server's scheduler, and
            # the server only falls back to its own correct choice when the field is falsy --
            # `"default"` is truthy, so it would override upstream's editing branch with something
            # else. `flow_match` IS what upstream picks for a single-reference edit, so naming it
            # here is the same decision made explicitly. With two references (the skeleton lane)
            # the backend sends null and the server picks `flash`, which is right for that case.
            sampler="flow_match" if lane in {"skeleton", "hidream"} else "euler",
            steps=8 if lane.startswith("flux") else 28,
            guidance=1.0 if lane.startswith("flux") else 0.0,
            style_prompt="cinematic",
            camera_prompt="side view, eye level",
            lighting_prompt="overcast daylight",
            background_prompt="a city pavement beside a weathered brick wall",
            reference_asset_sha256="0" * 64,
        )
        backend = _backend(lane, out)
        started = time.monotonic()

        # Ideogram has no edit path in this repo, by design: every frame is an independent
        # text-to-image with the same seed. That IS the measurement -- whether the model the
        # operator picked as the main one can hold a world without being shown one.
        if lane == "ideogram":
            _render_ideogram(set_id, out, spec)
            print(f"[{lane}/{set_id}] {time.monotonic() - started:.0f}s", flush=True)
            continue

        anchor_path = out / "anchor.png"
        if not anchor_path.exists():
            png = backend.text_to_image(_anchor_prompt(set_id), lock)
            anchor_path.write_bytes(png)
            print(f"[{lane}/{set_id}] anchor {time.monotonic() - started:.0f}s", flush=True)

        conditioning_for: Callable[[int], ControlConditioning] | None = None
        if lane == "skeleton":
            skels = [
                (scene_dir(set_id) / "skeletons" / f"{i:04d}.png").read_bytes()
                for i in range(FRAMES)
            ]
            # One OpenPose raster per frame, behind the anchor in the reference order. The
            # backend composes anchor-then-skeleton, so identity leads and pose follows.
            conditioning_for = _skeleton_conditioning(skels)

        # The drift gate is RECORDED, not enforced, and that is a deliberate choice for this
        # probe. `locked_region_similarity_min=0.92` is calibrated for a locked camera where only
        # the subject moves; every set here is two people walking across the frame, so the
        # background legitimately changes and the gate rejected every set A skeleton frame it
        # reached (four of six, at 0.56-0.65 similarity and 0.23-0.26 style delta) -- frames whose
        # identity and world a reviewer reads as consistent. Three
        # regeneration attempts at 2m13s each buys nothing when the threshold is the wrong
        # question. The measured numbers still land in result.json, which is the useful half.
        result = build_sequence(
            plan,
            lock,
            backend,
            out,
            frame_instructions={
                i: f"{spec['beats'][i].capitalize()}; frame {i} ." for i in range(FRAMES)
            },
            conditioning_for=conditioning_for,
            max_regen_attempts=1,
            locked_region_similarity_min=0.0,
            style_delta_max=10.0,
        )
        instructions = {i: f"{spec['beats'][i].capitalize()}; frame {i} ." for i in range(FRAMES)}
        repaired = _repair_truncated(
            out,
            plan,
            lock,
            backend,
            frame_instructions=instructions,
            conditioning_for=conditioning_for,
        )
        elapsed = time.monotonic() - started
        (out / "result.json").write_text(
            json.dumps(
                {
                    "lane": lane,
                    "set": set_id,
                    "seconds": round(elapsed, 1),
                    "regenerated": list(result.regenerated),
                    "failed": list(result.failed),
                    "frames": len(result.frames),
                    "repaired_truncated": repaired,
                    "drift": [
                        {
                            "frame": f.get("frame_index"),
                            "locked": (f.get("drift") or {}).get("locked"),
                            "style": (f.get("drift") or {}).get("style"),
                        }
                        for f in result.frames
                    ],
                },
                indent=1,
            )
        )
        print(
            f"[{lane}/{set_id}] {elapsed:.0f}s  frames={len(result.frames)}"
            f"  regenerated={list(result.regenerated)}  failed={list(result.failed)}",
            flush=True,
        )


def _render_ideogram(set_id: str, out: Path, spec: dict) -> None:
    """Ideogram 4 through ComfyUI, one independent frame per beat, seed held fixed.

    No anchor and no drift gate, because the backend cannot take one. Whatever consistency shows
    up here is the model's alone -- which is the measurement.
    """
    import time

    from content_factory.config import get_settings
    from content_factory.qc.frame_review import is_refusal_frame
    from content_factory.schemas.sequences import GenerationLock
    from content_factory.sequences.ideogram_backend import Ideogram4Backend
    from content_factory.services.local import LocalServices

    services = LocalServices(get_settings().local_services, state_dir=Path(".services"))
    backend = Ideogram4Backend(workdir=out / "_work", endpoint=services.ensure("comfyui"))
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    for i, beat in enumerate(spec["beats"]):
        dest = frames_dir / f"{i:04d}.png"
        if dest.exists() and not is_refusal_frame(dest.read_bytes()):
            continue
        caption = _ideogram_caption(set_id, beat, i)
        for seed in (4242, 4243, 99001, 31337, 7):
            lock = GenerationLock(
                workflow_package_id="cns_probe00001",
                workflow_package_version="1.0.0",
                model_revision="ideogram4",
                width=WIDTH,
                height=HEIGHT,
                seed=seed,
                sampler="euler",
                steps=20,
                guidance=7.0,
                style_prompt="cinematic",
                camera_prompt="side view, eye level",
                lighting_prompt="overcast daylight",
                background_prompt="a city pavement beside a weathered brick wall",
                reference_asset_sha256="0" * 64,
            )
            began = time.monotonic()
            dest.write_bytes(backend.text_to_image(caption, lock))
            blocked = is_refusal_frame(dest.read_bytes())
            print(
                f"  frame {i} seed {seed:>6} {time.monotonic() - began:5.0f}s"
                f" {'REFUSED' if blocked else 'ok'}",
                flush=True,
            )
            if not blocked:
                break


def _sheet(rows: list[tuple[str, list[Path]]], dest: Path, *, cell: int = 300) -> None:
    """One row per lane, one column per frame, labelled. The thing a reviewer actually reads."""
    from PIL import Image, ImageDraw

    if not rows:
        return
    first = Image.open(rows[0][1][0])
    ch = round(cell * first.size[1] / first.size[0])
    cols = max(len(paths) for _, paths in rows)
    pad, head = 4, 15
    sheet = Image.new(
        "RGB", (cols * (cell + pad) + pad, len(rows) * (ch + head + pad) + pad), (250, 249, 246)
    )
    draw = ImageDraw.Draw(sheet)
    for r, (label, paths) in enumerate(rows):
        y = pad + r * (ch + head + pad)
        draw.text((pad, y + 2), label, fill=(15, 15, 15))
        for c, path in enumerate(paths):
            img = Image.open(path).convert("RGB").resize((cell, ch))
            sheet.paste(img, (pad + c * (cell + pad), y + head))
    dest.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(dest)


def sheets(set_ids: list[str], lanes: list[str]) -> None:
    for set_id in set_ids:
        rows: list[tuple[str, list[Path]]] = []
        for lane in lanes:
            frames_dir = scene_dir(set_id) / lane / "frames"
            paths = sorted(frames_dir.glob("*.png")) if frames_dir.is_dir() else []
            if paths:
                rows.append((f"{lane}  ({SETS[set_id]['motion']})", paths))
        dest = REPO / "videos" / f"consistency-set{set_id}.png"
        _sheet(rows, dest)
        if rows:
            print(f"set {set_id}: {len(rows)} lanes -> {dest}")


def clips(set_ids: list[str], lanes: list[str], *, fps: int = 2) -> None:
    """Each lane's six frames as a slow flipbook, through the engine's own packager.

    Two frames a second, not eight: six pictures of a motion are a flipbook, not footage, and at
    8 fps the whole take is over in under a second -- too fast to see whether the man's coat
    changed. The packager also writes a contact sheet and a printable flipbook PDF beside it.
    """
    import shutil

    from content_factory.sequences.engine import package_sequence

    dest_dir = REPO / "videos"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for set_id in set_ids:
        for lane in lanes:
            out = scene_dir(set_id) / lane
            if not (out / "frames").is_dir() or not any((out / "frames").glob("*.png")):
                continue
            package_sequence(_motion_plan(set_id), out, fps=fps)
            dest = dest_dir / f"consistency-set{set_id}-{lane}.mp4"
            shutil.copy2(out / "preview.mp4", dest)
            print(f"set {set_id} / {lane} -> {dest}")


def score(set_ids: list[str], lanes: list[str]) -> None:
    from content_factory.qc.frame_review import consistency_matrix, dead_flat_fraction

    rows = []
    for set_id in set_ids:
        for lane in lanes:
            frames_dir = scene_dir(set_id) / lane / "frames"
            pngs = sorted(frames_dir.glob("*.png")) if frames_dir.is_dir() else []
            if len(pngs) < 2:
                continue
            loaded = [(p.stem, p.read_bytes()) for p in pngs]
            matrix = consistency_matrix(loaded)
            flats = [dead_flat_fraction(b)[0] for _, b in loaded]
            rows.append(
                {
                    "set": set_id,
                    "lane": lane,
                    "frames": len(loaded),
                    "median_distance": matrix["median_distance"],
                    "worst": matrix["worst_pair"],
                    "spread": matrix["spread"],
                    "outliers": matrix["outliers"],
                    "dead_flat_mean": round(statistics.mean(flats), 4),
                }
            )
    out = REPO / "output" / "consistency" / "scores.json"
    out.write_text(json.dumps(rows, indent=1))
    print(f"{'set':>4} {'lane':>10} {'frames':>7} {'median':>8} {'worst':>8} {'flat%':>7}  note")
    for r in rows:
        worst = r["worst"]["distance"] if r["worst"] else 0.0
        note = (
            "SPREAD: no consistent core"
            if r["spread"]
            else (f"outliers {r['outliers']}" if r["outliers"] else "one world")
        )
        print(
            f"{r['set']:>4} {r['lane']:>10} {r['frames']:>7} {r['median_distance']:>8.4f}"
            f" {worst:>8.4f} {r['dead_flat_mean'] * 100:>6.1f}%  {note}"
        )
    print(f"\nwritten: {out}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["stage", "render", "score", "sheets", "clips"])
    ap.add_argument("--sets", default="ABC")
    ap.add_argument("--lane", default="")
    args = ap.parse_args()
    ids = [c for c in args.sets.upper() if c in SETS]
    lanes = [x for x in (args.lane.split(",") if args.lane else LANES) if x]
    if args.command == "stage":
        stage(ids)
    elif args.command == "render":
        for lane in lanes:
            render(lane, ids)
    elif args.command == "sheets":
        sheets(ids, lanes)
    elif args.command == "clips":
        clips(ids, lanes)
    else:
        score(ids, lanes)
        sheets(ids, lanes)
        clips(ids, lanes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
