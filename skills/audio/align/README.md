# Forced aligner skill (CPU)

Isolated environment for the explainer's alignment stage 2: **facebook/wav2vec2-base-960h** CTC
emissions through `torchaudio.functional.forced_align`. The control plane never imports this
code; `content_factory.explainer.narration.align_take` runs `run.py` by subprocess on the words
that stage 1 (ASR diff, `verify_take`) has already accepted.

**Licences:** weights `facebook/wav2vec2-base-960h` Apache-2.0; torch BSD-3-Clause; torchaudio
BSD-2-Clause; transformers Apache-2.0; safetensors Apache-2.0; soundfile BSD-3-Clause; numpy
BSD-3-Clause. The CC-BY-NC `MMS_FA` pipeline is not used, so nothing here limits commercial output.

## What is on disk
- Weights: `/mnt/fast/models/wav2vec2-base-960h` (HF revision `22aad52`, 371 MB;
  `content_factory.models.weights` key `wav2vec2-base-960h`).
- torch and torchaudio come from `https://download.pytorch.org/whl/cpu` (`[tool.uv.sources]`),
  so the environment has no CUDA libraries and never touches the GPU.

## Setup (once)
```bash
uv sync --project skills/audio/align
```

## Run
```bash
uv run --project skills/audio/align python skills/audio/align/run.py \
  --audio take.wav --words '["It","still","takes","sixty"]' \
  --model /mnt/fast/models/wav2vec2-base-960h --out align.json
```
`--words` is the spoken transcript with numbers already written as words ("80" → "eighty"); the
vocabulary is uppercase letters plus the apostrophe, so digits and symbols never match. A word
with no vocabulary character is listed in `unmatched` and left out of the target sequence.

Output JSON: `words: [{index, start_ms, end_ms, score}]` (20 ms frames, score is the mean token
probability), `unmatched: [index...]`, `aligner: "torchaudio.forced_align"`, `aligner_version`,
`model_id`. Exit 2 with a message on stderr when the audio is too short for the transcript.

Audio of any rate or channel count is resampled to 16 kHz mono; audio over 20 s is processed in
20 s windows with 1 s overlap, each window's frames trusted up to the middle of the overlap.
`--threads` (default 8) caps the CPU threads because the box is shared.
