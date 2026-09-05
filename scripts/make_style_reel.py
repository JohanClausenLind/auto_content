"""Render the same short film once per art direction, cut as held drawings, for comparison.

    uv run python scripts/make_style_reel.py out/style-reel woodblock charcoal riso
    uv run python scripts/make_style_reel.py out/style-reel            # every preset

One Blender pass is shared by every style — the control passes do not depend on the art direction,
only the drawings do — so the cost per style is just its drawings. Each style gets its own project
directory so their anchor caches cannot collide, and the finished cut is copied out as
``<style>.mp4`` next to the others.

Styles are rendered one at a time and each is complete before the next starts, so an interrupted
run leaves finished videos rather than a directory of half-films.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SUBJECT = (
    "Two people cross a rain-wet stone plaza at dusk in a northern old town, and their hands meet."
)


def _run(args: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    import os

    # Fixed argv: the only caller-supplied values are a style name already checked against
    # STYLE_PRESETS and two fixture paths, and nothing goes through a shell.
    return subprocess.run(  # noqa: S603
        args, cwd=REPO_ROOT, capture_output=True, text=True, env={**os.environ, **(env or {})}
    )


def render_style(style: str, out_dir: Path, story: str, shots: str) -> dict:
    project = out_dir / style
    started = time.time()
    proc = _run(
        [
            "uv",
            "run",
            "content-factory",
            "run-local",
            "picture-story",
            "--set",
            "motion.motion=hold",
            "--project-dir",
            str(project),
            "--story",
            story,
            "--shots",
            shots,
            "--subject",
            SUBJECT,
            "--style",
            style,
            "--until",
            "interpolate",
        ],
        env={"CF__IMAGE_SEQUENCES__BACKEND": "hidream"},
    )
    if proc.returncode != 0:
        return {"style": style, "ok": False, "error": proc.stdout[-600:] + proc.stderr[-600:]}
    cut = project / "deliverables" / "dlv_short0000001" / "exports" / "generated.mp4"
    if not cut.exists():
        return {"style": style, "ok": False, "error": "no cut produced"}
    target = out_dir / f"{style}.mp4"
    shutil.copy2(cut, target)
    drawings = len(
        list((project / "deliverables" / "dlv_short0000001" / "anchors").glob("*/*.png"))
    )
    return {
        "style": style,
        "ok": True,
        "minutes": round((time.time() - started) / 60, 1),
        "drawings": drawings,
        "video": str(target.relative_to(REPO_ROOT)),
    }


def main(argv: list[str]) -> int:
    from content_factory.sequences.styles import STYLE_PRESETS

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir")
    ap.add_argument("styles", nargs="*", default=[])
    ap.add_argument("--story", default="fixtures/story/love_story_reel.json")
    ap.add_argument("--shots", default="fixtures/shots/love_story_topdown_reel.json")
    args = ap.parse_args(argv)

    styles = args.styles or list(STYLE_PRESETS)
    unknown = [s for s in styles if s not in STYLE_PRESETS]
    if unknown:
        print(f"unknown style(s) {unknown}; known: {sorted(STYLE_PRESETS)}", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = REPO_ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for style in styles:
        result = render_style(style, out_dir, args.story, args.shots)
        results.append(result)
        print(json.dumps(result), flush=True)
        (out_dir / "reel.json").write_text(json.dumps(results, indent=1) + "\n")
    ok = sum(1 for r in results if r["ok"])
    print(json.dumps({"styles": len(results), "ok": ok, "out": str(out_dir)}))
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
