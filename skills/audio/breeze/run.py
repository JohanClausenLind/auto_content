"""Breeze TTS 2 executor: reads text on stdin, prints one JSON line (wav path, no timestamps).

Run inside this skill's own environment:
`uv run --project skills/audio/breeze python skills/audio/breeze/run.py --instruction "..."`.
Not imported by the control plane. The inference code is the breeze-tts checkout (Apache-2.0)
at CF_BREEZE_REPO (default <repo>/external/breeze-tts); the weights are at CF_BREEZE_MODEL_PATH
(default <repo>/models/speech/Breeze-TTS-2; research/non-commercial licence).

Breeze emits streaming PCM only — `tokens` is always empty, so the caller must obtain word
timings by forced alignment (ADR-0004: WhisperX / faster-whisper), never from this script.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

MODEL_REVISION = "BreezeBlue/Breeze-TTS-2@799624c"
REPO_ROOT = Path(__file__).resolve().parents[3]  # skills/audio/breeze/run.py -> repo root
DEFAULT_REPO = REPO_ROOT / "external" / "breeze-tts"
DEFAULT_MODEL = REPO_ROOT / "models" / "speech" / "Breeze-TTS-2"
MAX_NEW_TOKENS = 1500
MAX_SEQ_LEN = 2048
REPETITION_PENALTY = 1.1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instruction", default="Speak clearly and naturally.")
    ap.add_argument("--ref-audio", type=Path, default=None)
    ap.add_argument("--ref-text", default=None)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cfg-scale", type=float, default=1.0)
    ap.add_argument("--fast-all", action="store_true", help="CUDA-graph fast path (~14 GiB)")
    args = ap.parse_args()
    text = sys.stdin.read().strip()
    if not text:
        print("no text on stdin", file=sys.stderr)
        return 2
    if (args.ref_audio is None) != (args.ref_text is None):
        print("--ref-audio and --ref-text must be given together", file=sys.stderr)
        return 2

    repo = Path(os.environ.get("CF_BREEZE_REPO", DEFAULT_REPO)).expanduser()
    model_dir = Path(os.environ.get("CF_BREEZE_MODEL_PATH", DEFAULT_MODEL)).expanduser()
    if not (repo / "breeze_infer").is_dir():
        print(f"breeze-tts checkout not found at {repo}", file=sys.stderr)
        return 3
    if not (model_dir / "config.json").is_file():
        print(f"Breeze weights not found at {model_dir}", file=sys.stderr)
        return 3
    sys.path.insert(0, str(repo))

    # The runtime prints diagnostics to stdout; keep our stdout clean for the one JSON line.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    import soundfile as sf
    import torch
    from breeze_infer.runtime import (
        load_runtime,
        resolve_device,
        set_all_seeds,
        update_generation_config_for_breeze,
    )
    from breeze_infer.templates import get_template, prepare_inputs
    from models.fast_streaming import FastBreezeStreamingRuntime, FastStreamingConfig

    t0 = time.perf_counter()
    tokenizer, model, audio_tokenizer = load_runtime(
        model_dir, device=resolve_device(), attn_implementation="eager"
    )
    update_generation_config_for_breeze(model)
    config = FastStreamingConfig(
        max_new_tokens=MAX_NEW_TOKENS,
        max_seq_len=MAX_SEQ_LEN,
        fast_all=True if args.fast_all else None,
        fast_text_encoder=False,
        fast_backbone_prefill=False,
        fast_backbone_decode=False,
        fast_depth_decoder=False,
        fast_codec=False,
        repetition_penalty=REPETITION_PENALTY,
    )
    runtime = FastBreezeStreamingRuntime(model, audio_tokenizer, config, tokenizer=tokenizer)
    t_loaded = time.perf_counter()

    request: dict[str, str] = {
        "id": "cf-breeze",
        "text": text,
        "instruction": args.instruction,
        "speaker": "S0",
    }
    template_name = "tts_instruction"
    if args.ref_audio is not None:
        request["ref_audio_path"] = str(args.ref_audio)
        request["ref_text"] = str(args.ref_text).strip()
        template_name = "ref_edit_tata"

    set_all_seeds(args.seed)
    inputs = prepare_inputs(
        tokenizer,
        audio_tokenizer,
        model,
        [request],
        get_template(template_name),
        guidance_scale=args.cfg_scale,
        guidance_scale_ref=None,
        guidance_scale_ins=None,
    )
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    frames = 0
    t_first: float | None = None
    with sf.SoundFile(
        out.name, mode="w", samplerate=runtime.sample_rate, channels=1, subtype="PCM_16"
    ) as f:
        for chunk in runtime.iter_audio_chunks(inputs, request_id="cf-breeze", seed=args.seed):
            if t_first is None:
                t_first = time.perf_counter()
            f.write(chunk.audio)
            frames += len(chunk.audio)
    t_done = time.perf_counter()

    duration_ms = int(frames * 1000 / runtime.sample_rate)
    sys.stdout = real_stdout
    print(
        json.dumps(
            {
                "wav": out.name,
                "sample_rate": int(runtime.sample_rate),
                "duration_ms": duration_ms,
                "tokens": [],
                "timing_source": "none",
                "model_revision": MODEL_REVISION,
                "instruction": args.instruction,
                "seed": args.seed,
                "cfg_scale": args.cfg_scale,
                "load_s": round(t_loaded - t0, 2),
                "ttfa_ms": round(((t_first or t_done) - t_loaded) * 1000),
                "generate_s": round(t_done - t_loaded, 2),
                "rtf": round((t_done - t_loaded) / max(duration_ms / 1000, 1e-6), 3),
                "peak_vram_mib": (
                    round(torch.cuda.max_memory_allocated() / 2**20)
                    if torch.cuda.is_available()
                    else None
                ),
                "device": resolve_device(),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
