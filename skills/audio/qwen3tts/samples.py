"""Generate one sample per built-in Qwen3-TTS voice, then the same line cloned by the Base model.

    uv run --project skills/audio/qwen3tts python skills/audio/qwen3tts/samples.py

Two models, two different things — this is the distinction the model card makes and it is worth
stating plainly, because only one of them has voices at all:

* **CustomVoice** carries the nine built-in timbres (``config.talker_config.spk_id``). Each one is
  addressed by name, optionally steered by a natural-language ``instruct`` string.
* **Base** has ``spk_id == {}``: no built-in voices whatsoever. It clones a voice from three
  seconds of reference audio plus that audio's transcript. So "every voice, through Base" means
  cloning each CustomVoice timbre and re-speaking the shared line with it.

For every speaker this writes:
  1. ``<speaker>.native.wav``   — a line in the speaker's own language (the card's quality advice)
  2. ``<speaker>.shared_en.wav`` — one shared English line, so the nine are directly comparable
  3. ``<speaker>.clone_en.wav``  — Base cloning (1) and re-speaking the shared English line

plus ``manifest.json`` with durations, digests, timings and the exact text used.
Not imported by the control plane.
"""

# ruff: noqa: RUF001 — the sample lines are real CJK text; fullwidth punctuation is correct there.
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CUSTOM_VOICE = REPO_ROOT / "models" / "speech" / "Qwen3-TTS-12Hz-1.7B-CustomVoice"
BASE = REPO_ROOT / "models" / "speech" / "Qwen3-TTS-12Hz-1.7B-Base"
OUT_DIR = REPO_ROOT / "output" / "qwen3-tts-samples"

# One shared English line for every speaker, so the nine timbres can be compared like for like.
# (It is a line from this repo's own `last_train` story fixture.)
SHARED_EN = "The last train leaves at eleven, and I still have not decided whether to wait."

# (speaker, language, native line) — speaker names and descriptions from the model card; the
# language string is validated against model.get_supported_languages() at run time.
VOICES: tuple[tuple[str, str, str], ...] = (
    ("Vivian", "Chinese", "其实我真的有发现，我是一个特别善于观察别人情绪的人。"),
    ("Serena", "Chinese", "今天的风有点凉，出门记得多穿一件外套。"),
    ("Uncle_Fu", "Chinese", "这件事啊，急不得，得慢慢来，一步一步地办。"),
    ("Dylan", "Chinese", "您猜怎么着，这事儿还真让我给办成了。"),
    ("Eric", "Chinese", "巴适得板，今天这顿饭安逸得很。"),
    ("Ryan", "English", "Alright, let's take it from the top — and this time, with feeling."),
    (
        "Aiden",
        "English",
        "Morning! Coffee's on, and the forecast says it's going to be a good one.",
    ),
    ("Ono_Anna", "Japanese", "おはようございます。今日はいい天気ですね、お散歩でもしませんか。"),
    ("Sohee", "Korean", "안녕하세요. 오늘 하루도 잘 보내셨나요?"),
)

DESCRIPTIONS = {
    "Vivian": "Bright, slightly edgy young female voice. (Chinese)",
    "Serena": "Warm, gentle young female voice. (Chinese)",
    "Uncle_Fu": "Seasoned male voice with a low, mellow timbre. (Chinese)",
    "Dylan": "Youthful Beijing male voice with a clear, natural timbre. (Chinese, Beijing dialect)",
    "Eric": "Lively Chengdu male voice with a slightly husky brightness. "
    "(Chinese, Sichuan dialect)",
    "Ryan": "Dynamic male voice with strong rhythmic drive. (English)",
    "Aiden": "Sunny American male voice with a clear midrange. (English)",
    "Ono_Anna": "Playful Japanese female voice with a light, nimble timbre. (Japanese)",
    "Sohee": "Warm Korean female voice with rich emotion. (Korean)",
}


def _write(path: Path, wav, sr: int) -> dict:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, wav, sr)
    data = path.read_bytes()
    return {
        "path": str(path.relative_to(REPO_ROOT)),
        "sample_rate": sr,
        "duration_s": round(len(wav) / sr, 3),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT_DIR))
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument(
        "--attn",
        default="sdpa",
        choices=["sdpa", "flash_attention_2", "eager"],
        help="flash_attention_2 needs a prebuilt flash-attn wheel (no nvcc on this host); "
        "sdpa is the portable default and costs a little more VRAM",
    )
    ap.add_argument("--skip-clone", action="store_true", help="CustomVoice samples only")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    import torch
    from qwen_tts import Qwen3TTSModel

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(args.seed)

    manifest: dict = {
        "shared_english_line": SHARED_EN,
        "seed": args.seed,
        "attn_implementation": args.attn,
        "models": {
            "custom_voice": str(CUSTOM_VOICE.resolve()),
            "base": str(BASE.resolve()),
        },
        "voices": [],
    }

    # --- CustomVoice: the nine built-in timbres ----------------------------------------------
    print(f"loading CustomVoice from {CUSTOM_VOICE} ...", flush=True)
    t0 = time.monotonic()
    cv = Qwen3TTSModel.from_pretrained(
        str(CUSTOM_VOICE),
        device_map=args.device,
        dtype=torch.bfloat16,
        attn_implementation=args.attn,
    )
    load_s = round(time.monotonic() - t0, 1)
    speakers = cv.get_supported_speakers() or []
    languages = cv.get_supported_languages() or []
    print(f"  loaded in {load_s}s · {len(speakers)} speakers · {len(languages)} languages")
    print(f"  speakers: {speakers}")
    manifest["custom_voice_supported_speakers"] = speakers
    manifest["custom_voice_supported_languages"] = languages
    manifest["custom_voice_load_s"] = load_s

    # Refuse to guess: a name the weights do not know would otherwise be a confusing runtime error.
    known = {s.lower() for s in speakers}
    unknown = [name for name, _, _ in VOICES if name.lower() not in known]
    if unknown:
        raise SystemExit(f"speakers not present in these weights: {unknown}; model has {speakers}")

    refs: dict[str, tuple] = {}
    for speaker, language, native_text in VOICES:
        entry: dict = {
            "speaker": speaker,
            "description": DESCRIPTIONS[speaker],
            "native_language": language,
            "native_text": native_text,
            "clips": {},
        }
        for label, text, lang in (
            ("native", native_text, language),
            ("shared_en", SHARED_EN, "English"),
        ):
            t = time.monotonic()
            wavs, sr = cv.generate_custom_voice(text=text, language=lang, speaker=speaker)
            took = round(time.monotonic() - t, 2)
            info = _write(out_dir / f"{speaker}.{label}.wav", wavs[0], sr)
            info.update({"text": text, "language": lang, "generate_s": took})
            entry["clips"][label] = info
            print(f"  {speaker:<9} {label:<10} {info['duration_s']:>5.2f}s audio in {took:>5.2f}s")
            if label == "native":
                refs[speaker] = (wavs[0], sr)
        manifest["voices"].append(entry)

    # --- Base: no built-in voices; clone each timbre from its own native clip -----------------
    if not args.skip_clone:
        del cv
        torch.cuda.empty_cache()
        print(f"\nloading Base from {BASE} ...", flush=True)
        t0 = time.monotonic()
        base = Qwen3TTSModel.from_pretrained(
            str(BASE),
            device_map=args.device,
            dtype=torch.bfloat16,
            attn_implementation=args.attn,
        )
        load_s = round(time.monotonic() - t0, 1)
        base_speakers = base.get_supported_speakers()
        print(f"  loaded in {load_s}s · built-in speakers: {base_speakers or 'none (clone-only)'}")
        manifest["base_supported_speakers"] = base_speakers
        manifest["base_load_s"] = load_s

        by_name = {e["speaker"]: e for e in manifest["voices"]}
        for speaker, _language, native_text in VOICES:
            prompt = base.create_voice_clone_prompt(
                ref_audio=refs[speaker], ref_text=native_text, x_vector_only_mode=False
            )
            t = time.monotonic()
            wavs, sr = base.generate_voice_clone(
                text=SHARED_EN, language="English", voice_clone_prompt=prompt
            )
            took = round(time.monotonic() - t, 2)
            info = _write(out_dir / f"{speaker}.clone_en.wav", wavs[0], sr)
            info.update(
                {
                    "text": SHARED_EN,
                    "language": "English",
                    "generate_s": took,
                    "cloned_from": f"{speaker}.native.wav",
                    "ref_text": native_text,
                }
            )
            by_name[speaker]["clips"]["clone_en"] = info
            print(f"  {speaker:<9} clone_en   {info['duration_s']:>5.2f}s audio in {took:>5.2f}s")

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    clips = sum(len(v["clips"]) for v in manifest["voices"])
    print(f"\n{clips} clips + manifest.json in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
