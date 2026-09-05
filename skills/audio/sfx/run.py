"""Stable Audio 3 Small-SFX: one prompt on stdin, one JSON line on stdout.

    echo "TrackType: SFX, a heavy iron gate closing in a stone courtyard." | \
      uv run --project skills/audio/sfx python skills/audio/sfx/run.py --duration 4 --seed 7

For the curated set use build_library.py; this is the ad-hoc path, for auditioning a prompt before
adding it to library.json. Not imported by the control plane.

Two defaults worth knowing (both explained at length in README.md):
  * the model is asked for at least 3 s even when --duration is shorter, because short requests are
    out of distribution and come back as broadband hiss; --duration then trims the result;
  * --loop wraps the take into a seamless bed instead of trimming it to an event.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import sfx

MIN_GEN_S = 3.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--duration", type=float, default=3.0, help="length of the finished file, seconds"
    )
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument(
        "--steps", type=int, default=8, help="8 is the post-trained default; higher rarely helps"
    )
    ap.add_argument("--loop", action="store_true", help="wrap into a seamless loop of --duration")
    ap.add_argument("--crossfade", type=float, default=3.0, help="loop crossfade, seconds")
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    ap.add_argument("--out", type=Path, default=None, help="output .wav (default: alongside cwd)")
    args = ap.parse_args()

    prompt = sys.stdin.read().strip()
    if not prompt:
        print("no prompt on stdin", file=sys.stderr)
        return 2

    sr = sfx.SAMPLE_RATE
    t0 = time.time()
    # stable_audio_3 writes loader diagnostics to stdout; this script's stdout is one JSON line.
    with contextlib.redirect_stdout(sys.stderr):
        model = sfx.load_model(device=args.device, half=(args.device == "cuda"))
    load_s = time.time() - t0

    t0 = time.time()
    if args.loop:
        lead = pad = 1.5
        raw = sfx.generate(
            model, prompt, lead + args.duration + args.crossfade + pad, args.seed, args.steps
        )
        raw = raw[:, int(lead * sr) : raw.shape[-1] - int(pad * sr)]
        x = sfx.loop_wrap(raw.astype(np.float64), sr, args.duration, args.crossfade)
        x = sfx.highpass(x, sr, 25)
        x, _, after = sfx.normalize(x, sr, "integrated_lufs", -23.0, -1.0)
        qc = {**sfx.tone_qc(x, sr), **sfx.loop_qc(x, sr)}
    else:
        raw = sfx.generate(
            model, prompt, max(MIN_GEN_S, args.duration + 1.0), args.seed, args.steps
        )
        x = sfx.trim_oneshot(raw.astype(np.float64), sr)
        cap = int(max(args.duration * 2.0, args.duration + 0.5) * sr)
        if x.shape[-1] > cap:
            x = x[:, :cap].copy()
            f = int(0.03 * sr)
            x[:, -f:] *= np.linspace(1.0, 0.0, f)
        x = sfx.highpass_linear(x, sr, 25)
        x, _, after = sfx.normalize(x, sr, "max_momentary_lufs", -16.0, -3.0)
        qc = sfx.tone_qc(x, sr)
    gen_s = time.time() - t0

    out = args.out or Path(f"sfx_{args.seed}.wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    sfx.write_wav(out, np.clip(x, -1.0, 1.0), sr, subtype="PCM_24")

    print(
        json.dumps(
            {
                "wav": str(out.resolve()),
                "prompt": prompt,
                "model": sfx.MODEL_ID,
                "sample_rate": sr,
                "channels": int(x.shape[0]),
                "duration_s": round(x.shape[-1] / sr, 4),
                "loop": bool(args.loop),
                "seed": args.seed,
                "steps": args.steps,
                "device": args.device,
                "load_s": round(load_s, 2),
                "generate_s": round(gen_s, 2),
                "loudness": {
                    k: round(v, 2) for k, v in after.items() if isinstance(v, (int, float))
                },
                "qc": qc,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
