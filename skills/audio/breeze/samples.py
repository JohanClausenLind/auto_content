"""Generate a Breeze-TTS-2 voice sheet: one clip per designed voice, model loaded once.

    uv run --project skills/audio/breeze python skills/audio/breeze/samples.py

Breeze has **no voice list**. Unlike Qwen3-TTS CustomVoice (nine named timbres in
`config.talker_config.spk_id`), a Breeze voice *is* its instruction string — a natural-language
description of who is speaking. So "the voices" here are the descriptors below, which are the ones
validated in the 2026-09-05 evaluation (`output/eval/tts-2026-09-05/`), shaped after Breeze's own
TTS-Voice-Design-Benchmark: accent, age, pitch, timbre, energy, style.

Every voice speaks ONE shared line — the same line the Qwen3-TTS sample sheet uses — so the two
models can be compared clip for clip.

The 2026-09-05 batch script paid a full model load per clip; this loads the runtime once and
reuses it, which is the whole reason this is a script rather than a shell loop.

Not imported by the control plane. Weights are research/non-commercial (operator-accepted
2026-09-05 for local testing); these clips are local evaluation material, nothing else.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Constants only — run.py's heavy imports live inside its main(), so this stays cheap and the two
# scripts cannot drift apart on sequence length, penalty or the recorded model revision.
from run import (
    DEFAULT_MODEL,
    DEFAULT_REPO,
    MAX_NEW_TOKENS,
    MAX_SEQ_LEN,
    MODEL_REVISION,
    REPETITION_PENALTY,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = REPO_ROOT / "output" / "breeze-tts2-samples"

# The Qwen3-TTS sheet's shared line, so `Ryan.shared_en.wav` and `trailer_m.wav` are the same words.
SHARED_EN = "The last train leaves at eleven, and I still have not decided whether to wait."

# (name, instruction) — carried over verbatim from the 2026-09-05 evaluation.
VOICES: tuple[tuple[str, str], ...] = (
    # Round 2b: compact benchmark-style descriptors (accent / age / pitch / timbre / energy).
    (
        "swedish_m",
        "Masculine, young adult, Swedish-accented English. Calm, conversational, informal. "
        "Clear, warm, medium pace.",
    ),
    (
        "rp_f",
        "Female, mid-30s, British RP accent. Smooth, poised, formal documentary delivery, "
        "moderate pace.",
    ),
    (
        "australian_m",
        "Masculine, 40s, Australian accent. Relaxed, friendly, dry humour, medium pace, resonant.",
    ),
    (
        "scholar_m",
        "Masculine, older adult, North American. Scholarly, measured, resonant, deliberate slow "
        "pace, formal.",
    ),
    (
        "synthetic_n",
        "Neutral gender, synthetic robotic assistant voice, even mid pitch, precise articulation, "
        "slightly metallic, calm.",
    ),
    (
        "heroic_f",
        "Feminine, young adult, North American. Heroic, high-energy, bright, resonant, dynamic "
        "pitch, dramatic.",
    ),
    # Round 1: prose descriptions.
    (
        "british_f",
        "A crisp British-accented woman in her forties, precise diction, authoritative "
        "news-anchor delivery, medium pace.",
    ),
    (
        "corporate_f",
        "A calm, neutral, professional female corporate narrator, medium pace, minimal emotion.",
    ),
    (
        "events_f",
        "A warm, thoughtful young woman with a clear voice and a calm, reflective delivery.",
    ),
    (
        "husky_f",
        "A husky, low-pitched woman with a relaxed, confident, slightly raspy delivery.",
    ),
    (
        "latenight_m",
        "A soft-spoken young man with a gentle, intimate late-night radio delivery, low energy, "
        "slow pace.",
    ),
    (
        "presenter_f",
        "A bright, energetic young woman, upbeat social-media presenter, fast pace, smiling and "
        "enthusiastic delivery.",
    ),
    (
        "storyteller_m",
        "An elderly man with a gravelly, unhurried storyteller voice, warm, slightly amused, "
        "slow pace.",
    ),
    (
        "teacher_f",
        "A cheerful middle-aged woman with a friendly, patient teacher's tone, clear and "
        "encouraging.",
    ),
    (
        "trailer_m",
        "A deep, resonant movie-trailer announcer, dramatic pauses, intense and cinematic.",
    ),
)

# Cross-model clone demo: hand Breeze a Qwen3-TTS voice and ask it to say the shared line.
CLONE_REF = REPO_ROOT / "output" / "qwen3-tts-samples" / "Ryan.native.wav"
CLONE_REF_TEXT = "Alright, let's take it from the top — and this time, with feeling."


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--text", default=SHARED_EN)
    ap.add_argument("--cfg-scale", type=float, default=4.0, help="4.0 held up in the 09-05 round")
    ap.add_argument("--clone-cfg-scale", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--fast-all", action="store_true", help="CUDA-graph fast path (~14 GiB)")
    ap.add_argument("--skip-clone", action="store_true")
    args = ap.parse_args()

    repo = Path(os.environ.get("CF_BREEZE_REPO", DEFAULT_REPO)).expanduser()
    model_dir = Path(os.environ.get("CF_BREEZE_MODEL_PATH", DEFAULT_MODEL)).expanduser()
    if not (repo / "breeze_infer").is_dir():
        print(f"breeze-tts checkout not found at {repo}", file=sys.stderr)
        return 3
    if not (model_dir / "config.json").is_file():
        print(f"Breeze weights not found at {model_dir}", file=sys.stderr)
        return 3
    sys.path.insert(0, str(repo))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # The runtime chatters on stdout; keep progress on the real one and let its noise go to stderr.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr

    def log(msg: str) -> None:
        print(msg, file=real_stdout, flush=True)

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

    log(f"loading Breeze-TTS-2 from {model_dir} ...")
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
    load_s = round(time.perf_counter() - t0, 2)
    log(f"  loaded in {load_s}s on {resolve_device()}")

    def synth(name: str, instruction: str, cfg: float, ref: Path | None = None) -> dict:
        request: dict[str, str] = {
            "id": "cf-breeze",
            "text": args.text,
            "instruction": instruction,
            "speaker": "S0",
        }
        template_name = "tts_instruction"
        if ref is not None:
            request["ref_audio_path"] = str(ref)
            request["ref_text"] = CLONE_REF_TEXT
            template_name = "ref_edit_tata"
        set_all_seeds(args.seed)
        inputs = prepare_inputs(
            tokenizer,
            audio_tokenizer,
            model,
            [request],
            get_template(template_name),
            guidance_scale=cfg,
            guidance_scale_ref=None,
            guidance_scale_ins=None,
        )
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        path = out_dir / f"{name}.wav"
        frames = 0
        t_start = time.perf_counter()
        t_first: float | None = None
        with sf.SoundFile(
            path, mode="w", samplerate=runtime.sample_rate, channels=1, subtype="PCM_16"
        ) as fh:
            for chunk in runtime.iter_audio_chunks(inputs, request_id="cf-breeze", seed=args.seed):
                if t_first is None:
                    t_first = time.perf_counter()
                fh.write(chunk.audio)
                frames += len(chunk.audio)
        t_done = time.perf_counter()
        duration_s = round(frames / runtime.sample_rate, 3)
        data = path.read_bytes()
        rec = {
            "name": name,
            "instruction": instruction,
            "text": args.text,
            "path": str(path.relative_to(REPO_ROOT)),
            "sample_rate": int(runtime.sample_rate),
            "duration_s": duration_s,
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "cfg_scale": cfg,
            "seed": args.seed,
            "ttfa_ms": round(((t_first or t_done) - t_start) * 1000),
            "generate_s": round(t_done - t_start, 2),
            "rtf": round((t_done - t_start) / max(duration_s, 1e-6), 3),
            "peak_vram_mib": (
                round(torch.cuda.max_memory_allocated() / 2**20)
                if torch.cuda.is_available()
                else None
            ),
        }
        if ref is not None:
            rec["cloned_from"] = str(ref.relative_to(REPO_ROOT))
            rec["ref_text"] = CLONE_REF_TEXT
        log(
            f"  {name:<15} {duration_s:>5.2f}s audio in {rec['generate_s']:>5.2f}s "
            f"(rtf {rec['rtf']}, ttfa {rec['ttfa_ms']}ms)"
        )
        return rec

    clips = [synth(name, instruction, args.cfg_scale) for name, instruction in VOICES]

    if not args.skip_clone and CLONE_REF.is_file():
        log("\ncross-model clone (Qwen3-TTS Ryan -> Breeze):")
        clips.append(
            synth(
                "clone_qwen_ryan", "Speak clearly and naturally.", args.clone_cfg_scale, CLONE_REF
            )
        )
    elif not args.skip_clone:
        log(f"\nskipping clone: {CLONE_REF} not found")

    manifest = {
        "model_revision": MODEL_REVISION,
        "shared_line": args.text,
        "cfg_scale": args.cfg_scale,
        "seed": args.seed,
        "load_s": load_s,
        "note": "Breeze has no speaker list: a voice is its instruction string.",
        "clips": clips,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    total = round(sum(c["duration_s"] for c in clips), 1)
    log(f"\n{len(clips)} clips ({total}s audio) + manifest.json in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
