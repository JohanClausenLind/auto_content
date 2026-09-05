#!/usr/bin/env python3
"""Build one character's styled identity sheet, in one style.

An asset build step, not a lane node. The mesh and its turnaround come out of Blender
(``build_character.py`` then ``render_turnaround.py``); this draws the styled, clothed reference
that the ``identity`` slot of an anchor prompt actually sends, from the asset's own turnaround
views so it is the same body. Run it once per character per style — when the asset is built, or
when the film's style changes — never once per run.

Why it exists at all: HiDream-O1's IP pipeline treats every reference as subject material, so the
clay render makes it draw clay people and the untextured MPFB turnaround makes it draw a nude
mannequin. Both were measured (STATUS 1339, 1379-1381), which is why
``ImageSequenceSettings.anchor_references`` says to add ``identity`` only once a *styled* sheet
exists. This is how one gets made.

Usage, from the repo root:

    uv run python skills/video/blender_scene/assets_build/build_identity_sheet.py \\
        --asset man_01 --style watercolour [--seed 0] [--backend hidream] \\
        [--appearance "a man in his forties, short dark hair, plain grey coat"] [--all-styles ...]

The image model has to be reachable: the HiDream skill server on 127.0.0.1:8801, or ComfyUI for
``--backend flux2``. Both are started for you when ``local_services.auto_start`` is on. ``--dry-run``
prints what would be built and touches nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "python"))


def main(argv: list[str]) -> int:
    from content_factory.config import get_settings
    from content_factory.controls.identity_sheets import (
        DEFAULT_VIEWS,
        SheetError,
        build_sheet,
        sheet_paths,
        sheet_prompt,
        style_slug,
    )
    from content_factory.sequences.styles import resolve_style

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", required=True, help="character asset name, e.g. man_01")
    parser.add_argument(
        "--style",
        action="append",
        default=[],
        required=True,
        help="a style preset name or a full style prompt; repeat for several",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--backend", default="", help="mock | hidream | flux2 (default: settings)")
    parser.add_argument(
        "--appearance",
        default="",
        help="how this character looks, in the words an image model reads. Also belongs on"
        " CharacterSpec.appearance, so the anchor prompt says the same thing as the sheet.",
    )
    parser.add_argument("--views", default=",".join(DEFAULT_VIEWS))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    asset_dir = Path(settings.controls.assets_root) / "characters" / args.asset
    if not asset_dir.is_dir():
        print(f"no asset at {asset_dir}", file=sys.stderr)
        return 2
    views = tuple(v.strip() for v in args.views.split(",") if v.strip())
    styles = [resolve_style(s) for s in args.style]

    if args.dry_run:
        for style in styles:
            png, marker = sheet_paths(settings.controls.assets_root, args.asset, style)
            print(
                json.dumps(
                    {
                        "asset": args.asset,
                        "style_slug": style_slug(style),
                        "png": str(png),
                        "exists": png.exists() and marker.exists(),
                        "views": list(views),
                        "prompt": sheet_prompt(style=style, appearance=args.appearance or None),
                    }
                )
            )
        return 0

    # The same selector the stages use, so a sheet is drawn by the model the film will be.
    from content_factory.workflows.stages import _reference_backend

    if args.backend:
        import os

        os.environ["CF__IMAGE_SEQUENCES__BACKEND"] = args.backend
        get_settings.cache_clear()  # type: ignore[attr-defined]
    backend = _reference_backend()
    from content_factory.services.local import ensure_service

    if backend.name == "hidream-o1":
        ensure_service("hidream")
    elif backend.name.startswith("flux2"):
        ensure_service("comfyui")

    built = []
    for style in styles:
        try:
            result = build_sheet(
                asset_dir,
                style=style,
                backend=backend,  # type: ignore[arg-type]
                seed=args.seed,
                appearance=args.appearance or None,
                views=views,
            )
        except SheetError as exc:
            print(str(exc), file=sys.stderr)
            return 3
        built.append(
            {
                "style_slug": result.sheet.style_slug,
                "png": str(result.path),
                "png_sha256": result.sheet.png_sha256[:12],
                "cache_hit": result.cache_hit,
            }
        )
    print(json.dumps({"asset": args.asset, "backend": backend.name, "sheets": built}))
    print(
        "Now look at the sheets and approve the asset:"
        f" `content-factory assets approve {args.asset} --as <name>`."
        " An approval covers the mesh AND its sheets, so a new sheet needs a new look.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
