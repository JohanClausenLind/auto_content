"""Render a ShotSpec's control passes with headless Blender (one-shot skill runner).

Usage:
    uv run --project skills/video/blender_scene python skills/video/blender_scene/render.py \
        <spec.json> <out_dir> [--blender PATH] [--engine workbench|eevee|cycles_cpu] \
        [--frames 0,48,96] [--assets ROOT] [--keep-raw] [--timeout SECONDS]

Exit codes: 0 ok · 2 invalid spec · 3 exception inside Blender · 4 blender binary or asset
missing · 5 timeout · 6 post-processing / verification failure. The last stdout line is one JSON
summary; Blender's own output goes to <out_dir>/logs/.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from postprocess import PostprocessError, finalize  # noqa: E402
from spec import SpecError, load_spec, validate  # noqa: E402

EXIT_OK, EXIT_SPEC, EXIT_BLENDER, EXIT_MISSING, EXIT_TIMEOUT, EXIT_POST = 0, 2, 3, 4, 5, 6


def find_blender(explicit: str | None) -> str | None:
    for cand in (
        explicit,
        os.environ.get("CF_BLENDER_BIN"),
        shutil.which("blender"),
        "/snap/bin/blender",
    ):
        if cand and (Path(cand).exists() or shutil.which(cand)):
            return cand
    return None


def blender_command(blender: str, spec_path: Path, raw_dir: Path, assets_root: Path) -> list[str]:
    return [
        blender,
        "--background",
        "--factory-startup",
        "--python-exit-code",
        str(EXIT_BLENDER),
        "--python",
        str(SKILL_DIR / "blender_entry.py"),
        "--",
        str(spec_path),
        str(raw_dir),
        f"--assets={assets_root}",
    ]


def _summary(**fields: object) -> None:
    sys.stdout.write(json.dumps(fields, sort_keys=True) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("spec")
    ap.add_argument("out_dir")
    ap.add_argument("--blender", default=None)
    ap.add_argument("--engine", choices=["workbench", "eevee", "cycles_cpu"], default=None)
    ap.add_argument("--frames", default=None, help="comma-separated frame indices to render")
    ap.add_argument(
        "--assets", default=os.environ.get("CF_BLENDER_ASSETS", "/mnt/fast/models/blender-assets")
    )
    ap.add_argument("--keep-raw", action="store_true")
    ap.add_argument("--timeout", type=float, default=1800.0)
    args = ap.parse_args(argv)

    t0 = time.time()
    out_dir = Path(args.out_dir)
    try:
        spec = load_spec(Path(args.spec))
        if args.engine:
            spec["render"]["engine"] = args.engine
        if args.frames:
            spec["render"]["frames"] = [int(v) for v in args.frames.split(",") if v.strip()]
        spec = validate(spec)
    except (SpecError, OSError, ValueError) as exc:
        _summary(ok=False, exit=EXIT_SPEC, error=f"invalid spec: {exc}")
        return EXIT_SPEC

    blender = find_blender(args.blender)
    if blender is None:
        _summary(
            ok=False,
            exit=EXIT_MISSING,
            error="blender binary not found (use --blender or CF_BLENDER_BIN)",
        )
        return EXIT_MISSING
    assets_root = Path(args.assets).expanduser()
    for c in spec["characters"]:
        blend = assets_root / "characters" / c["asset"] / f"{c['asset']}.blend"
        if not blend.exists():
            _summary(ok=False, exit=EXIT_MISSING, error=f"character asset missing: {blend}")
            return EXIT_MISSING

    out_dir.mkdir(parents=True, exist_ok=True)
    logs = out_dir / "logs"
    logs.mkdir(exist_ok=True)
    raw_dir = out_dir / "raw"
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir()
    spec_copy = raw_dir / "spec.json"
    spec_copy.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    cmd = blender_command(blender, spec_copy, raw_dir, assets_root)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "CF_BLENDER_ASSETS": str(assets_root)}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=args.timeout, env=env
        )
    except subprocess.TimeoutExpired:
        _summary(ok=False, exit=EXIT_TIMEOUT, error=f"blender timed out after {args.timeout}s")
        return EXIT_TIMEOUT
    (logs / "blender.stdout.txt").write_text(proc.stdout, encoding="utf-8")
    (logs / "blender.stderr.txt").write_text(proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout)[-1200:]
        _summary(
            ok=False, exit=EXIT_BLENDER, returncode=proc.returncode, error=f"blender failed: {tail}"
        )
        return EXIT_BLENDER

    try:
        metadata = finalize(raw_dir, out_dir, spec, keep_raw=args.keep_raw)
    except (PostprocessError, OSError, KeyError, ValueError) as exc:
        _summary(ok=False, exit=EXIT_POST, error=f"postprocess failed: {exc}")
        return EXIT_POST

    run = {
        "out_dir": str(out_dir),
        "frames": len(metadata["frames_rendered"]),
        "passes": metadata["passes"],
        "engines": metadata["engines"],
        "blender_version": metadata["blender_version"],
        "spec_sha256": metadata["spec_sha256"],
        "elapsed_s": round(time.time() - t0, 3),
    }
    (out_dir / "run.json").write_text(
        json.dumps(run, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    _summary(ok=True, exit=EXIT_OK, **run)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
