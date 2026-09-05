# Qwen3-TTS 12Hz 1.7B skill

Isolated environment for **Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice** and
**Qwen/Qwen3-TTS-12Hz-1.7B-Base**. The control plane never imports this code; it would talk to
`run.py` the way `KokoroTTS` talks to `skills/audio/kokoro/run.py`.

**Licence:** Apache-2.0 for both the inference code (`qwen-tts` on PyPI) and the weights.
No gate, no acceptance step.

## The two models are not interchangeable

| | CustomVoice | Base |
| --- | --- | --- |
| Built-in voices | **9** (`config.talker_config.spk_id`) | **none** — `spk_id == {}` |
| How you pick a voice | by name (`speaker="Ryan"`) | clone it from ~3 s of reference audio + its transcript |
| Instruction control | yes (`instruct="Very happy."`) | no |
| Languages | Chinese, English, Japanese, Korean, German, French, Russian, Portuguese, Spanish, Italian | same |

This is the point most easily got wrong: **the voice list lives in CustomVoice.** Base is the
clone/fine-tune base and declares no speakers at all — verified against the downloaded weights,
not just the model card.

The nine CustomVoice timbres (native language in brackets):

| Speaker | Voice description | Native |
| --- | --- | --- |
| Vivian | Bright, slightly edgy young female voice | Chinese |
| Serena | Warm, gentle young female voice | Chinese |
| Uncle_Fu | Seasoned male voice, low mellow timbre | Chinese |
| Dylan | Youthful Beijing male, clear natural timbre | Chinese (Beijing) |
| Eric | Lively Chengdu male, slightly husky brightness | Chinese (Sichuan) |
| Ryan | Dynamic male voice with strong rhythmic drive | English |
| Aiden | Sunny American male, clear midrange | English |
| Ono_Anna | Playful Japanese female, light nimble timbre | Japanese |
| Sohee | Warm Korean female, rich emotion | Korean |

Each speaker can speak any supported language; the card recommends its native language for the
best quality.

## What is on disk

Weights in the store, indexed by category from the repo:

- `models/speech/Qwen3-TTS-12Hz-1.7B-CustomVoice` → `/mnt/fast/models/qwen3-tts-1.7b-customvoice`
- `models/speech/Qwen3-TTS-12Hz-1.7B-Base` → `/mnt/fast/models/qwen3-tts-1.7b-base`

4.3 GB each (a 3.9 GB bf16 transformer plus the 682 MB 12 Hz speech tokenizer, bundled in
`speech_tokenizer/` — the separate `Qwen3-TTS-Tokenizer-12Hz` repo is not needed).
HF revisions: Base `fd4b254`, CustomVoice at download time 2026-09-07.

## Setup (once)

```bash
uv sync --project skills/audio/qwen3tts    # torch 2.9.1+cu128, qwen-tts 0.1.1, transformers 4.57.3
```

`flash-attn` is not installed (no nvcc on this host), so everything defaults to
`attn_implementation="sdpa"`; pass `--attn flash_attention_2` if a prebuilt wheel is ever added.
The `sox: not found` warning at import comes from the `sox` python binding and is harmless — the
paths used here go through torchaudio/soundfile.

VRAM: about 5 GB at bf16, so unlike HiDream (~17 GB) and LTX-2.5 (~20 GB) this model does **not**
need the exclusive-GPU dance in `services/local.py`.

## Run

Built-in voice:

```bash
echo "The last train leaves at eleven." | \
  uv run --project skills/audio/qwen3tts python skills/audio/qwen3tts/run.py \
  --speaker Ryan --language English
```

With style control:

```bash
echo "You did what?" | uv run --project skills/audio/qwen3tts python skills/audio/qwen3tts/run.py \
  --speaker Vivian --language English --instruct "Incredulous, with a hint of panic."
```

Voice clone on Base:

```bash
echo "And I took it." | uv run --project skills/audio/qwen3tts python skills/audio/qwen3tts/run.py \
  --ref-audio output/qwen3-tts-samples/Sohee.native.wav \
  --ref-text "안녕하세요. 오늘 하루도 잘 보내셨나요?" --language English
```

List what the weights actually declare:

```bash
uv run --project skills/audio/qwen3tts python skills/audio/qwen3tts/run.py --list </dev/null
```

Prints one JSON line: wav path, sample rate, duration, mode, model revision.

## Sample sheet

```bash
uv run --project skills/audio/qwen3tts python skills/audio/qwen3tts/samples.py
```

Writes `output/qwen3-tts-samples/` — per speaker a native-language clip, the same shared English
line for like-for-like comparison, and a Base clone of that speaker re-speaking the English line —
plus `manifest.json` with the text, durations, timings and sha256 of every clip.

## How it drives a narrated timeline (wired 2026-09-07)

`tokens` is always empty: **Qwen3-TTS returns no word timestamps.** Captions and the timeline
compiler need them, so `Qwen3TTS` in `python/content_factory/audio/tts.py` measures them after the
fact — ADR-0004's precedence, provider → forced alignment → ASR, landing on the middle one:

1. this script generates the beat;
2. `faster_whisper_words()` transcribes it with word timestamps (CPU int8, no VRAM);
3. `snap_to_script()` puts the **locked script's** words on the measured spans, so a caption never
   shows the transcript's spelling — the same function that times a human recording;
4. the transcript is compared with the script, and a beat the model garbled or truncated fails by
   name (`narration.script_similarity_min`, default 0.80) instead of shipping with drifting captions.

`even_split` is the offline stand-in and records itself as `estimated`, never as measured. Kokoro
(`skills/audio/kokoro`) stays as the fallback: 82M parameters against 1.7B, and the only executor
here that times its own tokens, so it needs no aligner and runs on CPU in seconds.

Turn it on with `CF__NARRATION__TTS=qwen3tts` (see `.env.example` for the timbre, the delivery
note, the clone options and the aligner).

**Measured 2026-09-07** on this host: one 11-word beat, `--device cpu`, generated 5520 ms of
24 kHz audio; faster-whisper `base.en` returned 11 spans for 11 script words — a 1:1 snap with real
measured boundaries — at transcript similarity 0.909 (only "outproduced" vs "out-produced"
differed). CPU generation took over ten minutes for that one beat, so **`qwen_device=cuda:0` is the
default for a reason**; the GPU path was not exercised because the card was held all session.
