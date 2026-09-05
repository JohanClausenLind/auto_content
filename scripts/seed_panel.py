#!/usr/bin/env python
"""Generate one shot at several seeds, score every clip the same way, and build the panel.

Why a script and not a stage: choosing a seed is an *audition*, not a step in making a film. The
pipeline's job is to produce the same film from the same inputs, and a seed is one of those inputs
— so the place to try five of them is beside the pipeline, with a sheet a person looks at and a
table of numbers that says which ones are worth looking at first.

What it scores, all deterministic and all already in the repo:

* ``qc/frame_review.py`` — tonal collapse, half-applied colour, edge intrusion, background churn.
  These are findings about a *frame*, and they are the same checks the review stages apply.
* ``sequences/drift.py:guide_adherence`` — did the clip actually arrive at the anchors it was
  given. Calibrated on the first live guided run (see ``GUIDE_SIMILARITY_MIN``).

No model grades another model. The ranking is arithmetic over those two, and the contact sheet is
there because the thing the numbers cannot judge — whether the motion reads as a camera move or as
a cross-dissolve between two anchors — is exactly what a person sees at a glance.

    uv run python scripts/seed_panel.py --anchor <start.png> --seeds 7,11,13 \
        --out output/seed-panels/wind-hook [--end-anchor <end.png>] [--prompt "..."]

Needs a running ComfyUI with the LTX-2.5 weights (``comfy launch --background -- --cache-none``).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from PIL import Image

from content_factory.media.ltx_packages import (
    ltx_i2v_guided_package,
    ltx_i2v_package,
    snap_length,
)
from content_factory.media.video_generate import (
    ComfyUIVideoBackend,
    GuideFrame,
    VideoGenerationRequest,
    run_video_skill,
)
from content_factory.qc.frame_review import contact_sheet, review_findings
from content_factory.schemas.base import sha256_hex
from content_factory.sequences.drift import guide_adherence

# The frames pulled out of every clip for scoring: the two ends, because those are what a guide
# pins, and the middle, because that is where a two-anchor clip cross-dissolves if it is going to.
SAMPLE_FRACTIONS = (0.0, 0.5, 1.0)


def _prepare(src: Path, dest: Path, size: tuple[int, int]) -> bytes:
    Image.open(src).convert("RGB").resize(size, Image.Resampling.LANCZOS).save(dest)
    return dest.read_bytes()


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--anchor", required=True, type=Path, help="first frame")
    ap.add_argument("--end-anchor", type=Path, help="optional anchor pinned at the last frame")
    ap.add_argument("--seeds", default="7,11,13,17", help="comma-separated")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--prompt", default="Continuous camera motion, no cuts.")
    ap.add_argument("--width", type=int, default=704)
    ap.add_argument("--height", type=int, default=384)
    ap.add_argument("--frames", type=int, default=49, help="snapped to 8k+1")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--endpoint", default="http://127.0.0.1:8188")
    args = ap.parse_args(argv)

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    size = (args.width, args.height)
    length = snap_length(args.frames)
    last = length - 1
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    start = _prepare(args.anchor, out / "start.png", size)
    end = _prepare(args.end_anchor, out / "end.png", size) if args.end_anchor else None
    guides = (
        (GuideFrame(frame_index=last, png_sha256=sha256_hex(end), strength=1.0),) if end else ()
    )

    backend = ComfyUIVideoBackend(
        args.endpoint,
        ltx_i2v_package(width=args.width, height=args.height, length=length, fps=float(args.fps)),
        guided_packages={
            n: ltx_i2v_guided_package(
                n, width=args.width, height=args.height, length=length, fps=float(args.fps)
            )
            for n in (1, 2)
        },
        collect="history",
        timeout_s=2400.0,
    )

    # Imported here so the module's import cost stays with the stage that owns it.
    from content_factory.workflows.stages import _extract_frames

    indices = sorted({round(f * last) for f in SAMPLE_FRACTIONS})
    panel: list[tuple[str, bytes]] = []
    rows: list[dict] = []
    for seed in seeds:
        clip = out / f"seed-{seed}.mp4"
        began = time.monotonic()
        if not clip.exists():
            request = VideoGenerationRequest(
                request_id=f"vid_seed{seed:08d}",
                purpose=f"seed panel, seed {seed}",
                prompt=args.prompt,
                width=args.width,
                height=args.height,
                duration_s=round(length / args.fps, 3),
                fps=args.fps,
                seed=seed,
                with_audio=False,
                guides=guides,
            )
            result = run_video_skill(
                request,
                backend,
                workdir=out,
                first_frame_png=start,
                guide_pngs={last: end} if end else None,
            )
            clip.write_bytes(result.mp4)
        seconds = round(time.monotonic() - began, 1)

        frames = _extract_frames(clip, indices)
        pngs = [(f"seed {seed} f{i}", frames[i]) for i in indices if i in frames]
        findings = review_findings(pngs)
        flagged = {
            frame_id: [f.check for f in items if not f.passed]
            for frame_id, items in findings.items()
            if any(not f.passed for f in items)
        }
        adherence = guide_adherence(frames, {last: end} if end else {})
        # The middle frame is what a person has to look at, so it is the one on the sheet.
        middle = indices[len(indices) // 2]
        if middle in frames:
            panel.append((f"seed {seed}", frames[middle]))
        rows.append(
            {
                "seed": seed,
                "seconds": seconds,
                "flagged": flagged,
                "findings": sum(len(v) for v in flagged.values()),
                "guide": adherence.as_facts(),
            }
        )

    # Fewest findings first, then best-adhering guide. Both are "look here first" orderings, not
    # verdicts: a clip with no findings can still be the wrong take.
    rows.sort(key=lambda r: (r["findings"], -float(r["guide"]["worst"])))
    sheet = out / "panel.png"
    if panel:
        contact_sheet(panel, sheet, columns=min(3, len(panel)))
    (out / "panel.json").write_text(json.dumps({"seeds": rows}, indent=1, sort_keys=True))
    print(json.dumps({"seeds": rows, "sheet": str(sheet) if panel else None}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
