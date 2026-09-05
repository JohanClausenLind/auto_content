"""ClearerVoice-Studio executor: speech enhancement, or 48 kHz speech super-resolution.

Run inside this skill's own environment:
`uv run --project skills/audio/clearervoice python skills/audio/clearervoice/run.py
    --in narration.wav --out clean.wav --task enhancement`.
Not imported by the control plane. The inference code is the published `clearvoice` wheel
(Apache-2.0, modelscope/ClearerVoice-Studio); the weights (also Apache-2.0) are at
CF_CLEARERVOICE_MODEL_ROOT (default <repo>/models/speech_restoration).

  enhancement        MossFormer2_SE_48K — removes noise, hum and room from 48 kHz speech.
  super_resolution   MossFormer2_SR_48K — rebuilds the band a 16/24 kHz TTS never produced.

Both models are 48 kHz. Input is resampled to 48 kHz here and the output is written at 48 kHz —
upstream's own file writer resamples back to the input rate, which would throw away exactly the
band super-resolution just created, so this script bypasses it and writes the returned array.

Upstream resolves `checkpoint_dir` relative to the working directory and downloads from Hugging
Face when it is missing. This script runs in a private temporary directory whose
`checkpoints/<MODEL>` is a symlink to the pinned local weights, so a production run never reaches
the network. Both models preserve length exactly; the script still refuses to write a file whose
duration drifted more than --max-drift-ms.

Prints one JSON line: out path, sample rate, durations, timings, peak VRAM.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

MODEL_RATE_HZ = 48000
TASKS = {
    # our name           (upstream task,          upstream model,       pinned HF revision)
    "enhancement": ("speech_enhancement", "MossFormer2_SE_48K", "eff8c97"),
    "super_resolution": ("speech_super_resolution", "MossFormer2_SR_48K", "39eb1f2"),
}
CODE_REVISION = "clearvoice==0.1.2 (modelscope/ClearerVoice-Studio@6b3774d)"
REPO_ROOT = Path(__file__).resolve().parents[3]  # skills/audio/clearervoice/run.py -> repo root
DEFAULT_MODEL_ROOT = REPO_ROOT / "models" / "speech_restoration"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_wav", type=Path, required=True)
    ap.add_argument("--out", dest="out_wav", type=Path, required=True)
    ap.add_argument("--task", choices=tuple(TASKS), default="enhancement")
    ap.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    ap.add_argument("--max-drift-ms", type=int, default=10)
    args = ap.parse_args()

    task, model_name, model_revision = TASKS[args.task]
    model_root = Path(os.environ.get("CF_CLEARERVOICE_MODEL_ROOT", DEFAULT_MODEL_ROOT)).expanduser()
    model_dir = model_root / model_name
    manifest = model_dir / "last_best_checkpoint"
    if not manifest.is_file():
        print(
            f"{model_name} weights not found at {model_dir} (no last_best_checkpoint)",
            file=sys.stderr,
        )
        return 3
    missing = [n for n in manifest.read_text().split() if not (model_dir / n).is_file()]
    if missing:
        print(f"{model_name} weights incomplete at {model_dir}: missing {missing}", file=sys.stderr)
        return 3
    if not args.in_wav.is_file():
        print(f"input not found: {args.in_wav}", file=sys.stderr)
        return 2
    if args.device == "cpu":
        # Upstream picks the GPU with the most free memory whenever torch can see one.
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    # Upstream prints progress to stdout; keep our stdout clean for the one JSON line.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    import librosa
    import numpy as np
    import soundfile as sf
    import torch

    if args.device == "cuda" and not torch.cuda.is_available():
        print("--device cuda but torch reports no CUDA device", file=sys.stderr)
        return 4

    samples, sr = sf.read(str(args.in_wav), dtype="float32", always_2d=True)
    mono = samples.mean(axis=1)
    in_duration_ms = round(len(mono) * 1000 / sr)
    if sr != MODEL_RATE_HZ:
        mono = librosa.resample(mono, orig_sr=sr, target_sr=MODEL_RATE_HZ, res_type="soxr_hq")

    with tempfile.TemporaryDirectory(prefix="cf-clearervoice-") as tmp:
        tmpdir = Path(tmp)
        (tmpdir / "checkpoints").mkdir()
        (tmpdir / "checkpoints" / model_name).symlink_to(model_dir.resolve())
        staged = tmpdir / "input.wav"
        sf.write(str(staged), mono, MODEL_RATE_HZ, subtype="PCM_16")
        cwd = Path.cwd()
        try:
            os.chdir(tmpdir)
            t0 = time.perf_counter()
            from clearvoice import ClearVoice

            cv = ClearVoice(task=task, model_names=[model_name])
            load_ms = (time.perf_counter() - t0) * 1000
            t1 = time.perf_counter()
            out = cv(input_path=str(staged), online_write=False)
            infer_s = time.perf_counter() - t1
        finally:
            os.chdir(cwd)

    out = np.asarray(out, dtype=np.float32)
    if out.ndim == 2:  # (channels, samples) for a single-speaker model
        out = out[0]
    out = np.squeeze(out)
    if out.ndim != 1:
        print(f"unexpected output shape {out.shape} from {model_name}", file=sys.stderr)
        return 6

    out_duration_ms = round(len(out) * 1000 / MODEL_RATE_HZ)
    drift = abs(out_duration_ms - in_duration_ms)
    if drift > args.max_drift_ms:
        print(
            f"refusing to write: {in_duration_ms} ms in, {out_duration_ms} ms out"
            f" ({drift} ms > --max-drift-ms {args.max_drift_ms}) — word timings would break",
            file=sys.stderr,
        )
        return 5

    args.out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(args.out_wav), out, MODEL_RATE_HZ, subtype="PCM_16")

    peak_vram_mb = None
    if args.device == "cuda":
        with contextlib.suppress(Exception):
            peak_vram_mb = round(torch.cuda.max_memory_allocated() / 2**20)

    sys.stdout = real_stdout
    print(
        json.dumps(
            {
                "wav": str(args.out_wav),
                "task": args.task,
                "model": model_name,
                "sample_rate": MODEL_RATE_HZ,
                "in_sample_rate": int(sr),
                "in_duration_ms": in_duration_ms,
                "duration_ms": out_duration_ms,
                "device": args.device,
                "load_ms": round(load_ms),
                "infer_s": round(infer_s, 3),
                "rtf": round(infer_s / max(out_duration_ms / 1000, 1e-6), 3),
                "peak_vram_mb": peak_vram_mb,
                "model_revision": f"alibabasglab/{model_name}@{model_revision}",
                "code_revision": CODE_REVISION,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
