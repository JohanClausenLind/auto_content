"""Qwen3-TTS executor: text on stdin, one JSON line out (wav path, duration, model revision).

Run inside this skill's own environment:
    echo "The last train leaves at eleven." | \
      uv run --project skills/audio/qwen3tts python skills/audio/qwen3tts/run.py \
      --speaker Ryan --language English

Three modes, matching what the two downloaded models can actually do:

* ``--speaker NAME``            CustomVoice, one of the nine built-in timbres
* ``--speaker NAME --instruct`` CustomVoice with natural-language style control
* ``--ref-audio W --ref-text T`` Base, cloning the voice in ``W`` (Base has no built-in voices)

``--list`` prints the speakers and languages the chosen weights actually declare, and exits.
Shaped like skills/audio/kokoro/run.py: the control plane would talk to this over stdin/stdout
and never import it. Qwen3-TTS gives no word timestamps, so ``tokens`` is always empty — a
narrated timeline still needs faster-whisper/WhisperX alignment (ADR-0004).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MODELS = {
    "custom_voice": REPO_ROOT / "models" / "speech" / "Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "base": REPO_ROOT / "models" / "speech" / "Qwen3-TTS-12Hz-1.7B-Base",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="", help="path or hf id; default follows the mode")
    ap.add_argument("--speaker", default="", help="CustomVoice timbre, e.g. Ryan")
    ap.add_argument("--language", default="Auto", help="Auto | English | Chinese | Japanese | ...")
    ap.add_argument("--instruct", default="", help='style control, e.g. "Very happy."')
    ap.add_argument("--ref-audio", default="", help="Base voice clone: reference wav/URL")
    ap.add_argument("--ref-text", default="", help="Base voice clone: transcript of --ref-audio")
    ap.add_argument("--x-vector-only", action="store_true", help="clone without a transcript")
    ap.add_argument("--out", default="", help="output wav (default: a temp file)")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--attn", default="sdpa", choices=["sdpa", "flash_attention_2", "eager"])
    ap.add_argument("--list", action="store_true", help="print supported speakers/languages, exit")
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="seed torch's generators so one take is reproducible (and a retake is a choice)",
    )
    args = ap.parse_args()

    clone = bool(args.ref_audio)
    model_path = args.model or str(MODELS["base"] if clone else MODELS["custom_voice"])

    import torch
    from qwen_tts import Qwen3TTSModel

    # Seeded before the model is built, so weight-init and generation both see it. Qwen3-TTS
    # samples, so two runs of the same text differ in timing by tens of milliseconds and in
    # delivery audibly — which makes a cache key over (voice, text) a lie and makes "run it again,
    # that take was odd" an untraceable change. With a seed the take is a fact about the inputs,
    # and a retake is an explicit different seed rather than a dice roll.
    if args.seed is not None:
        torch.manual_seed(args.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(args.seed)

    model = Qwen3TTSModel.from_pretrained(
        model_path, device_map=args.device, dtype=torch.bfloat16, attn_implementation=args.attn
    )

    if args.list:
        print(
            json.dumps(
                {
                    "model": model_path,
                    "speakers": model.get_supported_speakers(),
                    "languages": model.get_supported_languages(),
                },
                ensure_ascii=False,
            )
        )
        return 0

    text = sys.stdin.read().strip()
    if not text:
        print("no text on stdin", file=sys.stderr)
        return 2

    if clone:
        if not args.ref_text and not args.x_vector_only:
            print("--ref-text is required unless --x-vector-only", file=sys.stderr)
            return 2
        wavs, sr = model.generate_voice_clone(
            text=text,
            language=args.language,
            ref_audio=args.ref_audio,
            ref_text=args.ref_text or None,
            x_vector_only_mode=args.x_vector_only,
        )
    else:
        if not args.speaker:
            print("--speaker is required (or use --ref-audio to clone)", file=sys.stderr)
            return 2
        wavs, sr = model.generate_custom_voice(
            text=text,
            language=args.language,
            speaker=args.speaker,
            instruct=args.instruct or None,
        )

    import soundfile as sf

    out = args.out or tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    sf.write(out, wavs[0], sr)
    print(
        json.dumps(
            {
                "wav": out,
                "sample_rate": sr,
                "duration_ms": int(len(wavs[0]) * 1000 / sr),
                "tokens": [],  # Qwen3-TTS returns no word timings; align separately (ADR-0004)
                "mode": "voice_clone" if clone else "custom_voice",
                "speaker": args.speaker or None,
                "model_revision": Path(model_path).name,
                "seed": args.seed,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
