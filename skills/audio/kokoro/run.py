"""Kokoro executor: reads text on stdin, prints one JSON line (wav path + token timestamps).

Run inside this skill's own environment: `uv run --project skills/audio/kokoro python run.py`.
Not imported by the control plane. Requires the model download on first use (~330 MB).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile

# Locale -> Kokoro lang code, mirroring content_factory/audio/languages.py. Explicit, because the
# line this replaces was `"a" if lang.startswith("en") else "b"` — and "b" is British English, so
# every non-English locale silently produced a British voice reading foreign words as English.
# Kokoro's own LANG_CODES (read from the installed package, 2026-09-08): a American English,
# b British English, e es, f fr-fr, h hi, i it, p pt-br, j Japanese, z Mandarin Chinese.
LANG_CODES = {
    "en": "a",
    "en-gb": "b",
    "es": "e",
    "fr": "f",
    "hi": "h",
    "it": "i",
    "pt": "p",
    "ja": "j",
    "zh": "z",
}


def lang_code(lang: str) -> str:
    """Kokoro's code for a locale, or the code itself if one was passed. Refuses anything else.

    A locale Kokoro cannot speak is an error here and not a fallback: a fallback is what produced
    a whole film narrated in the wrong language with nothing in the run reporting it.
    """
    value = lang.strip().lower()
    if value in set(LANG_CODES.values()):
        return value
    if value in LANG_CODES:
        return LANG_CODES[value]
    prefix = value.split("-", 1)[0]
    if prefix in LANG_CODES:
        return LANG_CODES[prefix]
    supported = ", ".join(sorted(LANG_CODES))
    msg = f"kokoro cannot speak {lang!r}; it supports: {supported}"
    raise SystemExit(msg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="af_heart")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--lang", default="en", help="locale (en, en-GB, ja, ...) or a kokoro code")
    ap.add_argument(
        "--device",
        default="cpu",
        choices=["cpu", "cuda"],
        help="cpu (default: the GPU is usually held by the image/video models) or cuda",
    )
    args = ap.parse_args()
    # Resolved before the import below, so a bad locale costs nothing: the model never loads.
    code = lang_code(args.lang)
    text = sys.stdin.read().strip()
    import numpy as np
    import soundfile as sf
    from kokoro import KPipeline

    pipeline = KPipeline(lang_code=code, device=args.device)
    chunks, tokens = [], []
    offset = 0.0
    for result in pipeline(text, voice=args.voice, speed=args.speed):
        audio = result.audio.numpy() if hasattr(result.audio, "numpy") else result.audio
        chunks.append(audio)
        for t in result.tokens or []:
            if t.start_ts is not None and t.end_ts is not None:
                tokens.append(
                    {"text": t.text, "start_ts": offset + t.start_ts, "end_ts": offset + t.end_ts}
                )
        offset += len(audio) / 24000
    wav = np.concatenate(chunks)
    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(out.name, wav, 24000)
    print(
        json.dumps(
            {
                "wav": out.name,
                "sample_rate": 24000,
                "duration_ms": int(len(wav) * 1000 / 24000),
                "tokens": tokens,
                "model_revision": "hexgrad/Kokoro-82M",
                "lang_code": code,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
