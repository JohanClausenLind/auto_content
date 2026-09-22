"""Chatterbox Turbo executor: text in, one JSON line out; local weights only, never the Hub."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

# Set before any import that could reach the Hub: the weights are on disk, a run never downloads.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL = REPO_ROOT / "models" / "speech" / "Chatterbox-Turbo"
MODEL_REVISION = "ResembleAI/chatterbox-turbo@749d1c1"
# Turbo stops at 1000 speech tokens (~40 s of audio), so sentences are packed into chunks under
# this many characters and the chunks are joined with a short pause.
MAX_CHUNK_CHARS = 400
CHUNK_GAP_S = 0.25
_SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+")


def chunk_sentences(text: str, max_chars: int) -> list[str]:
    """Whole sentences packed greedily under `max_chars`; one over-long sentence stands alone."""
    chunks: list[str] = []
    current = ""
    for sentence in (s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()):
        if current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="", help="text to speak (default: stdin)")
    ap.add_argument("--out", default="", help="output wav (default: a temp file)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--ref-audio", type=Path, default=None, help="clone this voice (> 5 s wav)")
    ap.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--max-chunk-chars", type=int, default=MAX_CHUNK_CHARS)
    args = ap.parse_args()

    text = args.text.strip() or sys.stdin.read().strip()
    if not text:
        print("no text given", file=sys.stderr)
        return 2
    model_dir = args.model_dir.expanduser()
    if not (model_dir / "t3_turbo_v1.safetensors").is_file():
        print(f"Chatterbox Turbo weights not found at {model_dir}", file=sys.stderr)
        return 3

    # The library prints its warnings to stdout; keep ours for the one JSON line.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    import numpy as np
    import soundfile as sf
    import torch
    from chatterbox.tts_turbo import ChatterboxTurboTTS

    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    t0 = time.perf_counter()
    model = ChatterboxTurboTTS.from_local(model_dir, args.device)
    if args.ref_audio is not None:
        model.prepare_conditionals(str(args.ref_audio))
    t_loaded = time.perf_counter()
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    chunks = chunk_sentences(text, args.max_chunk_chars)
    gap = np.zeros(int(CHUNK_GAP_S * model.sr), dtype=np.float32)
    pieces: list[np.ndarray] = []
    for index, chunk in enumerate(chunks):
        wav = model.generate(chunk)
        pieces.append(wav.squeeze(0).cpu().numpy().astype(np.float32))
        if index < len(chunks) - 1:
            pieces.append(gap)
    audio = np.concatenate(pieces)
    t_done = time.perf_counter()

    out = args.out or tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    sf.write(out, audio, model.sr, subtype="PCM_16")
    sys.stdout = real_stdout
    print(
        json.dumps(
            {
                "wav": out,
                "duration_s": round(len(audio) / model.sr, 3),
                "sample_rate": int(model.sr),
                "peak_vram_mib": (
                    round(torch.cuda.max_memory_allocated() / 2**20)
                    if torch.cuda.is_available()
                    else None
                ),
                "wall_s": round(t_done - t_loaded, 3),
                "load_s": round(t_loaded - t0, 2),
                "chunks": len(chunks),
                "seed": args.seed,
                "mode": "clone" if args.ref_audio is not None else "builtin_voice",
                "model_revision": MODEL_REVISION,
                "watermark": "perth",
                "device": args.device,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
