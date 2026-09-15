"""Derive ``tracks.json`` for the music library from the library's own build manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MOODS_MAX = 8
"""``MusicTrack.moods`` is capped at eight. The category leads, then the build tags in their own
order, so the mood a lane asks for (`calm`, `tension`) is matched by the same list every run."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(library: Path) -> list[dict]:
    manifest = json.loads((library / "manifest.json").read_text())
    licence = manifest.get("licence", "")
    model = manifest.get("model_id") or manifest.get("model") or "unknown model"
    tracks: list[dict] = []
    for sound in manifest["sounds"]:
        path = library / sound["file"]
        if not path.exists():
            msg = f"{sound['id']}: manifest names {sound['file']}, which is not on disk"
            raise SystemExit(msg)
        digest = _sha256(path)
        if digest != sound["sha256"]:
            msg = f"{sound['id']}: {sound['file']} does not match the build manifest hash"
            raise SystemExit(msg)
        moods: list[str] = []
        for mood in (sound["category"], *sound.get("tags", ())):
            mood = mood.strip().lower()
            if mood and mood not in moods:
                moods.append(mood)
        tracks.append(
            {
                "track_id": sound["id"],
                "filename": sound["file"],
                "sha256": digest,
                "duration_s": round(float(sound["duration_s"]), 6),
                "moods": moods[:MOODS_MAX],
                # The model and its licence stand in for a composer: MiniMax-Music3's terms require
                # disclosure of AI generation on public distribution, and this string carries it.
                "attribution": f"Generated locally with {model}. {licence}"[:500],
            }
        )
    tracks.sort(key=lambda t: t["track_id"])
    return tracks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--library", default="assets/music")
    ap.add_argument(
        "--check",
        action="store_true",
        help="fail if the index on disk is not what this would write",
    )
    args = ap.parse_args()
    library = Path(args.library)
    if not library.is_absolute():
        library = REPO_ROOT / library
    tracks = build(library)
    out = library / "tracks.json"
    text = json.dumps(tracks, indent=1, sort_keys=True) + "\n"
    if args.check:
        current = out.read_text() if out.exists() else ""
        if current != text:
            print(f"{out} is stale; run without --check", file=sys.stderr)
            return 1
        print(json.dumps({"ok": True, "tracks": len(tracks)}))
        return 0
    out.write_text(text)
    moods = sorted({m for t in tracks for m in t["moods"]})
    print(json.dumps({"tracks": len(tracks), "index": str(out), "moods": moods}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
