"""Qwen3-TTS executor: text on stdin, one JSON line out (wav path, duration, model revision)."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MODELS = {
    "custom_voice": REPO_ROOT / "models" / "speech" / "Qwen3-TTS-12Hz-1.7B-CustomVoice",
    "base": REPO_ROOT / "models" / "speech" / "Qwen3-TTS-12Hz-1.7B-Base",
    "voice_design": REPO_ROOT / "models" / "speech" / "Qwen3-TTS-12Hz-1.7B-VoiceDesign",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="", help="path or hf id; default follows the mode")
    ap.add_argument("--speaker", default="", help="CustomVoice timbre, e.g. Ryan")
    ap.add_argument("--language", default="Auto", help="Auto | English | Chinese | Japanese | ...")
    ap.add_argument("--instruct", default="", help='style control, e.g. "Very happy."')
    ap.add_argument(
        "--describe",
        default="",
        help="VoiceDesign: build a voice from this description instead of picking a timbre",
    )
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
    design = bool(args.describe) and not clone
    if design:
        mode_default = MODELS["voice_design"]
    elif clone:
        mode_default = MODELS["base"]
    else:
        mode_default = MODELS["custom_voice"]
    model_path = args.model or str(mode_default)

    import torch
    from qwen_tts import Qwen3TTSModel

    # Before the model is built, so weight-init and generation both see it. Qwen3-TTS samples, so
    # without a seed a cache key over (voice, text) is a lie (journal 2026-09-07).
    if args.seed is not None:
        torch.manual_seed(args.seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(args.seed)

    t0 = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        model_path, device_map=args.device, dtype=torch.bfloat16, attn_implementation=args.attn
    )
    t_loaded = time.perf_counter()

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

    if design:
        # VoiceDesign has no built-in timbres: the description IS the voice, prepended to the
        # sequence as a control signal (paper 2601.15621, sec. "instruction following").
        wavs, sr = model.generate_voice_design(
            text=text, instruct=args.describe, language=args.language
        )
    elif clone:
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

    t_done = time.perf_counter()
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
                "mode": "voice_design" if design else ("voice_clone" if clone else "custom_voice"),
                "speaker": args.speaker or None,
                "describe": args.describe or None,
                "model_revision": Path(model_path).name,
                "seed": args.seed,
                "load_s": round(t_loaded - t0, 2),
                "generate_s": round(t_done - t_loaded, 2),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
