"""Generate the holding-hands sequence: two people, hands slowly meeting, clipped into a video.

Anchor (text-to-image) + hub-and-spoke reference edits via the local HiDream-O1 server, then the
keyframes are blended into a slow 30 fps clip with ffmpeg.

    1. terminal A:  CF_HIDREAM_MODEL_PATH=~/models/HiDream-O1-Image-Dev \
                    uv run --project skills/image/hidream python skills/image/hidream/server.py
    2. terminal B:  uv run python scripts/generate_holding_hands.py --workdir out/holding-hands

Requires only loopback HTTP; nothing leaves the machine.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from content_factory.schemas.sequences import (
    Box,
    GenerationLock,
    MotionPlan,
    SubjectKeyframe,
    TrackedSubject,
)
from content_factory.sequences.engine import build_sequence
from content_factory.sequences.hidream_backend import HiDreamReferenceEditBackend

ANCHOR_PROMPT = (
    "medium shot, eye-level, side view. Two people stand close together on a quiet forest path "
    "at golden hour, facing forward, photographed from the side in warm natural light. On the "
    "left, a woman in her late twenties with shoulder-length dark hair, wearing a rust-colored "
    "knit sweater and jeans, her arms relaxed at her sides. On the right, a man of the same age "
    "with short brown hair, wearing a gray henley and dark trousers, his arms also relaxed at "
    "his sides. A small visible gap separates their nearest hands. Soft bokeh of green and gold "
    "leaves behind them, gentle rim light on their shoulders, photorealistic, calm and tender "
    "mood."
)

# One declared change per frame: only the inner hands move, everything else is preserved.
HAND_PROGRESS = [
    "Their inner hands hang relaxed at their sides with a clear gap between them; frame 0 .",
    "Their inner arms angle very slightly toward each other, hands still apart; frame 1 .",
    "Their inner hands drift closer, the gap between them halved; frame 2 .",
    "The backs of their inner hands are almost touching, fingers relaxed; frame 3 .",
    "Their fingertips make first gentle contact; frame 4 .",
    "Their fingers begin to curl together, palms starting to meet; frame 5 .",
    "Their palms press together, fingers loosely wrapped around each other's hand; frame 6 .",
    "They hold hands firmly, fingers fully interlaced between them; frame 7 .",
]


def _lerp_boxes(count: int) -> list[SubjectKeyframe]:
    # The inner-hands region drifts from a wider gap to a single meeting point at the center.
    frames = []
    for i in range(count):
        t = i / (count - 1)
        width = 0.30 - 0.18 * t  # region narrows as the hands close
        frames.append(
            SubjectKeyframe(
                frame_index=i,
                layout=Box(x=0.5 - width / 2, y=0.52, w=width, h=0.22),
            )
        )
    return frames


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workdir", type=Path, default=Path("out/holding-hands"))
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--size", type=int, default=1024)
    ap.add_argument("--seed", type=int, default=32)
    ap.add_argument("--steps", type=int, default=28)
    ap.add_argument("--endpoint", default="http://127.0.0.1:8801")
    ap.add_argument("--duration", type=float, default=10.0, help="Final clip length in seconds.")
    args = ap.parse_args()

    backend = HiDreamReferenceEditBackend(endpoint=args.endpoint)
    if not backend.ready():
        print(
            f"hidream server not ready at {args.endpoint} — see the module docstring",
            file=sys.stderr,
        )
        return 1

    plan = MotionPlan(
        plan_id="plan_holdinghands01",
        sequence_id="seq_holdinghands01",
        frame_count=args.frames,
        canvas_width=args.size,
        canvas_height=args.size,
        subjects=(
            TrackedSubject(
                subject_id="subj_innerhands1",
                label="the couple's inner hands",
                keyframes=tuple(_lerp_boxes(args.frames)),
            ),
        ),
        description="Two people slowly reach for and hold each other's hand.",
    )
    lock = GenerationLock(
        workflow_package_id="hidream-o1-image",
        workflow_package_version="1.0.0",
        model_revision="HiDream-O1-Image-Dev",
        width=args.size,
        height=args.size,
        seed=args.seed,
        sampler="flow_match",
        steps=args.steps,
        guidance=5.0,
        style_prompt="photorealistic, warm golden-hour light, tender documentary feel",
        camera_prompt="medium shot, eye-level, fixed camera",
        lighting_prompt="golden hour, soft rim light",
        background_prompt="quiet forest path with green-gold bokeh",
        reference_asset_sha256="0" * 64,  # bound to the real anchor inside build_sequence
    )

    args.workdir.mkdir(parents=True, exist_ok=True)
    anchor_path = args.workdir / "anchor.png"
    if not anchor_path.exists():
        print("generating anchor (text-to-image)…", flush=True)
        anchor_path.write_bytes(backend.text_to_image(ANCHOR_PROMPT, lock))
        print(f"anchor -> {anchor_path}")

    instructions = {i: HAND_PROGRESS[min(i, len(HAND_PROGRESS) - 1)] for i in range(args.frames)}
    print(f"building {args.frames} keyframes (hub-and-spoke edits of the anchor)…", flush=True)
    result = build_sequence(
        plan,
        lock,
        backend,
        args.workdir,
        frame_instructions=instructions,
        # First real-model run: drift QC observes but barely gates; calibrate before tightening.
        max_regen_attempts=1,
        locked_region_similarity_min=0.30,
        style_delta_max=0.60,
    )
    print(
        json.dumps(
            {
                "frames": len(result.frames),
                "regenerated": result.regenerated,
                "failed": result.failed,
            }
        )
    )
    if result.failed:
        print("some frames failed drift QC — inspect the workdir", file=sys.stderr)
        return 2

    # Blend the keyframes into one slow, smooth clip.
    in_fps = args.frames / args.duration
    out_path = args.workdir / "holding-hands.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-framerate",
            f"{in_fps:.4f}",
            "-i",
            str(args.workdir / "frames" / "%04d.png"),
            "-vf",
            "minterpolate=fps=30:mi_mode=blend,format=yuv420p",
            "-movflags",
            "+faststart",
            str(out_path),
        ],
        check=True,
    )
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(out_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    print(f"video -> {out_path} ({float(probe):.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
