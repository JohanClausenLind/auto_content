"""Resemble Enhance executor: restores one speech WAV (denoise + generative band repair).

Run inside this skill's own environment:
`uv run --project skills/audio/resemble_enhance python skills/audio/resemble_enhance/run.py
    --in narration.wav --out restored.wav`.
Not imported by the control plane. The inference code is the resemble-enhance checkout (MIT) at
CF_RESEMBLE_ENHANCE_REPO (default <repo>/external/resemble-enhance); the weights (also MIT) are at
CF_RESEMBLE_ENHANCE_MODEL_PATH (default <repo>/models/speech_restoration/ResembleEnhance).

Two modes:
  enhance  the full chain — UNet denoiser, then the latent CFM restorer and UnivNet vocoder that
           rebuild the missing high band. This is what fixes metallic/muffled TTS speech.
  denoise  the denoiser alone (cheaper, no generative step, no band repair).

The model works at 44.1 kHz and `inference()` returns exactly as many samples as it was given
(after resampling), so the duration in seconds is preserved and the caller's word timings stay
valid. The caller checks that anyway — this script also refuses to write a file whose duration
drifts more than --max-drift-ms from the input.

Prints one JSON line: out path, sample rate, durations, timings, peak VRAM.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path

MODEL_REVISION = "ResembleAI/resemble-enhance@4e3510c"
CODE_REVISION = "resemble-ai/resemble-enhance@8e97814"
RUN_NAME = "enhancer_stage2"
REPO_ROOT = Path(__file__).resolve().parents[3]  # skills/audio/resemble_enhance/run.py -> root
DEFAULT_REPO = REPO_ROOT / "external" / "resemble-enhance"
DEFAULT_MODEL = REPO_ROOT / "models" / "speech_restoration" / "ResembleEnhance"
# The three files upstream's own downloader fetches. Present-or-fail: a production run must never
# reach out to Hugging Face mid-pipeline.
REQUIRED = ("hparams.yaml", "ds/G/latest", "ds/G/default/mp_rank_00_model_states.pt")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_wav", type=Path, required=True)
    ap.add_argument("--out", dest="out_wav", type=Path, required=True)
    ap.add_argument("--mode", choices=("enhance", "denoise"), default="enhance")
    ap.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    # Upstream defaults for the CFM solver. nfe is the number of function evaluations: 32 is the
    # upstream CLI default, 64 the value the shipped hparams trained with.
    ap.add_argument("--nfe", type=int, default=32)
    ap.add_argument("--solver", choices=("midpoint", "rk4", "euler"), default="midpoint")
    ap.add_argument("--lambd", type=float, default=0.5, help="0=denoise less, 1=denoise more")
    ap.add_argument("--tau", type=float, default=0.5, help="CFM temperature")
    ap.add_argument("--chunk-seconds", type=float, default=30.0)
    ap.add_argument("--overlap-seconds", type=float, default=1.0)
    ap.add_argument("--max-drift-ms", type=int, default=10)
    args = ap.parse_args()

    repo = Path(os.environ.get("CF_RESEMBLE_ENHANCE_REPO", DEFAULT_REPO)).expanduser()
    model_dir = Path(os.environ.get("CF_RESEMBLE_ENHANCE_MODEL_PATH", DEFAULT_MODEL)).expanduser()
    run_dir = model_dir / RUN_NAME
    if not (repo / "resemble_enhance").is_dir():
        print(f"resemble-enhance checkout not found at {repo}", file=sys.stderr)
        return 3
    missing = [r for r in REQUIRED if not (run_dir / r).is_file()]
    if missing:
        print(
            f"Resemble Enhance weights incomplete at {run_dir}: missing {missing}", file=sys.stderr
        )
        return 3
    if not args.in_wav.is_file():
        print(f"input not found: {args.in_wav}", file=sys.stderr)
        return 2
    if args.device == "cpu":
        # Both the model and (importantly) deepspeed's accelerator probe read this.
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
    sys.path.insert(0, str(repo))

    # Upstream logs to stdout through rich/tqdm; keep our stdout clean for the one JSON line.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    import soundfile as sf
    import torch
    from resemble_enhance.enhancer.enhancer import Enhancer
    from resemble_enhance.enhancer.hparams import HParams
    from resemble_enhance.inference import inference

    if args.device == "cuda" and not torch.cuda.is_available():
        print("--device cuda but torch reports no CUDA device", file=sys.stderr)
        return 4
    device = torch.device(args.device)

    t0 = time.perf_counter()
    hp = HParams.load(run_dir)
    enhancer = Enhancer(hp)
    # The checkpoint is a DeepSpeed model-state file we downloaded ourselves from the pinned
    # revision above, so it is trusted; weights_only=False is required because it also carries the
    # training config next to the tensors.
    state = torch.load(
        run_dir / "ds" / "G" / "default" / "mp_rank_00_model_states.pt",
        map_location="cpu",
        weights_only=False,
    )["module"]
    enhancer.load_state_dict(state)
    enhancer.eval().to(device)
    load_ms = (time.perf_counter() - t0) * 1000

    # soundfile, not torchaudio.load: torchaudio 2.9 routes its loader through TorchCodec, and
    # every input here is a plain WAV.
    samples, sr = sf.read(str(args.in_wav), dtype="float32", always_2d=True)
    dwav = torch.from_numpy(samples.mean(axis=1))  # mono; narration stems are mono anyway
    in_duration_ms = round(dwav.shape[-1] * 1000 / sr)

    model = enhancer.denoiser if args.mode == "denoise" else enhancer
    if args.mode == "enhance":
        enhancer.configurate_(nfe=args.nfe, solver=args.solver, lambd=args.lambd, tau=args.tau)

    t1 = time.perf_counter()
    with torch.inference_mode():
        hwav, out_sr = inference(
            model=model,
            dwav=dwav,
            sr=sr,
            device=device,
            chunk_seconds=args.chunk_seconds,
            overlap_seconds=args.overlap_seconds,
        )
    infer_s = time.perf_counter() - t1

    out_duration_ms = round(hwav.shape[-1] * 1000 / out_sr)
    drift = abs(out_duration_ms - in_duration_ms)
    if drift > args.max_drift_ms:
        print(
            f"refusing to write: {in_duration_ms} ms in, {out_duration_ms} ms out"
            f" ({drift} ms > --max-drift-ms {args.max_drift_ms}) — word timings would break",
            file=sys.stderr,
        )
        return 5

    args.out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(args.out_wav), hwav.cpu().numpy(), int(out_sr), subtype="PCM_16")

    peak_vram_mb = None
    if args.device == "cuda":
        with contextlib.suppress(Exception):
            peak_vram_mb = round(torch.cuda.max_memory_allocated() / 2**20)

    sys.stdout = real_stdout
    print(
        json.dumps(
            {
                "wav": str(args.out_wav),
                "mode": args.mode,
                "sample_rate": int(out_sr),
                "in_sample_rate": int(sr),
                "in_duration_ms": in_duration_ms,
                "duration_ms": out_duration_ms,
                "device": args.device,
                "nfe": args.nfe,
                "solver": args.solver,
                "lambd": args.lambd,
                "tau": args.tau,
                "load_ms": round(load_ms),
                "infer_s": round(infer_s, 3),
                "rtf": round(infer_s / max(out_duration_ms / 1000, 1e-6), 3),
                "peak_vram_mb": peak_vram_mb,
                "model_revision": MODEL_REVISION,
                "code_revision": CODE_REVISION,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
