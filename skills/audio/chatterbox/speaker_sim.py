"""Cosine similarity of two voices through Chatterbox's voice encoder (MIT weights), CPU only."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL = REPO_ROOT / "models" / "speech" / "Chatterbox-Turbo"
ENCODER_SR = 16000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", type=Path, required=True, help="the creator's reference recording")
    ap.add_argument("--wav", type=Path, required=True, help="the synthesized take")
    ap.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL)
    args = ap.parse_args()
    weights = args.model_dir.expanduser() / "ve.safetensors"
    if not weights.is_file():
        print(f"voice encoder weights not found at {weights}", file=sys.stderr)
        return 3

    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    import librosa
    import numpy as np
    from chatterbox.models.voice_encoder import VoiceEncoder
    from safetensors.torch import load_file

    encoder = VoiceEncoder()
    encoder.load_state_dict(load_file(weights))
    encoder.eval()

    def embed(path: Path) -> np.ndarray:
        audio, _sr = librosa.load(str(path), sr=ENCODER_SR)
        return encoder.embeds_from_wavs([audio], sample_rate=ENCODER_SR).mean(axis=0)

    a, b = embed(args.ref), embed(args.wav)
    cosine = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    sys.stdout = real_stdout
    print(json.dumps({"cosine": round(cosine, 4), "encoder": "chatterbox-turbo/ve.safetensors"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
