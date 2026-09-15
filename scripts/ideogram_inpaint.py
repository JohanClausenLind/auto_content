#!/usr/bin/env python
"""Repaint a region of a finished frame with Ideogram 4, via Differential Diffusion."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "python"))

from PIL import Image  # noqa: E402

from content_factory.schemas.sequences import Box  # noqa: E402
from content_factory.sequences.diffdiff_map import (  # noqa: E402
    composite,
    describe,
    from_mask_png,
    soft_map,
)
from content_factory.sequences.ideogram_sdnq_backend import (  # noqa: E402
    DEFAULT_ENDPOINT,
    Ideogram4SdnqBackend,
)


def _box(text: str) -> Box:
    parts = text.split(",")
    if len(parts) != 4:
        msg = f"--box wants x,y,w,h as relative floats, got {text!r}"
        raise argparse.ArgumentTypeError(msg)
    try:
        x, y, w, h = (float(p) for p in parts)
    except ValueError as exc:
        msg = f"--box values must be numbers: {text!r}"
        raise argparse.ArgumentTypeError(msg) from exc
    return Box(x=x, y=y, w=w, h=h)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--image", type=Path, required=True, help="the frame to repaint")
    ap.add_argument("--out", type=Path, required=True, help="where the repainted frame goes")
    ap.add_argument("--caption", type=Path, help="JSON caption file of the FINISHED frame")
    ap.add_argument("--caption-text", help="the caption inline, instead of --caption")
    ap.add_argument("--box", type=_box, action="append", default=[], help="x,y,w,h to repaint")
    ap.add_argument(
        "--mask", type=Path, help="a conventional mask PNG (white = repaint) instead of boxes"
    )
    ap.add_argument("--feather-tokens", type=float, default=2.0, help="blur radius in 16 px cells")
    ap.add_argument("--hold", type=float, default=0.0, help="0.3 keeps the original's layout/light")
    ap.add_argument(
        "--outside", type=float, default=1.0, help="below 1.0 lets the rest take a light pass"
    )
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--preset", default="default", choices=("quality", "default", "turbo"))
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--save-map", type=Path, help="also write the change map, for inspection")
    ap.add_argument(
        "--dry-run", action="store_true", help="build and report the map, generate nothing"
    )
    ap.add_argument(
        "--no-composite",
        action="store_true",
        help="skip pasting the original back outside the map (leaves VAE round-trip drift)",
    )
    args = ap.parse_args()

    if bool(args.box) == bool(args.mask):
        ap.error("give either --box (repeatable) or --mask, not both and not neither")
    if not args.dry_run and not (args.caption or args.caption_text):
        ap.error("--caption or --caption-text is required unless --dry-run")

    frame = Image.open(args.image).convert("RGB")
    width, height = frame.size
    if width % 16 or height % 16:
        ap.error(f"{args.image} is {width}x{height}; both sides must be multiples of 16")

    if args.mask:
        change_map = from_mask_png(
            args.mask.read_bytes(),
            feather_tokens=args.feather_tokens,
            hold=args.hold,
            outside=args.outside,
        )
    else:
        change_map = soft_map(
            width=width,
            height=height,
            regenerate=args.box,
            feather_tokens=args.feather_tokens,
            hold=args.hold,
            outside=args.outside,
        )

    facts = describe(change_map)
    print(json.dumps({"size": [width, height], **facts}, indent=1))
    if facts["regenerated"] > 0.6:
        print(
            f"warning: this map rebuilds {facts['regenerated']:.0%} of the frame -- that is a"
            " text-to-image run wearing an inpaint's clothes, not a repair",
            file=sys.stderr,
        )
    if args.save_map:
        args.save_map.write_bytes(change_map)
        print(f"map -> {args.save_map}")
    if args.dry_run:
        return 0

    caption = args.caption_text or args.caption.read_text()
    backend = Ideogram4SdnqBackend(endpoint=args.endpoint, preset=args.preset)
    if not backend.ready():
        print(
            f"no Ideogram 4 SDNQ server at {args.endpoint}. Start it with:\n"
            "  uv run --project skills/image/ideogram4 python skills/image/ideogram4/server.py",
            file=sys.stderr,
        )
        return 2

    png = backend.inpaint(
        args.image.read_bytes(),
        change_map,
        caption,
        width=width,
        height=height,
        seed=args.seed,
    )
    if not args.no_composite:
        png = composite(args.image.read_bytes(), png, change_map)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(png)
    print(json.dumps({"out": str(args.out), **backend.last_facts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
