"""Derive ``tracks.json`` for the music library from the library's own build manifest.

Two manifests describe ``assets/music`` and they answer different questions. ``manifest.json`` is
what ``skills/audio/music/build_library.py`` wrote: generator, model, seeds, loudness, QC, the full
provenance of twenty-two generated beds. ``tracks.json`` is what ``stage_select_music`` reads, and
it is a list of :class:`MusicTrack` — the four fields a selection needs plus the attribution that
travels into a destination package.

The stage was pointed at ``fixtures/music`` (one synthesised sine chord) while this library sat
unread beside it, so every run that ever selected music selected ``calm_bed_a``, because it was the
only track there was. Nothing was wrong with the stage; the library simply had no index in the
shape the stage validates. This writes one.

Re-runnable and deterministic: the hashes come from the files on disk and are checked against the
build manifest, so a track edited after its build fails here rather than at run time.

    uv run python scripts/build_music_index.py [--library assets/music] [--check]
"""

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
                # Generated locally, so the attribution is the model and its licence rather than a
                # composer. The licence line matters downstream: MiniMax-Music3's community terms
                # require disclosure of AI generation on anything publicly distributed, and this
                # string is what carries that into a destination package.
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
