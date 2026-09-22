"""CTC forced alignment: wav2vec2 emissions in, per-word millisecond spans and scores out."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

MODEL_ID = "facebook/wav2vec2-base-960h"
SAMPLE_RATE = 16000
FRAME_MS = 20
WINDOW_S = 20.0
OVERLAP_S = 1.0
WORD_SEPARATOR = "|"


def spell(word: str, vocab: dict[str, int]) -> list[int]:
    """The word as vocabulary ids: uppercase letters and the apostrophe; anything else dropped."""
    return [vocab[ch] for ch in word.upper() if ch in vocab and ch != WORD_SEPARATOR]


def load_audio(path: Path) -> Any:
    import soundfile as sf
    import torch
    from torchaudio.functional import resample

    samples, rate = sf.read(str(path), dtype="float32", always_2d=True)
    wave = torch.from_numpy(samples.mean(axis=1))
    if rate != SAMPLE_RATE:
        wave = resample(wave, rate, SAMPLE_RATE)
    return wave


def emissions(model: Any, processor: Any, wave: Any) -> Any:
    """Per-frame log-probabilities; long audio runs in 20 s windows stitched mid-overlap."""
    import torch

    window = int(WINDOW_S * SAMPLE_RATE)
    overlap = int(OVERLAP_S * SAMPLE_RATE)
    hop = window - overlap
    half = int(OVERLAP_S * 1000 / FRAME_MS) // 2
    pieces = []
    start = 0
    while True:
        chunk = wave[start : start + window]
        last = start + window >= len(wave)
        inputs = processor(chunk.numpy(), sampling_rate=SAMPLE_RATE, return_tensors="pt")
        with torch.inference_mode():
            logits = model(inputs.input_values).logits[0]
        frames = torch.log_softmax(logits, dim=-1)
        lo = half if start > 0 else 0
        hi = frames.shape[0] if last else frames.shape[0] - half
        pieces.append(frames[lo:hi])
        if last:
            break
        start += hop
        # A tail shorter than the overlap is already covered by the previous window.
        if len(wave) - start <= overlap:
            break
    return torch.cat(pieces)


def align(
    log_probs: Any, words: list[str], vocab: dict[str, int]
) -> tuple[list[dict[str, Any]], list[int]]:
    import torch
    from torchaudio.functional import forced_align, merge_tokens

    spelled = [spell(w, vocab) for w in words]
    unmatched = [i for i, ids in enumerate(spelled) if not ids]
    kept = [(i, ids) for i, ids in enumerate(spelled) if ids]
    if not kept:
        return [], unmatched
    targets: list[int] = []
    for k, (_, ids) in enumerate(kept):
        if k:
            targets.append(vocab[WORD_SEPARATOR])
        targets.extend(ids)
    path, scores = forced_align(
        log_probs[None], torch.tensor([targets], dtype=torch.int32), blank=0
    )
    spans = merge_tokens(path[0], scores[0].exp(), blank=0)
    out: list[dict[str, Any]] = []
    cursor = 0
    for k, (index, ids) in enumerate(kept):
        if k:
            cursor += 1
        own = spans[cursor : cursor + len(ids)]
        cursor += len(ids)
        out.append(
            {
                "index": index,
                "start_ms": own[0].start * FRAME_MS,
                "end_ms": own[-1].end * FRAME_MS,
                "score": round(sum(s.score for s in own) / len(own), 4),
            }
        )
    return out, unmatched


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audio", required=True, type=Path)
    ap.add_argument("--words", required=True, help="JSON list of spoken words, letters only")
    ap.add_argument("--model", required=True, type=Path, help="local wav2vec2-base-960h directory")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--threads", type=int, default=min(8, os.cpu_count() or 1))
    args = ap.parse_args()
    words = [str(w) for w in json.loads(args.words)]
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    import torch
    import torchaudio
    from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor

    torch.set_num_threads(args.threads)
    processor = Wav2Vec2Processor.from_pretrained(str(args.model), local_files_only=True)
    model = Wav2Vec2ForCTC.from_pretrained(str(args.model), local_files_only=True).eval()
    vocab: dict[str, int] = processor.tokenizer.get_vocab()
    log_probs = emissions(model, processor, load_audio(args.audio))
    try:
        aligned, unmatched = align(log_probs, words, vocab)
    except (RuntimeError, ValueError) as exc:
        print(f"alignment failed: {exc}", file=sys.stderr)
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "words": aligned,
                "unmatched": unmatched,
                "aligner": "torchaudio.forced_align",
                "aligner_version": torchaudio.__version__,
                "model_id": MODEL_ID,
                "frames": int(log_probs.shape[0]),
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
