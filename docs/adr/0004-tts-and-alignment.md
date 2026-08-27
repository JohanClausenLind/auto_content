# ADR 0004 — Narration (TTS) and word-level alignment

- Status: accepted (2026-08-27)
- Research: docs/research/tts-alignment-and-tier1-platforms.md

## Context
Temporal deliverables need measured word/segment timestamps before scene durations are frozen
(principle 2.2). Local models must be permissively licensed and must pass a quality floor; a
premium adapter and a deterministic mock complete the contract.

## Decision
- `voice.synthesize` is a skill contract with three interchangeable executors:
  1. **Mock** (deterministic sine/silence audio with synthetic timings) — core CI.
  2. **Local: Kokoro-82M** (Apache-2.0 weights and code; `kokoro` 0.9.4 on PyPI; Python 3.10–3.12).
     It natively emits token-level `start_ts`/`end_ts` for English, which becomes the primary
     timing source for local narration. Its phonemizer path pulls in espeak-ng (GPL-3.0+) as a
     separate process/library dependency; it is run in the skill's isolated environment, never
     linked into the control plane, and recorded in THIRD_PARTY_NOTICES.
     **Chatterbox** (MIT, 0.1.7) is documented as an evaluated alternative: higher expressiveness,
     no timestamps, hard-pinned `torch==2.6.0` (conflicts with WhisperX), watermarked output.
  3. **Premium: ElevenLabs** `with-timestamps` endpoint (character-level alignment). Azure Speech
     `WordBoundary` events are the documented second premium option. OpenAI TTS returns no timing.
- **Timestamp precedence**: provider/model timing → forced alignment → ASR fallback with sanity
  checks. Forced alignment uses **WhisperX 3.8.6 (BSD-2)** with the MIT `WAV2VEC2_ASR_BASE_960H`
  English aligner (no HF token needed without diarization). **Montreal Forced Aligner 3.4.2 (MIT)**
  is documented but not adopted for the default path because it is effectively conda-only and
  its English model/dictionary are CC BY 4.0. ASR fallback: **faster-whisper 1.2.1 (MIT)** with
  `word_timestamps=True` (CTranslate2 4.8.1 requires CUDA 12 + cuDNN 9 for GPU).
- Because Chatterbox (torch 2.6) and WhisperX (torch ~2.8) conflict, **every audio skill runs in
  its own uv-managed environment** declared by its SkillManifest; the control plane never imports
  torch.
- Audio observations are integer milliseconds. Alignment validation (monotonicity, overlaps,
  missing words, gaps, duration mismatch) gates captions and scene compilation. Voice cloning is
  disabled by default and requires a ConsentRecord.
- House loudness target: −14 LUFS integrated, −1 dBTP (configurable, documented as a choice).

## Consequences
- Piper is not used (successor `piper1-gpl` is GPL-3.0-or-later).
- Quality floors for Kokoro vs premium are measured per evaluation pack (phase 5), never assumed.
