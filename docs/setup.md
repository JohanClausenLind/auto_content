# Setup

Tested on Ubuntu 24.04 with Docker 29, Node 24, Python 3.12, FFmpeg 6.1, RTX 3090 (optional).

## One command
```bash
./setup.sh
```
It is idempotent. It checks prerequisites with plain-language remediation, creates `.env` from
`.env.example` (and generates `VAULT_MASTER_KEY` — back it up), installs Python (`uv sync
--frozen`) and Node (`pnpm install --frozen-lockfile`) dependencies, starts Postgres 18 and the
Temporal dev server on loopback, exports contracts, applies migrations to the dev and test
databases, bootstraps the owner account (`operator`, password printed **once**) and two demo
workspaces, and downloads Chrome Headless Shell for the renderer (~150 MB, once).

Optional services: `CF_WITH_SEARCH=1` (SearXNG on 127.0.0.1:8083), `CF_WITH_S3=1` (SeaweedFS S3 on
127.0.0.1:8333), `CF_WITH_NTFY=1` (ntfy on 127.0.0.1:8092). Ports are chosen to avoid 8080/8082/8091
which other local stacks commonly use; change them in `.env`.

## Run
```bash
just dev          # API on 127.0.0.1:8000, web on 127.0.0.1:3000 (proxying /v1)
just doctor       # health check
just test         # offline suites
just test-integration
```
Sign in with `operator` and the printed password, or `content-factory login`.

## Stop
```bash
just stop                     # the run, the worker, API/web, HiDream/ComfyUI/Ollama, compose
just stop --dry-run           # what it would stop, and what it would leave alone
just stop --no-docker         # everything but the compose stack
just stop-run                 # only the production run; --after-stage finishes the stage first
just stop-run --run <run id>  # one run out of several
```
`just stop` only ever signals processes running from this checkout, and never the MCP server (an
assistant is connected through its stdin). A local run stopped between stages keeps everything it
finished, and `content-factory make <lane> --from <stage>` carries on from there. A durable run is
closed through Temporal: it records CANCELLED, resolves its ActionItems, and its cached stages are
reused by the next run.

## ComfyUI (optional, later phases)
Not installed automatically (multi-gigabyte). Either point `comfyui.workspace` at an existing
comfy-cli workspace (this machine: `~/git/ComfyUI`) or run
`comfy --workspace=./.comfy install --nvidia` then `comfy launch --background`.

## Local AI stack (optional, GPU)
Everything the video/audio skills depend on lives inside this repository, laid out ComfyUI-style
in flat, purpose-named directories. All of them are git-ignored:

| Directory | Holds |
| --- | --- |
| `external/` | upstream code checkouts (LTX-2, MMAudio, ACE-Step-1.5, whisperX, breeze-tts, hidream-o1-code, …), each with its own `.venv` where needed |
| `models/` | the weight index, sorted by category like ComfyUI's `models/`: `video_generation/`, `image_generation/`, `video_editing/`, `characters/`, `restoration/`, `frame_interpolation/`, `sound_effects/`, `music/`, `speech/`, `speech_restoration/`, `text/` — every entry is a symlink into the store, `/mnt/fast/models/<short-name>` on this machine |
| `datasets/` | the **data** index, sorted by category the same way: `human_interaction/`, `staging/`, `audio/`, `training/` — every entry a symlink into the host's data. Built by `just datasets link` from `content_factory.libraries`; `docs/datasets.md` says what each library gives, what it cannot, and what reads it |
| `output/` | generated media: image-sequence workdirs (`image_sequences.output_root`), `output/eval/` evaluation runs |
| `.venvs/` | tool venvs: `download` (the `hf` CLI the download scripts use) and `scenedetect` |
| `sandbox/` | throwaway prototypes (two untouched `create-video` Remotion scaffolds) |
| `scripts/host/` | host provisioning: `setup-model-storage.sh` (created `/mnt/fast`) and the model-move logs |

Populate with `./scripts/download_ai_video_stack.sh` then `./scripts/download_video_stack_extras.sh`;
verify with `uv run content-factory video-stack`. For the data half, `just datasets link` then
`just datasets check` — which reports what is absent (not an error: every lane runs without any of
it) and what nothing reads. `AI_VIDEO_ROOT` (a directory holding `external/`)
and `AI_VIDEO_MODELS` override the defaults. History: the checkouts lived in `~/ai-video-stack/repos`
and the index in `~/models` until 2026-09-05.

### The reference library (optional)

`/mnt/fast/reference` holds 19 GB of real two-person interaction across seven sources: CMU mocap,
Harmony4D's 22-camera hugging capture, SBU Kinect, UT-Interaction, TV Human Interactions, MotionHub
and hand-picked stock. Like the model weights it lives outside the repo, and like them an absent
copy is a normal state rather than an error: `find_reference` selects nothing, says so, and every
lane still runs.

```
just reference                                   # build the index (about 20 s, 15789 clips)
just reference-search "she rests her head on his shoulder"
```

Two things to know before building on it, both established by measurement on 2026-09-12 and
written up in `docs/datasets.md`. **Only 441 of the 15789 clips carry usable pixels** — this is a
pose library, good for staging and not for cutting footage. And **only 58 clips are retargeted**,
so a beat can match well and still stage nothing: `find_reference` now reports `stageable_beats`
and a `reason` separately from `selected`, because before that both outcomes looked identical to
an absent library.

The search answers in clip ids and also reports `absent`: words it understood and has nothing for.
That list is a gap in the material, not a failure of the search. `INVENTORY.json` and `README.md`
beside the data say what each source is and is not good for, and `docs/reference-library.md` carries
the design.

`picture-story` uses it: `find_reference` runs before `plan_shots`, and `plan_shots`'s `reference`
planner stages each beat from the mocap clip retrieval chose, with two characters on the one
captured take so the contact on screen is the contact that was recorded.

### Running a workflow live on this machine

`just make <workflow>` (see `content-factory workflows list` for the catalogue)
executes a template's stages in order with the real executors and no Temporal, writing to
`output/local-runs/<workflow>/`. Backends come from `.env` (see `.env.example`: `CF__CONTROLS__COMPILER=blender`,
`CF__IMAGE_SEQUENCES__BACKEND=hidream`, `CF__VIDEO__BACKEND=comfyui`, `CF__NARRATION__TTS=qwen3tts`); the
defaults stay mocks. Stages that need a GPU server start it themselves (`services/local.py`): the HiDream
skill server for anchors, ComfyUI (with the LTX flags) for video, one at a time on a 24 GB card, with the
packages' weights linked from the store into ComfyUI's model folders first. `--from <stage>` resumes a run;
every stage caches its own outputs. `--story fixtures/story/<name>.json` picks a hand-written
StoryPlan; a `<name>.datasets.json` (DatasetTable list) and `<name>.sources.json` (SourceCard
list) next to it supply the numbers its charts and source cards show. `--shots
fixtures/shots/<name>.json` stages the generative beats by hand (prompts, camera, size). Captions
are burned into the picture by default (`compose.burn_captions`) in Inter Bold from `assets/fonts/inter`,
inside the platform safe zone; a `<name>.brand.json` sidecar picks paper/ink/accent and `font_family`
(`Inter`, or `Sora` to set headlines and figures in the display face). Burned captions highlight the
word being spoken in the accent colour (`compose.caption_highlight`), and a story's optional `hook_text`
is shown as a headline over the opening seconds (`compose.hook_seconds`). `exports/compose.json` reports
pacing (mean and longest scene, changes per 10 s).
`just setup-postchain` builds the six upstream environments the post chain calls into (Cutie,
ProPainter, GIMM-VFI, Practical-RIFE, SeedVR2, MMAudio), each with its own Python and venv. It
needs the checkouts to exist first — see [docs/gpu-hosts.md](gpu-hosts.md) for the pinned list.

### Narration voice

`synthesize_narration` speaks the locked script with one of three executors
(`narration.tts`, `.env`):

| | Timings | Notes |
| --- | --- | --- |
| `mock` (default) | synthetic | deterministic tone bursts; the offline demo and the core suite |
| `qwen3tts` | **forced alignment** | `skills/audio/qwen3tts`, Qwen3-TTS 12Hz 1.7B, Apache-2.0. Nine timbres, ten languages, a natural-language delivery note, and voice cloning on the Base weights. ~5 GB VRAM |
| `kokoro` | from the model | `skills/audio/kokoro`, 82M params, English-leaning. The fallback: no aligner needed, CPU seconds per beat |

Qwen3-TTS became the narration voice on 2026-09-07. It returns **no** word timings, so the beat is
timed afterwards — faster-whisper transcribes it with word timestamps, `snap_to_script` puts the
locked script's words on the measured spans (the same function that times a human recording), and
a beat whose transcript drifts below `narration.script_similarity_min` fails by name rather than
shipping with captions that do not match. `even_split` is the offline stand-in and records itself
as `estimated`, never as measured. The stage's facts carry `timing_source` so a run says which it
was.

```bash
uv sync --project skills/audio/qwen3tts
CF__NARRATION__TTS=qwen3tts
CF__NARRATION__QWEN_SPEAKER=ryan            # ryan aiden vivian serena uncle_fu dylan eric ono_anna sohee
CF__NARRATION__QWEN_INSTRUCT="Calm documentary narrator, unhurried."
CF__NARRATION__ALIGNER=faster_whisper
```

Detail and the nine timbres: `skills/audio/qwen3tts/README.md`.

### Speech restoration

Every narrated deliverable's audio goes through a voice chain between the take and the mix
(`restore_speech`, `python/content_factory/audio/{detect,restore}.py`):

```
TTS or a recorded take
    -> artifact / noise detection      measurements, not taste: level, clipping, DC, noise floor,
                                       sibilance, spectral flatness, band limit
    -> ClearerVoice MossFormer2_SE_48K optional cleanup     (gated on the noise measurements)
    -> ClearerVoice MossFormer2_SR_48K optional 48 kHz band extension (gated on the band limit)
    -> Resemble Enhance                main restoration (denoise + generative band repair)
    -> de-esser -> EQ -> light compression      one deterministic FFmpeg graph
    -> 48 kHz WAV, exactly as long as the take
```

The true-peak limiter and the EBU R128 normalization that finish the chain are **not** here: they
are the programme master in `mix_audio`, applied after the music bed and SFX are under the speech.

### Generated sound effects and ambience

Generated **non-speech** audio goes through the same architecture and none of the same models
(`content_factory.audio.condition`, on by default via `sound_design.condition`):

```
generated bed / one-shot
    -> the same artifact detection, under a sound_effect profile
       (a flat spectrum is the content in a rain bed, not hiss)
    -> repair only what was measured: declip, declick at the generated-window seams, DC, sub trim
    -> normalise to a known loudness  (bed: integrated -23 LUFS; one-shot: max-momentary)
    -> the same true-peak limiter
    -> 48 kHz, exactly as long as it came in (a bed is scored against the picture)
```

**The speech models are deliberately absent, and that is measured, not assumed.** On sounds from
`assets/sfx`, ClearerVoice `MossFormer2_SE_48K` left 0.5-1.0 % of the energy of a whoosh, a rain
bed and an impact — to a speech enhancer a sound effect *is* the noise it removes. Resemble Enhance
took a whoosh from -24.0 to -73.4 dBFS with a **0.002** waveform correlation to its input: it
rebuilt what it heard as speech and returned unrelated rumble. `SoundConditionSpec` therefore has
no field for either, and `condition_sound` refuses a `speech` profile outright.

Normalising is what makes the bed's place in the mix reproducible. Before, the level was whatever
the model rendered minus a blind 22 dB, which for a quietly-rendered bed meant about -53 LUFS in a
-14 LUFS programme — inaudible. Now the bed sits at `bed_lufs` and `gain_db` is an operator trim.

The curated `fixtures/music` and `assets/sfx` libraries are **not** conditioned at runtime: they
are already mastered, and `select_music` verifies each track against its manifest hash, which
runtime processing would invalidate. Generated music gets the same treatment as SFX at its own
generation stage when it is wired.

The FFmpeg tail needs nothing but FFmpeg and is on by default. The two model steps are opt-in like
every other backend — see `.env.example`:

```bash
cd skills/audio/resemble_enhance && uv sync    # MIT code + MIT weights
cd skills/audio/clearervoice     && uv sync    # Apache-2.0 code + weights
./scripts/download_video_stack_extras.sh       # fetches both, links models/speech_restoration/
uv run content-factory video-stack             # SPEECH RESTORATION tier should read READY

CF__SPEECH_RESTORATION__ENHANCER=resemble_enhance
CF__SPEECH_RESTORATION__BAND_EXTENSION=clearervoice_sr
CF__SPEECH_RESTORATION__DEVICE=cuda            # cpu works but Resemble Enhance is ~19x realtime
```

Word timings never move: every step returns the beat at the length it received, the chain ends by
padding/trimming to the exact sample count, and both the skill scripts and the stage refuse to
write a beat that drifted more than 10 ms. What the `NarrationSegment` record gains is the new
audio hash and sample rate, so the contract keeps describing the bytes on disk. The synthesis
output is never overwritten — restoration writes `<beat>.restored.wav` beside it, so a rerun always
starts from the untouched take.

Per-model detail and measurements: `skills/audio/{resemble_enhance,clearervoice}/README.md` and
`docs/research/2026-09-07-speech-restoration-chain.md`.

## Non-generative video toolchain (charts, maps, diagrams, maths)

The deterministic lane: no model weights, no GPU. Remotion composites; the libraries below supply
the marks. Everything here is installed and smoke-tested on this host.

| Piece | Where | Version |
| --- | --- | --- |
| Remotion (renderer/compositor) | `apps/renderer` — this **is** the Remotion project; do not scaffold another with `create-video` | 4.0.518 |
| D3, Vega, Vega-Lite, Vega-Embed, MapLibre GL | `packages/video-ui` (dependencies of the scene components) | 7.9.0 / 6.4.0 / 6.4.3 / 7.2.0 / 6.7.0 |
| Playwright (web capture for `ScreenshotScene`) | `apps/renderer` devDependency | 1.63.0 + Chromium 153 |
| Manim (mathematical animation) | `skills/video/manim` — opt-in skill env, `CF__ANIMATION__EXECUTOR=manim` | 0.19.0 |
| Blender (3D scenes/camera) | system snap, driven by `skills/video/blender_scene` | 5.2.1 LTS |
| FFmpeg, build-essential, python3-dev, libcairo2-dev, libpango1.0-dev | system packages (already present) | 6.1.1 / 12.10 / 3.12.3 / 1.18.0 / 1.52.1 |

Setup is already done; to redo it on another host:

```bash
sudo apt install -y ffmpeg build-essential python3-dev libcairo2-dev libpango1.0-dev
pnpm install                                  # workspace deps, including the viz libraries
pnpm --filter @content-factory/renderer exec playwright install chromium
uv sync --project skills/video/manim          # + optional: sudo apt install texlive texlive-latex-extra
```

### Things that will bite you

- **MapLibre GL v6 is ESM-only with named exports.** There is no default export and no UMD build:
  `import { Map } from "maplibre-gl"`, never `import maplibregl from "maplibre-gl"`.
- **MapLibre needs WebGL in the render browser.** It works in headless Chromium (verified: WebGL 2.0),
  but to *capture* its canvas you must construct the map with `preserveDrawingBuffer: true` —
  otherwise the drawing buffer reads back black even though the page renders correctly.
- **Vega-Embed is browser-only** (it needs a DOM). For deterministic server-side charts prefer
  `vega.View(vega.parse(compile(spec).spec), {renderer: "none"}).toSVG()`, which needs no canvas and
  no DOM, and gives an SVG string a scene can inline.
- **Manim needs LaTeX only for `MathTex`.** No texlive is installed here, so `render.py` renders every
  glyph route through `Text` instead (including `DecimalNumber`, which typesets via LaTeX by default).
  Install `texlive texlive-latex-extra` if real equation typesetting is wanted.
- **Playwright's `install --with-deps` needs root.** It was not run; the system libraries are already
  satisfied, which is why headless Chromium launches and screenshots correctly.
- **Topojson ring winding** decides whether a region fills or floods. d3-geo reads a
  counter-clockwise exterior ring as the whole globe minus that shape, so one badly wound region
  covers the entire plot. Exterior rings must be clockwise in lon/lat. Published topology (Natural
  Earth, us-atlas, world-atlas) is already correct; hand-built or re-exported topology is where it
  bites, and it presents as a projection bug rather than a data bug.
- **MapLibre is installed but nothing imports it.** `MapScene` is d3-geo, because the contract asks
  for topojson and d3 projection names. MapLibre is there for a future tiled-basemap scene, which
  would be a contract change and would bring the WebGL constraints above with it.

### What the libraries are used by

| Library | Used by |
| --- | --- |
| D3 (shape, scale, geo) | `ChartScene` donut, stacked bar, histogram, scatter, bubble, waterfall; `MapScene` projections and path geometry |
| topojson-client | `MapScene`, decoding the `region` asset to GeoJSON |
| Playwright | `scripts/capture-screenshot.mjs`, producing the PNG that `ScreenshotScene` renders |
| Vega, Vega-Lite, Vega-Embed | not yet imported — kept for declarative statistical charts a hand-built scene would be tedious for (compile to a static SVG, never `vega-embed`) |
| MapLibre GL | not yet imported — see above |

## The two ways this box goes down, and how to avoid both

Both were hit on 2026-09-12. They look identical from the chair — the machine stops — and they
have nothing in common underneath, so they are worth telling apart.

### 1. The terminal dies, everything else keeps running (system RAM)

`journalctl -k` shows `oom-kill`, and the scope it names is the **terminal's**, not the model's.
systemd kills per scope, so what dies is the shell you were watching from while the process that
actually ate the memory survives.

The binding constraint on this box is **31 GB of system RAM, not the 3090's 24 GB of VRAM**. A
loaded ComfyUI offloads ~8 GB to system RAM *on top of* VRAM, musubi-tuner with
`--blocks_to_swap 24` does the same, and `uv run pytest` forks a worker per core on a 12900K. Any
two of those fit. Three do not, and swap is only 3.7 GB.

- Read `free -g` and use the **available** column, not `free`. Swap showing 3/3 with 25 GB
  available is stale pages and is fine.
- Never run the test suites while a model is resident.
- Run renders in the foreground, one at a time.

### 2. The whole machine hard-locks (GPU driver)

No `oom-kill`. No hung-task trace. `journalctl --list-boots` shows the boot simply **ending** —
the kernel died without getting to log anything, which is a driver fault rather than memory
pressure. Seen on 2026-09-12 at 23:42:19, four seconds after ComfyUI's CUDA init returned:

```
RuntimeError: CUDA unknown error - this may be due to an incorrectly set up environment,
e.g. changing env variable CUDA_VISIBLE_DEVICES after program start.
Setting the available devices to be zero.
```

The cause was a tenant handover that did not wait. `LocalServices.stop()` waits for the old
tenant to stop answering HTTP — which happens the moment it closes its socket, while the process
is still tearing down 21 GB of CUDA allocations — and then slept a fixed three seconds under a
comment reading "driver releases VRAM after exit". ComfyUI was started two seconds later, into a
card the driver had not given back.

**Fixed** (`services/local.py::_wait_for_vram`): the handover now polls `nvidia-smi` until free
VRAM actually comes back past `local_services.vram_free_target_mib` (18 GB), or stops climbing for
`vram_settle_s` — the second condition so a card another process legitimately part-owns does not
stall every switch. Exceeding `vram_release_timeout_s` is not an error; a slow release is still a
release.

If you are driving the GPU by hand rather than through `LocalServices`, wait for the number
yourself:

```bash
uv run content-factory services stop --tenant hidream
until [ "$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits)" -gt 18000 ]; do sleep 2; done
```

**Switching models inside one tenant needs the same care and gets none of it automatically.**
FLUX.2 and Ideogram 4 are both ComfyUI, so `ensure()` sees a healthy server and does not reload:
the first model keeps ~18 GB cached in-process and the second OOMs the card. Stop the tenant
between them.

## More than one GPU host, and sharing a card
See **[docs/gpu-hosts.md](gpu-hosts.md)**: adding a second machine that generates frames alongside
this one (driver, storage at the identical `/mnt/fast`, weights that no download script fetches,
the pinned checkouts, the per-tool venvs, exposing the servers on the tailnet, wiring the endpoint
pool), and `content-factory gpu yield|resume|status` for giving the card to a higher-priority
tenant and resuming the parked run afterwards.

## Tailscale
See README "Tailscale access". The app never binds beyond loopback; `tailscale serve` provides
tailnet HTTPS. Funnel requires password auth + MFA (enforced at config validation).

## Resetting
`docker compose down -v` deletes Postgres and Temporal data. `rm -rf data/` deletes artifacts.
Both are local-only; nothing external is touched.
