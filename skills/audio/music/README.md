# MiniMax-Music3 skill — non-verbal background music for video

Builds `assets/music/`: 22 fully instrumental underscore tracks for documentary, explainer,
nature, human-interest and tension cuts, plus four loopable calm textures. 44.1 kHz stereo,
24-bit FLAC.

The control plane never imports this code.

**Licence:** the weights are under the **MiniMax-Music3 Community License** — notable terms include
prominent display of "MiniMax-Music3" in commercial products, separate authorization above US$20M
annual revenue, and **clear disclosure of AI generation for publicly distributed outputs**. The
HOT-Step engine is MIT. Operator accepted these for local generation.

## What is on disk
- Weights: `models/music/MiniMax-Music3-GGUF` (repo-local index link → `/mnt/fast/models/minimax-music3`),
  five components: LM 8.59B q8_0, DiT 2.4B q8_0, RVQ depth 0.6B q8_0, cond encoder f16, vocoder f16.
- Runtime: `external/HOT-Step-CPP` (MIT engine) — git-ignored upstream checkout, built below.
- CUDA 12.6 toolkit: `.venvs/cuda-12.6/root` — extracted from NVIDIA's runfile **without root**
  (`./cuda_*.run --tar mxvf`), because the system has a driver but no toolkit and the PyPI
  `nvidia-cuda-nvcc-cu12` wheel ships only `ptxas`, not the `nvcc` driver. Needed to *build* the
  engine; nothing at runtime depends on it beyond the shared libs it links.
- Design rationale and sources: `docs/research/2026-09-07-background-music-library.md`.

## Setup (once)
```bash
cd skills/audio/music && uv sync          # numpy + soundfile only; no torch

# build the engine (CUDA, ~sm_86)
CUDA_ROOT=$PWD/../../../.venvs/cuda-12.6/root
cd ../../../external/HOT-Step-CPP/engine && rm -rf build && mkdir build && cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DGGML_BLAS=OFF -DGGML_CUDA=ON \
  -DCMAKE_CUDA_COMPILER=$CUDA_ROOT/bin/nvcc -DCUDAToolkit_ROOT=$CUDA_ROOT \
  -DCMAKE_CUDA_ARCHITECTURES=86
cmake --build . --config Release -j "$(nproc)" --target ace-server
```

## Run
```bash
# whole library -> assets/music/ (starts the engine itself if it is not already up)
uv run --project skills/audio/music python skills/audio/music/build_library.py

uv run --project skills/audio/music python skills/audio/music/build_library.py --only doc_piano_felt
uv run --project skills/audio/music python skills/audio/music/build_library.py --list
```

The engine is left running between builds on purpose (`--keep-loaded`): the five modules are ~13 GB
and reloading them per track would dominate the render. `--stop-server` shuts down a server this
script started; one that was already up is never touched.

## Things that are not obvious

**These GGUFs do not work with llama.cpp or ComfyUI.** The model card is explicit, and STATUS had
"Music3 has no runtime yet" for that reason. Generation needs the five-module pipeline
(LM → RVQ depth decoder → condition encoder → flow-matching DiT → vocoder) that only the HOT-Step
engine implements. `ace-lm` / `ace-synth` are the **ACE-Step** tools and contain no MM3 code — point
them at these weights and all you get is `unknown architecture`, because the ACE registry classifies
on `general.architecture` while MM3 is discovered separately. MM3 lives in the **server**:
`POST /mm3/synth`, `GET /mm3/job?id=`, `GET /mm3/take?id=`.

**CPU is not an option here.** Measured before the CUDA build: 15 s of audio took ~2¼ minutes in the
AR stage alone and was a fifth of the way through the flow stage at 5 minutes — roughly 20-30x
realtime, which is ~7 hours for this library. Hence the toolkit extraction above.

**`instrumental: true` is a mechanism, not a hint.** The engine substitutes the literal
`[instrumental]` structure tag for the lyrics in the prompt it assembles and skips LRC alignment
capture. Every caption *also* says the piece is instrumental, because the caption is what the model
reads.

**Captions are MiniMax's Structured Caption format**, three sections in this exact order:
`Global Metadata:` (genre, tempo as a range or a word, emotional progression, production profile) →
`Vocal Details:` (for an instrumental, say so and name the texture carrying the lead) →
`Arrangement:` (a section-by-section timeline). The spec warns against inventing a precise key or
BPM "when a broader description is sufficient", so the library gives tempo qualitatively.

**Only textures loop.** Pads and drones have no phrase structure, so the tail-over-head wrap from
the sfx skill applies cleanly. Music with phrases does not loop by wrapping, however good the
crossfade is, and the library does not pretend otherwise.

**QC is about fitness for purpose, not fidelity.** `presence_band_ratio` (1-4 kHz share) says
whether a bed will fight a narrator; `loudness_range_lu` says whether a track that was supposed to
move actually moved, and whether one that was supposed to stay put actually did.

## Files
| file | role |
|---|---|
| `library.json` | the recipe: 22 Structured Captions, seeds, durations, LRA bands |
| `mm3.py` | engine lifecycle, HTTP client, music QC (DSP imported from `skills/audio/sfx/sfx.py`) |
| `build_library.py` | render → level → measure → FLAC + manifest + index |

Env overrides: `CF_MM3_ENGINE`, `CF_MM3_MODELS`.
