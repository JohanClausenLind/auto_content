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
| `output/` | generated media: image-sequence workdirs (`image_sequences.output_root`), `output/eval/` evaluation runs |
| `.venvs/` | tool venvs: `download` (the `hf` CLI the download scripts use) and `scenedetect` |
| `sandbox/` | throwaway prototypes (two untouched `create-video` Remotion scaffolds) |
| `scripts/host/` | host provisioning: `setup-model-storage.sh` (created `/mnt/fast`) and the model-move logs |

Populate with `./scripts/download_ai_video_stack.sh` then `./scripts/download_video_stack_extras.sh`;
verify with `uv run content-factory video-stack`. `AI_VIDEO_ROOT` (a directory holding `external/`)
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
`just setup-postchain` builds the Cutie / ProPainter / GIMM-VFI environments the post chain calls
into.

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

## A second GPU host

A sequence's frames are hub-and-spoke — each is an edit of the anchor, never of its neighbour —
so at 100–385 s a frame they are worth generating on two machines at once. `image_sequences.
hidream_endpoints` and `flux2_endpoints` take a list; each **replaces** its singular field, so the
local server has to appear in the list to keep a share of the work. Unset is one endpoint and the
serial path, byte for byte.

Provisioning the second box (this tailnet: `nova`, 100.82.150.94):

```bash
# On the far host, as the operator (all of this needs sudo).
sudo mkdir -p /mnt/fast
sudo mount -o ro /dev/sda1 /mnt/fast && ls /mnt/fast   # LOOK before committing to the disk
sudo umount /mnt/fast
echo "UUID=$(sudo blkid -s UUID -o value /dev/sda1) /mnt/fast ext4 defaults,nofail 0 2" \
  | sudo tee -a /etc/fstab
sudo mount /mnt/fast && sudo chown "$USER:$USER" /mnt/fast

sudo apt install -y ffmpeg git-lfs build-essential python3-dev libcairo2-dev libpango1.0-dev
git lfs install
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash - && sudo apt install -y nodejs

git clone <this repo> ~/git/auto_content && cd ~/git/auto_content && ./setup.sh
```

Use `/mnt/fast` **at the identical path**: `shots/planner.py`, `cli/workflows_cmd.py`,
`controls/blender.py`, `reference/build.py` and `skills/video/postchain/common.py` hardcode it with
no environment escape, so a different mount point means patching all five.

Then, from the machine that has the weights:

```bash
rsync -aHAX --info=progress2 --partial /mnt/fast/models/  nova:/mnt/fast/models/
rsync -aHAX --info=progress2 --partial ~/git/auto_content/models/  nova:~/git/auto_content/models/
```

The second copies the symlink index; its links resolve on both hosts because the store is at the
same path. 278 GB takes about 45 minutes on gigabit — run it under `tmux`.

Expose the far host's servers on its **tailnet** address, never `0.0.0.0`: neither server has any
authentication, and both hand out a whole GPU.

```bash
# on nova
CF_HIDREAM_HOST=100.82.150.94 uv run --project skills/image/hidream \
  python skills/image/hidream/server.py &
python ~/git/ComfyUI/main.py --listen 100.82.150.94 --port 8188 &
```

```bash
# on the control-plane host, in .env
CF__IMAGE_SEQUENCES__HIDREAM_ENDPOINTS=["http://127.0.0.1:8801","http://100.82.150.94:8801"]
CF__LOCAL_SERVICES__AUTO_START=false   # it must not try to start and stop servers it does not own
```

Each frame's marker records `served_by`, so `jq -r .served_by <run>/sequence/frames/*.done.json`
says which card drew what. `generate_video`'s clips are **not** pooled yet and still run one at a
time against `CF__COMFYUI__ENDPOINT`.

## Sharing the card with the flashcards agent

`~/.openclaw/workspace/flashcards-agent` shares this machine's single 24 GB card. Its session LLM
asks `ensure_vram()` for `OLLAMA_VRAM_GB` plus a 1.5 GB margin — 17.5 GB by default — and a Krea2
anchor render is ~17.4 GB resident, so the two can never be co-resident. Its own `backend/vram.py`
knows only how to unload Ollama models, so when a render holds the card it runs out of things to
free and the session degrades.

`content-factory gpu` is the other half:

| Command | Does |
| --- | --- |
| `gpu status` | free VRAM, who holds the claim, what is parked and the command that resumes it |
| `gpu yield --need-gb 17.5 --reason flashcards` | returns at once if enough is free; otherwise parks the local run at a stage boundary, unloads the model servers, and leaves a claim. Exits non-zero if it still cannot make room |
| `gpu resume` | starts whatever was parked, from the stage it stopped at, with its original options; drops the claim. Idempotent |

Parking reuses the stop the runner already survives: `stop_requested` is read at the next step
boundary, the report is written, finished stages stay on disk, and the registration's own `step` is
a node key that `run-local --from` accepts. A stage in flight can hold the card for ten minutes, so
`--deadline` (90 s) escalates to the signalling stop — still resumable, from the interrupted stage
rather than the one after it.

While a claim is held, `run_plan` refuses to start: priority that works one way is not priority.
The claim expires after `gpu_priority.MAX_CLAIM_HOLD_S` (2 h) because the other tenant is a separate
program that can crash, and `CF_IGNORE_GPU_CLAIM=1` overrides it deliberately.

**Wire it to demand, not to a clock.** The agent's 06:15 and 20:30 cron entries only send a Web
Push nudge — they touch no GPU. The demand arrives when somebody opens the app, so the hook belongs
in `ensure_vram()`, which already knows. Preempting a six-hour film on a schedule nobody consulted
is how the film never finishes.

## Tailscale
See README "Tailscale access". The app never binds beyond loopback; `tailscale serve` provides
tailnet HTTPS. Funnel requires password auth + MFA (enforced at config validation).

## Resetting
`docker compose down -v` deletes Postgres and Temporal data. `rm -rf data/` deletes artifacts.
Both are local-only; nothing external is touched.
