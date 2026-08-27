# ComfyUI, comfy-cli and image-editing models — phase-zero research

**Retrieved 2026-08-27.** Every fact below is tied to a fetched URL (retrieved 2026-08-27 unless noted) or to a read-only local inspection of the already-installed tooling (`comfy` 1.17.0, ComfyUI checkout at `/home/vega/git/ComfyUI`, version 0.33.0, HEAD `0f1fa67` dated 2026-08-15). Anything not confirmed by a source is marked **UNVERIFIED**.

Local machine (from `comfy env`): RTX 3090, 24 GB VRAM (25,769,803,776 bytes), 33 GB RAM, workspace `/home/vega/git/ComfyUI`, ComfyUI-Manager installed as pip package `comfyui_manager` 4.2.2.

---

## 1. comfy-cli

### Version and license
- Latest PyPI release: **comfy-cli 1.18.0**, released **Aug 24, 2026**, license **GPL-3.0-only**, Python >=3.10. — https://pypi.org/project/comfy-cli/
- README: "Released under the GNU General Public License v3.0". — https://github.com/Comfy-Org/comfy-cli/blob/main/README.md
- Locally installed: **1.17.0** (`comfy --version`, via `uv tool`). 1.18.0 adds `comfy knowledge` verbs; no behaviour changes for the commands below were listed. — https://github.com/Comfy-Org/comfy-cli/releases

### Command-by-command confirmation (README + local `--help`)
| Command | Exists? | Confirmed syntax / notes | Source |
|---|---|---|---|
| `comfy --workspace=<dir> install` | yes | `--workspace` is a **global** option (before `install`); `comfy install` itself also accepts `--nvidia/--no-nvidia`, `--cpu/--no-cpu`, `--amd`, `--m-series`, `--skip-manager`, `--version <nightly\|latest\|0.2.0>`, `--commit`, `--cuda-version <13.0..11.8>`, `--fast-deps` (uv), `--pr`, `--skip-torch-or-directml`, `--skip-requirement`, `--restore`. README example: `comfy --workspace=<path> install`, `comfy install --skip-manager`, `comfy install --version 0.3.0`. | README; local `comfy install --help` |
| `comfy launch --background` | yes | `comfy launch [--background] [--frontend-pr ..] -- <extra ComfyUI args>`; e.g. `comfy --workspace=~/comfy launch --background -- --listen 10.0.0.10 --port 8000`. | README; local help |
| `comfy stop` | yes | Stops background ComfyUI; `--port <n>` stops an untracked local ComfyUI on that port (refuses anything it can't identify as ComfyUI); `--dry-run`. | local help; README |
| `comfy env` | yes | Prints env/server/hardware/workspace; honors `COMFY_LOCAL_URL`. Locally emits a JSON envelope when stdout is not a TTY. | README; local run |
| `comfy which` | yes | Shows selected workspace (`comfy which`, `comfy --recent which`, `comfy --here which`). | README; local |
| `comfy node install <name>` | yes | `comfy node install comfyui-impact-pack [--fast-deps] [--uv-compile] [--no-deps] [--channel] [--mode remote\|local\|cache] [--exit-on-fail]`. README: "delegates to ComfyUI-Manager's cm-cli. It accepts one or more node IDs, resolves them through the Manager's channel database". A separate `comfy node registry-install <node_id>` "talks directly to the Comfy Registry API… does not go through ComfyUI-Manager" (exists locally too). | README; local help |
| `comfy node deps-in-workflow --workflow=<file>` | yes | **`--output <deps.json>` is also required** locally: `comfy node deps-in-workflow --workflow=<wf.json/.png> --output=<deps.json>`. | README; local help |
| `comfy node install-deps --workflow=<file>` | yes | `comfy node install-deps --deps=<deps.json>` or `--workflow=<wf.json/.png>`; `--uv-compile`. | README; local help |
| `comfy node bisect` | yes | Sub-commands `start [--pinned-nodes ..] [-- <launch args>]`, `good`, `bad`, `reset`. | README; local help |
| `comfy node save-snapshot` / `restore-snapshot` | yes | `save-snapshot [--output <file.json/.yaml>]`; `restore-snapshot <path> [--pip-non-url] [--pip-non-local-url] [--pip-local-url] [--fast-deps] [--uv-compile]`. Default snapshot dir (ComfyUI-Manager): `<USER_DIRECTORY>/default/ComfyUI-Manager/snapshots`; cm-cli: "If no file exists at the snapshot path, it is implicitly assumed to be in ComfyUI-Manager/snapshots"; format .json or .yaml. comfy-cli also has a beta `comfy-lock.yaml` format compatible with Manager's yaml snapshot. | local help; https://github.com/Comfy-Org/ComfyUI-Manager ; https://github.com/Comfy-Org/ComfyUI-Manager/blob/main/docs/en/cm-cli.md ; README |
| `comfy model download --url <url> --relative-path models/<class>` | yes | Also `--filename`, `--set-civitai-api-token`, `--set-hf-api-token`, `--downloader httpx\|aria2`, `--background` (+ `comfy model download-status <id>`). Env vars **`CIVITAI_API_TOKEN`**, **`HF_API_TOKEN`**; priority "`--set-X-token` (always highest), then the environment variables, and lastly your config's stored tokens". `comfy auth set/list/remove` also manages these tokens. | README; local help |
| `comfy model list` / `remove` | yes | `list [--relative-path models]`; `remove [--relative-path models] --model-names <names> [--confirm]`. | README; local help |
| `comfy run --workflow x.json --wait --json` | yes | Default is submit-and-return; `--wait` blocks. `--json` streams NDJSON (`{"schema":"event/1","type":...}` per line, final `type:"envelope"` line). Accepts API-format **or** UI-format JSON (UI is converted client-side via `/object_info`, emits `converted` then `queued`; `prompt_preview` event always precedes `queued`). `--print-prompt`, `--host`, `--port`, `--timeout` (per-event, default 120 s), `--api-key`/`COMFY_API_KEY`, `--where local\|cloud`. | local help; https://raw.githubusercontent.com/Comfy-Org/comfy-cli/main/docs/json-output.md |
| `comfy validate` | **exists but DEPRECATED** | Local help: "[DEPRECATED — use 'comfy workflow validate'] Validate a workflow without submitting (UI exports are converted to API format first). Checks class_types, input shapes, enum values, edge wiring…"; `--workflow <file>`, `--input <saved object_info.json>` for offline mode. Use `comfy workflow validate`. (`comfy node validate` is a different command: validates a custom-node package for registry publishing.) | local help |
| `comfy skills install` | yes | `comfy skills install [--scope user\|project] [--target ..] [--skill ..] [--dry-run]`; writes skills into Claude Code / Cursor / AGENTS.md. `comfy skills list` locally returns: `comfy`, `comfy-debug`, `comfy-relay`, `comfy-director`, `comfy-build` (last one fetched from Comfy-Org/comfy-skills). | local help; https://docs.comfy.org/comfy-cli/getting-started |

None of the listed commands is missing. Extra useful commands seen locally: `comfy jobs ls|status|wait|cancel|watch`, `comfy upload`, `comfy download <prompt_id>`, `comfy system-stats`, `comfy free`, `comfy logs`, `comfy nodes ls|show|search`, `comfy workflow slots|set-slot|vary|validate|compose|add-node|connect|set-widget`, `comfy templates`, `comfy discover`, `comfy --help-json`.

### JSON envelope (observed locally, comfy-cli 1.17.0)
Global flags `--json`, `--json-stream`, `--no-json`, `--help-json`. When stdout is not a TTY the CLI auto-emits the envelope, e.g. `comfy which` →
`{"schema":"envelope/1","type":"envelope","ok":true,"command":"which","version":"1.17.0","where":null,"data":{"workspace_path":"/home/vega/git/ComfyUI","workspace_type":"default"},"error":null}`.
Contract (docs/json-output.md): envelope fields `schema, type, ok, command, version, where, data, error`; error object `{code, message, hint, details}`; exit codes 0 success / 130 cancelled / 1 other; event names and error-code registry are stable; unknown events/fields must be tolerated. `comfy --json discover` lists `error_codes` (e.g. `server_already_running`, `port_in_use`, `workflow_not_api_format`, `no_background_server`). — https://raw.githubusercontent.com/Comfy-Org/comfy-cli/main/docs/json-output.md

### comfy-mcp
- PyPI `comfy-mcp` **0.10.0**, license **"AGPL-3.0-or-later OR Commercial"**, "MCP server built on comfy-cli that enables AI agents to drive ComfyUI workflows locally or remotely", Python >=3.10, depends on `comfy-cli>=1.14.0`. Install: `pip install comfy-mcp "comfy-cli>=1.14.0"`; Claude Code: `claude mcp add comfy-mcp -e COMFY_BIN=/path/to/venv/bin/comfy -e COMFY_API_KEY=<key> -- comfy-mcp`. Every tool shells out to `comfy --json --where local` and parses the `envelope/1`. ~39–40 tools (run_workflow, fetch_outputs, server_info, jobs, lifecycle, model/node management). v0.10.0 released Aug 10 (2026). — https://pypi.org/project/comfy-mcp/ ; https://github.com/Comfy-Org/comfy-mcp ; https://github.com/Comfy-Org/comfy-mcp/releases
- Locally installed: `comfy-mcp v0.10.0` (`uv tool list`). Note: AGPL is a copyleft license stronger than comfy-cli's GPL-3.0 — relevant if this project redistributes/serves it.

---

## 2. ComfyUI server HTTP / WebSocket API

Sources: https://docs.comfy.org/development/comfyui-server/comms_routes , https://docs.comfy.org/development/comfyui-server/comms_messages , and local `/home/vega/git/ComfyUI/server.py`, `execution.py`, `protocol.py` (v0.33.0).

- **`POST /prompt`** body: `{"prompt": {...api-format graph...}, "client_id": "<sid>", "extra_data": {...}}`; optional `prompt_id` (must be canonical lowercase UUID, else 400 `{"error":{"type":"invalid_prompt_id",...},"node_errors":{}}`), `number`, `front`, `partial_execution_targets`. `client_id` is copied into `extra_data["client_id"]`. Success response: `{"prompt_id": "<uuid>", "number": <float>, "node_errors": {...}}`. Validation failure: `{"error": {...}, "node_errors": {...}}` with HTTP 400. (server.py lines 1072–1140)
- **`GET /history`** and **`GET /history/{prompt_id}`** → `{ "<prompt_id>": {"prompt": [number, prompt_id, prompt, extra_data, outputs_to_execute], "outputs": {"<node_id>": {"images":[{"filename","subfolder","type"}], ...}}, "status": {"status_str": "success"|"error", "completed": bool, "messages": [...]}, "meta": {...}} }` (execution.py ~1300; docs list the route).
- **`GET /view?filename=&subfolder=&type=`** — `type` defaults to `output` (`input`/`temp` also valid); optional `preview=` and `channel=`. (server.py 516–560)
- **`POST /upload/image`** — multipart fields `image`, `overwrite` ("true"/"1"), `subfolder`, `type`; response JSON `{"name","subfolder","type"}` (docs list the fields; server.py 398–430). Also `POST /upload/mask`.
- **`GET /object_info`** and **`GET /object_info/{node_class}`** → per class: `{"input": {"required": {...}, "optional": {...}, "hidden": {...}}, "input_order", "is_input_list", "output": [types], "output_is_list", "output_name", "name", "display_name", "description", "python_module", "category", "output_node": bool, "has_intermediate_output", "output_tooltips", "deprecated", "experimental", "dev_only"}`. (server.py 751–790)
- **`GET /system_stats`** → `{"system": {"os","ram_total","ram_free","comfyui_version","required_frontend_version","installed_templates_version","required_templates_version",...}, "devices": [{"name","type","index","vram_total","vram_free","torch_vram_total","torch_vram_free"}]}`. (server.py 686–730)
- **`POST /interrupt`** — body optional `{"prompt_id": "..."}` for targeted interrupt of the running prompt, else global interrupt; returns 200. (server.py 1160–1190)
- **`POST /free`** — body `{"unload_models": bool, "free_memory": bool}`; applied on the worker's next iteration. (server.py 1192–1200)
- **`GET /queue`** → `{"queue_running": [...], "queue_pending": [...]}`; **`POST /queue`** body `{"clear": true}` and/or `{"delete": ["<prompt_id>", ...]}`. (server.py 1064–1158)
- **`GET /prompt`** → `{"exec_info": {"queue_remaining": n}}`. Others: `GET /embeddings`, `GET /models`, `GET /models/{folder}`, `GET /features`, `GET /extensions`, `POST /history` (clear/delete), and newer `GET /api/jobs`, `GET /api/jobs/{job_id}`, `POST /api/jobs/{job_id}/cancel`.
- **WebSocket `GET /ws?clientId=<sid>`** — if `clientId` is omitted the server generates one and returns it in the first message `{"type":"status","data":{"status":{"exec_info":{"queue_remaining":n}},"sid":"<sid>"}}`. Text messages are `{"type": <name>, "data": {...}}`:
  - `status` → `exec_info.queue_remaining` (+ `sid` on connect)
  - `execution_start` → `prompt_id`, `timestamp`
  - `execution_cached` → `prompt_id`, `nodes` (list of cached node ids), `timestamp`
  - `executing` → `node` (node id or `null` when the prompt finishes), `display_node`, `prompt_id`
  - `progress` → `value`, `max`, `prompt_id`, `node`
  - `progress_state` → `prompt_id`, `nodes: {node_id: {state, node_id, prompt_id, display_node_id, parent_node_id, real_node_id, value, max}}` (comfy_execution/progress.py)
  - `executed` → `node`, `display_node`, `output` (e.g. `{"images":[{filename,subfolder,type}]}`), `prompt_id`
  - `execution_success` → `prompt_id`, `timestamp`
  - `execution_error` → `prompt_id`, `node_id`, `node_type`, `executed`, `exception_message`, `exception_type`, `traceback`, `current_inputs`, `current_outputs`
  - `execution_interrupted` → `prompt_id`, `node_id`, `node_type`, `executed`
  - Binary frames: first 4 bytes big-endian event type (`protocol.BinaryEventTypes`: 1 PREVIEW_IMAGE, 2 UNENCODED_PREVIEW_IMAGE, 3 TEXT, 4 PREVIEW_IMAGE_WITH_METADATA) followed by payload (for previews: 4-byte image-format code then JPEG/PNG bytes).
  Custom nodes can send arbitrary types via `PromptServer.instance.send_sync("my.custom.message", dict)`.
- **API-format export**: docs say use **File → Export Workflow (API)** in the frontend (older docs/search results describe enabling "Dev mode" in Settings then "Save (API Format)"; the comfy-cli error hint also says "use ComfyUI's `File > Export (API)`"). Shape: `{"<node_id>": {"class_type": "KSampler", "inputs": {"seed": 1, "model": ["4", 0], ...}, "_meta": {"title": "KSampler"}}, ...}`; links are `[source_node_id, output_index]`. — https://docs.comfy.org/development/api-development/workflow-api-format

---

## 3. ComfyUI release and licenses
- Latest GitHub release: **v0.34.0**, `published_at` **2026-08-26T02:09:08Z**, repo now **Comfy-Org/ComfyUI** (comfyanonymous/ComfyUI redirects). Release notes include "Bump comfyui-frontend-package to 1.49.6", "Update embedded docs to v0.5.10". Preceding tags: v0.33.1 (Aug 13), v0.32.0 (Aug 11), v0.31.0 (Aug 8). — https://api.github.com/repos/Comfy-Org/ComfyUI/releases/latest ; https://github.com/Comfy-Org/ComfyUI/releases
- Local checkout is **0.33.0** (`comfyui_version.py`), `requirements.txt` pins `comfyui-frontend-package==1.49.6`, `comfyui-workflow-templates==0.11.41`, `comfyui-embedded-docs==0.5.10`.
- ComfyUI license: **GPL-3.0** ("GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007"). — https://raw.githubusercontent.com/comfyanonymous/ComfyUI/master/LICENSE
- Frontend: PyPI `comfyui-frontend-package` latest **1.51.9** (released Aug 26, 2026) shows **no license metadata on PyPI**; the source repo `Comfy-Org/ComfyUI_frontend` LICENSE is **GPL-3.0**. — https://pypi.org/project/comfyui-frontend-package/ ; https://github.com/Comfy-Org/ComfyUI_frontend/blob/main/LICENSE

---

## 4. HiDream models — does "HiDream-O1" exist?

**Yes.** HiDream-O1-Image exists, is MIT-licensed, and is natively supported in ComfyUI core, including a node literally named `HiDreamO1ReferenceImages`.

### Official model family (HiDream-ai)
| Model | Params | Released | License | Notes | Source |
|---|---|---|---|---|---|
| HiDream-I1 (Full/Dev/Fast) | 17B | 2025 | MIT | T2I; 50/28/16 steps; text encoders Llama-3.1-8B-Instruct + T5 + CLIP-L/G | https://github.com/HiDream-ai/HiDream-I1 |
| HiDream-E1-Full | 17B | 2025-04-28 | MIT | instruction edit, fixed 768×768 | https://github.com/HiDream-ai/HiDream-E1 |
| HiDream-E1.1 (`HiDream-E1-1`) | 17B | 2025-07-16 | MIT | dynamic resolution up to 1 MP | same |
| **HiDream-O1-Image** | **8B** (HF card says 8B; HF org listing shows "9B") | **2026-05-08** | **MIT** ("The code in this repository and the HiDream-O1-Image models are licensed under MIT License") | Pixel-level Unified Transformer (UiT), no VAE, no external text encoder; T2I, instruction edit, multi-reference personalization, storyboard; up to 2048×2048; 50 steps | https://github.com/HiDream-ai/HiDream-O1-Image ; https://huggingface.co/HiDream-ai/HiDream-O1-Image |
| HiDream-O1-Image-Dev | 8B distilled | 2026-05-08 | MIT | 28 steps, CFG 1.0 | same |
| HiDream-O1-Image-Dev-2604 | 8B | 2026-05-14 | MIT | T2I-optimised, with prompt refiner | same |
| HiDream-O1-Image-Pro | 200B+ | — | — | **weights not released** | README |
- Upstream news: 2026-05-13 "IP pipeline now supports layout and skeleton conditioning" (pose/layout control in the *official* pipeline). — https://raw.githubusercontent.com/HiDream-ai/HiDream-O1-Image/main/README.md
- HF org listing confirms models `HiDream-O1-Image`, `HiDream-O1-Image-Dev`, `HiDream-O1-Image-Dev-2604`, `HiDream-I1-Full/Fast`, `HiDream-E1-Full`, `HiDream-E1-1`. — https://huggingface.co/HiDream-ai

### ComfyUI native support for HiDream-O1
- Core PR "feat: Support HiDream-O1-Image (CORE-187)" #13817 by kijai, merged **2026-05-12**; adds nodes `EmptyHiDreamO1LatentImage`, `HiDreamO1ReferenceImages`, `HiDreamO1PatchSeamSmoothing`; "Pixel-space DiT (no VAE)"; "Re-packaged and quantized models: fp8 or mxfp8 matmuls on safe MLP layers… worst outliers kept in bf16". — https://github.com/Comfy-Org/ComfyUI/pull/13817
- Local code confirms (`/home/vega/git/ComfyUI/comfy_extras/nodes_hidream_o1.py`): `HiDreamO1ReferenceImages` (display "HiDream-O1 Reference Images", category `model/conditioning/hidream`) takes `positive`, `negative` conditioning and an autogrow `images` input `image_1..image_10` (min 1): "1 image = instruction edit; 2-10 images = multi reference"; outputs positive/negative with `reference_latents` appended. `comfy/supported_models.py::HiDreamO1`: `supported_inference_dtypes = [bf16, fp32]` ("fp16 not supported"), `optimizations = {"fp8": False}`, shift 3.0, tokenizer-only text encoder (Qwen2 tokenizer; the Qwen3-VL backbone lives inside the checkpoint). Docs node page: https://docs.comfy.org/built-in-nodes/HiDreamO1ReferenceImages
- Official tutorial: https://docs.comfy.org/tutorials/image/hidream/hidream-o1 — templates **"HiDream O1 Full"** and **"HiDream O1 Dev"** (local template files `image_hidream_o1.json`, `image_hidream_o1_dev.json` in `comfyui_workflow_templates_json` 0.1.47). Edit mode: "set 'Switch to Image Edit' to on, upload a reference image in Load Image, and connect it to HiDreamO1ReferenceImages". Nodes used: CheckpointLoaderSimple, CLIPTextEncode, LoadImage, HiDreamO1ReferenceImages, EmptyHiDreamO1LatentImage, SamplerCustom/BasicScheduler/ModelNoiseScale, VAEDecode (pixel-space passthrough), ImageScaleToTotalPixels, HiDreamO1PatchSeamSmoothing, plus a "Prompt Enhancement" subgraph (CLIPLoader → TextGenerate). — template JSON: https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates/image_hidream_o1_dev.json

### Files, folders, sizes (Comfy-Org repack, MIT)
All-in-one checkpoints → **`ComfyUI/models/checkpoints/`** ("bundles a diffusion model, text encoder, and VAE"). — https://huggingface.co/Comfy-Org/HiDream-O1-Image ; https://comfy.org/p/supported-models/hidream-o1-image-bf16/
| File (`checkpoints/`) | Size |
|---|---|
| `hidream_o1_image_bf16.safetensors` | 16.4 GB |
| `hidream_o1_image_fp8_scaled.safetensors` | 8.07 GB |
| `hidream_o1_image_mxfp8.safetensors` | 8.92 GB |
| `hidream_o1_image_dev_bf16.safetensors` | 16.4 GB |
| `hidream_o1_image_dev_fp8_scaled.safetensors` | 8.07 GB (default in the Dev template) |
| `hidream_o1_image_dev_mxfp8.safetensors` | 8.92 GB |
— https://huggingface.co/Comfy-Org/HiDream-O1-Image/tree/main/checkpoints
- Prompt-enhancement LLM (used by the template's TextGenerate subgraph, labelled "Text Encoder (prompt enhancement): shared across all versions"): `gemma4_e4b_it_fp8_scaled.safetensors` **9.06 GB** → `ComfyUI/models/text_encoders/` (repo license Apache-2.0). It is a separate optional prompt-rewrite stage, not the model's text encoder (local `comfy/text_encoders/hidream_o1.py` confirms the checkpoint needs no external TE). — https://huggingface.co/Comfy-Org/gemma-4/tree/main/text_encoders ; docs tutorial above
- LoRAs (`models/loras/`): `hidream_o1_dev_lora_rank_64_bf16.safetensors`, `..._pruned_v1.safetensors`, `hidream_o1_image_dev_2604_lora_avg_rankg_224_bf16.safetensors`. — docs tutorial
- Recommended: Full = 50 steps; Dev = 28 steps, CFG 1.0, no negative prompt. — docs tutorial

### VRAM guidance for 24 GB (RTX 3090)
- No official VRAM table exists for O1 (docs tutorial has none). PR discussion: "The bf16 quantized version of the model still requires 17–20 GB of VRAM" (community comment in #13817). fp8_scaled checkpoint = 8.07 GB on disk; ComfyUI offloads models between stages, so **fp8_scaled O1 (8 GB) + optional Gemma4-e4b fp8 (9 GB) fits a 24 GB card**; bf16 (16.4 GB) should also fit for inference without the Gemma stage. **Exact peak usage at 2048² with 10 reference images: UNVERIFIED.** Note fp8 matmul speedup needs Ada/Hopper; on a 3090 fp8 weights are dequantised (still saves memory).
- HiDream-I1 (docs): fp8 variants ">16GB", full precision ">27GB" → I1 fp8 fits 24 GB. HiDream-E1.1 bf16 `hidream_e1_1_bf16.safetensors` is 34.2 GB and docs note fp16 "ran out of memory on both A100 40GB and 4090D 24GB", recommending loading with `weight_dtype fp8_e4m3fn_fast`. — https://docs.comfy.org/tutorials/image/hidream/hidream-i1 ; https://docs.comfy.org/tutorials/image/hidream/hidream-e1

### HiDream-E1 / I1 in ComfyUI (for completeness)
- E1.1 files: `hidream_e1_1_bf16.safetensors` (34.2 GB) → `models/diffusion_models/`; text encoders `clip_l_hidream.safetensors` (236 MB), `clip_g_hidream.safetensors` (1.29 GB), `t5xxl_fp8_e4m3fn_scaled.safetensors` (4.8 GB), `llama_3.1_8b_instruct_fp8_scaled.safetensors` (8.46 GB) → `models/text_encoders/`; `ae.safetensors` (320 MB) → `models/vae/`. Nodes: `QuadrupleCLIPLoader`, `CLIPTextEncodeHiDream`, UNETLoader, ReferenceLatent (local `nodes_hidream.py`, `nodes_edit_model.py`). Templates `hidream_e1_1.json`, `hidream_e1_full.json`. The Comfy-Org/HiDream-E1_ComfyUI HF repo returned HTTP 401 (gated) — **no fp8 E1.1 file confirmed: UNVERIFIED**. — https://docs.comfy.org/tutorials/image/hidream/hidream-e1
- I1 files → `models/diffusion_models/`: `hidream_i1_{full,dev,fast}_{fp8,fp16|bf16}.safetensors`; same four text encoders + `ae.safetensors`; Full 50 steps shift 3 cfg 5; Dev 28 steps shift 6 cfg 1; Fast 16 steps shift 3 cfg 1. — https://docs.comfy.org/tutorials/image/hidream/hidream-i1

### Alternative open reference-image editing models in ComfyUI core (comparison)
| Model | License | Core files (folder) | 24 GB fit | Multi-ref | Official pose/depth/canny control path | Source |
|---|---|---|---|---|---|---|
| **HiDream-O1-Image(-Dev)** | MIT | `hidream_o1_image[_dev]_fp8_scaled.safetensors` 8.07 GB (`checkpoints/`) | yes (fp8, bf16) | 1–10 images via `HiDreamO1ReferenceImages` | upstream pipeline supports skeleton/layout; **no ComfyUI node for it** (local grep finds none) | above |
| **Qwen-Image-Edit-2509 / 2511** | Apache-2.0 | `qwen_image_edit_2509_fp8_e4m3fn.safetensors` 20.4 GB, `qwen_image_edit_2511_fp8mixed.safetensors` 20.5 GB (`diffusion_models/`), `qwen_2.5_vl_7b_fp8_scaled.safetensors` (`text_encoders/`), `qwen_image_vae.safetensors` (`vae/`), optional `Qwen-Image-Lightning-4steps-V1.0.safetensors` (`loras/`) | tight: 20.4 GB fp8 weights + 7B TE → relies on offload (HF: Qwen-Image base 20B); works but slow on 3090 — **peak VRAM UNVERIFIED** | 1–3 images via `TextEncodeQwenImageEditPlus` | 2509 card: "natively supports… depth maps, edge maps, keypoint maps" as input images; Qwen-Image base also has InstantX Union ControlNet (canny/softedge/depth/pose) and DiffSynth patches | https://huggingface.co/Qwen/Qwen-Image-Edit-2509 ; https://huggingface.co/Qwen/Qwen-Image-Edit-2511 ; https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/tree/main/split_files/diffusion_models ; https://docs.comfy.org/tutorials/image/qwen/qwen-image-edit |
| **FLUX.1 Kontext dev** | **FLUX.1 [dev] Non-Commercial License** (12B) | `flux1-dev-kontext_fp8_scaled.safetensors` 11.9 GB (`diffusion_models/`), `clip_l.safetensors` + `t5xxl_fp8_e4m3fn_scaled.safetensors` (`text_encoders/`), `ae.safetensors` (`vae/`) | yes | multi via `FluxKontextMultiReferenceLatentMethod` / `ReferenceLatent` chaining (local `nodes_flux.py`) | no official ControlNet for Kontext; Flux ControlNet-Union-Pro-2.0 is itself under the non-commercial license | https://huggingface.co/black-forest-labs/FLUX.1-Kontext-dev ; https://huggingface.co/Comfy-Org/flux1-kontext-dev_ComfyUI/tree/main/split_files/diffusion_models ; https://docs.comfy.org/tutorials/flux/flux-1-kontext-dev |
| **OmniGen2** | Apache-2.0 (4B) | `omnigen2_fp16.safetensors` (`diffusion_models/`), `qwen_2.5_vl_fp16.safetensors` (`text_encoders/`), `ae.safetensors` (`vae/`) | yes (HF: ~17 GB on RTX 3090) | up to 2 images in the core template | none official | https://huggingface.co/OmniGen2/OmniGen2 ; https://docs.comfy.org/tutorials/image/omnigen/omnigen2 |
| **HiDream-E1.1** | MIT (17B) | `hidream_e1_1_bf16.safetensors` 34.2 GB + 4 TEs | only with fp8 weight_dtype cast; heavy | single image | none | docs above |

---

## 5. Pose / layout control and frame interpolation in ComfyUI core

### ControlNet nodes present in core (local v0.33.0; all in `nodes.py` / `comfy_extras/`)
- `ControlNetLoader`, `DiffControlNetLoader`, `ControlNetApply` (legacy), **`ControlNetApplyAdvanced`** (positive/negative, strength, start/end percent, optional vae) — `nodes.py` 865–960. Models go in `models/controlnet/` (also `models/t2i_adapter/`) — `folder_paths.py` line 38.
- **`SetUnionControlNetType`** (display "Set Union ControlNet Type") with types `auto, openpose, depth, hed/pidi/scribble/ted, canny/lineart/anime_lineart/mlsd, normal, segment, tile, repaint` — `comfy_extras/nodes_controlnet.py`, `comfy/cldm/control_types.py`.
- `ControlNetInpaintingAliMamaApply` — `nodes_controlnet.py`.
- **`ModelPatchLoader`** ("Load Model Patch", folder `models/model_patches/`) + **`QwenImageDiffsynthControlnet`** ("Apply Qwen Image DiffSynth ControlNet"), `ZImageFunControlnet`, `USOStyleReference` — `comfy_extras/nodes_model_patch.py`.
- Preprocessor-ish core nodes: `Canny` (`nodes_canny.py`), `SDPoseKeypointExtractor` / `SDPoseDrawKeypoints` / `SDPoseFaceBBoxes` (`nodes_sdpose.py`), `LoadDA3Model`/`DA3Inference` (Depth Anything 3), MediaPipe face landmarker. For DWPose/OpenPose detectors use custom node **`comfyui_controlnet_aux`** (registry id, publisher `fannovel16`, v1.1.5 2026-04-13; registry `license` field is empty `{}` — repo license Apache-2.0 **UNVERIFIED here**). — https://api.comfy.org/nodes/comfyui_controlnet_aux

### Official control paths per candidate model
- **Qwen-Image (base)**: InstantX `Qwen-Image-InstantX-ControlNet-Union.safetensors` (Apache-2.0; canny/soft-edge/depth/pose) → `models/controlnet/`, nodes ControlNetLoader → SetUnionControlNetType → ControlNetApplyAdvanced; DiffSynth patches `qwen_image_{canny,depth,inpaint}_diffsynth_controlnet.safetensors` (2.27 GB each) → `models/model_patches/`, nodes ModelPatchLoader → QwenImageDiffsynthControlnet; `qwen_image_union_diffsynth_lora.safetensors` (`models/loras/`) covers canny/depth/pose/lineart/softedge/normal/openpose. Templates locally: `image_qwen_image_instantx_controlnet.json`, `image_qwen_image_controlnet_patch.json`, `image_qwen_Image_2512_controlnet.json`. Whether the InstantX Union works with Qwen-Image-**Edit**: **UNVERIFIED** (HF card silent). — https://docs.comfy.org/tutorials/image/qwen/qwen-image ; https://huggingface.co/InstantX/Qwen-Image-ControlNet-Union ; https://huggingface.co/Comfy-Org/Qwen-Image-DiffSynth-ControlNets/tree/main/split_files/model_patches
- **Qwen-Image-Edit-2509**: pose/depth/edge control is *built in* — feed the keypoint/depth/edge map as one of the 1–3 input images ("natively supports common ControlNet conditions such as depth maps, edge maps, keypoint maps"). — https://huggingface.co/Qwen/Qwen-Image-Edit-2509
- **FLUX.1**: official BFL FLUX.1-Canny-dev / Depth-dev(-lora); community InstantX/Shakker Union-Pro(-2.0: canny, soft edge, depth, pose, gray; license flux-1-dev-non-commercial) → `models/controlnet/`. Not documented for Kontext. — https://docs.comfy.org/tutorials/flux/flux-1-controlnet ; https://huggingface.co/Shakker-Labs/FLUX.1-dev-ControlNet-Union-Pro-2.0
- **HiDream-O1**: skeleton/layout conditioning exists only in the upstream Python pipeline (README 2026-05-13); ComfyUI core exposes only reference images. Workaround: pass a pose-skeleton render as one of the reference images (behaviour **UNVERIFIED**).

### Frame interpolation
- **Core** (no install): `FrameInterpolationModelLoader` ("Load Frame Interpolation Model") + `FrameInterpolate` ("Run Frame Interpolation Model"), auto-detects **RIFE** (IFNet) or **FILM** state dicts; weights in **`models/frame_interpolation/`** (`folder_paths.py` line 61). Weights: HF `Comfy-Org/frame_interpolation` (license tag **MIT-AND-Apache-2.0**; 246 MB): `film_net_fp16.safetensors`, `rife_v4.25.safetensors`, `rife_v4.25_heavy.safetensors`, `rife_v4.25_lite.safetensors`, `rife_v4.26.safetensors`, `rife_v4.26_heavy.safetensors` (file list via search snippet — sizes **UNVERIFIED**). Template `utility_video_frame_interpolation.json`. Upstream licenses: Practical-RIFE **MIT**; Google FILM **Apache-2.0**. — local `comfy_extras/nodes_frame_interpolation.py`; https://docs.comfy.org/built-in-nodes/FrameInterpolationModelLoader ; https://huggingface.co/Comfy-Org/frame_interpolation ; https://github.com/hzwer/Practical-RIFE ; https://github.com/google-research/frame-interpolation
- **Custom node**: `ComfyUI-Frame-Interpolation` (Fannovel16) — registry id **`comfyui-frame-interpolation`**, v1.0.11 (2026-03-22), publisher `fannovel16`, 1.82 M downloads, registry `license` = `{"file":"LICENSE"}`; repo license **MIT**; supports RIFE 4.0–4.9, FILM, GMFSS, IFRNet, M2M, AMT, STMFNet, FLAVR, etc. Install: `comfy node install comfyui-frame-interpolation`. — https://api.comfy.org/nodes/comfyui-frame-interpolation ; https://github.com/Fannovel16/ComfyUI-Frame-Interpolation

---

## 6. ComfyUI Registry basics
- Identity: a node pack is identified by the `[project] name` in `pyproject.toml` — "Unique identifier for your node. Immutable after creation" — plus `[tool.comfy] PublisherId`. Versions are **semver** (`[project] version`), and "published versions are immutable". — https://docs.comfy.org/registry/overview ; https://docs.comfy.org/registry/publishing ; https://docs.comfy.org/registry/specifications
- License metadata: `[project] license` is **optional** ("Not required for registry publication"), given as `{ file = "LICENSE" }` or `{ text = "MIT License" }`. The public API exposes it verbatim, e.g. `GET https://api.comfy.org/nodes/comfyui-impact-pack` → `"license": "{\"file\": \"LICENSE.txt\"}"` — i.e. the registry publishes only what the author declared (often just a file pointer, sometimes empty `{}`), so **license must be resolved from the repo for compliance**. The registry web UI (registry.comfy.org) is a JS SPA and could not be inspected for a license display (**UNVERIFIED**). — https://api.comfy.org/nodes/comfyui-impact-pack ; https://api.comfy.org/nodes/comfyui-frame-interpolation
- Security: registry scans for "malicious behaviour such as custom pip wheels, arbitrary system calls"; standards forbid `eval/exec`, runtime pip installs, obfuscated code. — https://docs.comfy.org/registry/overview ; https://docs.comfy.org/registry/standards
- Publishing: `comfy node init` → fill `pyproject.toml` → `comfy node publish` (API key `REGISTRY_ACCESS_TOKEN`; GitHub Action `publish_action.yml`). — https://docs.comfy.org/registry/publishing
- Installation path: `comfy node install <id>` → ComfyUI-Manager `cm-cli`, which "resolves them through the Manager's channel database" (Manager "Officially supports https://registry.comfy.org/"); `comfy node registry-install <node_id>` hits the Registry API directly and bypasses Manager. So: **registry IDs are the default identifier, but resolution goes through Manager's catalog by default, not the registry API.** — README; https://github.com/Comfy-Org/ComfyUI-Manager

---

## Decisions this research supports
1. **HiDream-O1-class is real and viable**: target `hidream_o1_image_dev_fp8_scaled.safetensors` (8.07 GB, MIT) in `models/checkpoints/` with core nodes `CheckpointLoaderSimple → CLIPTextEncode → HiDreamO1ReferenceImages (1–10 refs) → SamplerCustom → VAEDecode`; Dev = 28 steps, CFG 1.0. Fits 24 GB. Skip the Gemma4 prompt-enhancement subgraph unless needed (saves 9 GB download and VRAM).
2. **Licensing**: HiDream-O1 (MIT), Qwen-Image-Edit (Apache-2.0), OmniGen2 (Apache-2.0) are commercial-safe; FLUX.1 Kontext dev and Flux ControlNet-Union are non-commercial — exclude from a commercial pipeline. ComfyUI/comfy-cli are GPL-3.0 (fine to use as tools; ship no derived code without GPL compliance); comfy-mcp is AGPL-3.0-or-later OR Commercial — avoid embedding/serving it in a product unless the AGPL is acceptable.
3. **Pose/layout control**: if pose conditioning is a hard requirement, Qwen-Image-Edit-2509/2511 (keypoint map as an input image) or Qwen-Image + InstantX Union / DiffSynth pose LoRA is the only *documented* open path in core; HiDream-O1 has no ComfyUI pose node today.
4. **Automation surface**: drive ComfyUI via `comfy run --workflow <api.json> --wait --json` (NDJSON events + final envelope) or directly via `POST /prompt` + `ws://host:8188/ws?clientId=` + `GET /history/{prompt_id}` + `GET /view`. Use `comfy workflow validate` (not the deprecated `comfy validate`). Pin the environment with `comfy node save-snapshot --output <file.yaml>`.
5. **Frame interpolation**: use core `FrameInterpolationModelLoader`/`FrameInterpolate` with `rife_v4.26.safetensors` (MIT) — no custom node needed.
6. **Model downloads**: `comfy model download --url https://huggingface.co/Comfy-Org/HiDream-O1-Image/resolve/main/checkpoints/hidream_o1_image_dev_fp8_scaled.safetensors --relative-path models/checkpoints` (HF_API_TOKEN only needed for gated repos).

## Open questions / UNVERIFIED
- Peak VRAM of HiDream-O1 fp8/bf16 at 2048² with many reference images on a 24 GB card (no official figure; PR comment cites 17–20 GB for bf16).
- Whether `resolve/main` download URLs for the Comfy-Org HiDream-O1 repo require an HF token (repo appeared public; E1 repo returned 401).
- Exact sizes of `Comfy-Org/frame_interpolation` files (list came from search snippet, not the tree page).
- Whether InstantX Qwen-Image ControlNet-Union is compatible with Qwen-Image-Edit-2509/2511.
- Whether passing a skeleton image as a HiDream-O1 reference yields pose control in ComfyUI (no docs).
- `comfyui_controlnet_aux` license (registry field empty; repo not fetched).
- Registry web UI license display (SPA not renderable via fetch).
- fp8 variant of HiDream-E1.1 (HF repo gated, 401).
- comfy-cli 1.18.0 vs local 1.17.0: no command-level differences found in release notes, but not exhaustively diffed.
