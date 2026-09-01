"""Kokoro executor script: reads text on stdin, prints one JSON line with wav path + token timestamps.

Run inside this skill's own environment: `uv run --project skills/audio/kokoro python run.py`.
Not imported by the control plane. Requires the model download on first use (~330 MB).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="af_heart")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--lang", default="en")
    args = ap.parse_args()
    text = sys.stdin.read().strip()
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    lang_code = "a" if args.lang.startswith("en") else "b"
    pipeline = KPipeline(lang_code=lang_code)
    chunks, tokens = [], []
    offset = 0.0
    for result in pipeline(text, voice=args.voice, speed=args.speed):
        audio = result.audio.numpy() if hasattr(result.audio, "numpy") else result.audio
        chunks.append(audio)
        for t in result.tokens or []:
            if t.start_ts is not None and t.end_ts is not None:
                tokens.append({"text": t.text, "start_ts": offset + t.start_ts, "end_ts": offset + t.end_ts})
        offset += len(audio) / 24000
    wav = np.concatenate(chunks)
    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)  # noqa: SIM115
    sf.write(out.name, wav, 24000)
    print(json.dumps({"wav": out.name, "sample_rate": 24000, "duration_ms": int(len(wav) * 1000 / 24000), "tokens": tokens, "model_revision": "hexgrad/Kokoro-82M"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
