"""Render a scratch track for a story fixture: one placeholder wav per beat, named for its take.

    uv run python scripts/make_placeholder_takes.py fixtures/story/love_story.json out/takes/love
    uv run python scripts/make_placeholder_takes.py <story.json> <dir> --voice kokoro

A scratch track is what an animation cut runs on before the real voices arrive: it holds the
timing so the picture can be cut, and it is replaced take by take. These files sit exactly where
the ``voice_over`` stage looks (``<dir>/<beat_id>.wav``), so replacing one with a real recording is
a file copy — nothing else in the graph changes.

The audio is synthesized and is **not** the operator's voice. Every file written here is recorded
in ``scratch.json`` beside them, so a run can say plainly which beats are still scratch.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from content_factory.audio.normalize import normalize_for_speech
from content_factory.audio.tts import KokoroTTS, MockTTS, TTSError
from content_factory.schemas.audio import NarrationRequest, VoiceIdentity
from content_factory.schemas.scenes import StoryPlan

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("story", help="StoryPlan fixture (repo-relative or absolute)")
    ap.add_argument("out_dir", help="where the takes are written")
    ap.add_argument("--voice", default="mock", choices=["mock", "kokoro"])
    ap.add_argument("--voice-id", default="af_heart", help="kokoro voice id")
    ap.add_argument("--speed", type=float, default=1.0)
    args = ap.parse_args(argv)

    story_path = Path(args.story)
    if not story_path.is_absolute():
        story_path = REPO_ROOT / story_path
    plan = StoryPlan.model_validate_json(story_path.read_text())
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.voice == "kokoro":
        tts = KokoroTTS(REPO_ROOT / "skills" / "audio" / "kokoro")
        voice = VoiceIdentity(
            provider="kokoro", voice_id=args.voice_id, model_revision="Kokoro-82M", speed=args.speed
        )
    else:
        tts = MockTTS()
        voice = VoiceIdentity(
            provider="mock", voice_id="scratch", model_revision="mock-1", speed=args.speed
        )

    written = []
    for beat in plan.beats:
        request = NarrationRequest(
            beat_id=beat.beat_id,
            display_text=beat.display_text,
            spoken_text=normalize_for_speech(beat.display_text),
            voice=voice,
        )
        try:
            result = tts.synthesize(request)
        except TTSError as exc:
            print(f"{args.voice} failed on {beat.beat_id}: {exc}", file=sys.stderr)
            return 3
        path = out_dir / f"{beat.beat_id}.scratch.wav"
        path.write_bytes(result.audio)
        written.append(
            {
                "beat_id": beat.beat_id,
                "file": path.name,
                "text": beat.display_text,
                "duration_ms": result.segment.duration_ms,
            }
        )
    (out_dir / "scratch.json").write_text(
        json.dumps(
            {"provider": args.voice, "story": str(story_path.name), "takes": written}, indent=1
        )
        + "\n"
    )
    total = sum(t["duration_ms"] for t in written) / 1000
    print(
        json.dumps(
            {
                "takes": len(written),
                "seconds": round(total, 1),
                "dir": str(out_dir),
                "provider": args.voice,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
