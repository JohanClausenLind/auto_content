# GPU hosts

How to add a second machine that generates frames alongside this one, and how to share a single
card with another program that has priority over rendering.

Verified end to end on 2026-09-09 between **vegaserv** (Ubuntu 24.04, RTX 3090, driver 595.84) and
**nova** (Ubuntu 26.04.1, RTX 3090, driver 595.91.07, 20 cores, 76 GB RAM), 1 GbE, measured
107 MB/s host to host.

## Why a second host, and what it can actually take

Wall clock in this pipeline is almost entirely **N independent GPU items**: 100–385 s per HiDream
anchor or drawing (a thirty-drawing film is 52 min – 2.75 h), 40–74 s per LTX clip with tens of
clips in a shot list, 12 s/frame for SeedVR2. Against that, a Remotion render is 13.5 s and compose
is 5 s. So the only parallelism worth building is per-item, and the only items worth spreading are
the ones a model spends minutes on.

The backends split into two kinds, and only one kind can move:

| | Contract | Can it run on another host? |
| --- | --- | --- |
| **HiDream** (`sequences/hidream_backend.py`) | base64 PNG in and out | **Yes** — no path ever crosses the wire |
| **ComfyUI** (`comfyui/client.py`) — LTX-2.5, FLUX.2, Wan, Krea2 | multipart upload + `/view` download; path injection is *refused* at `client.py:114` | **Yes** for the media; weights must be managed on the far side |
| Post chain (Cutie/ProPainter/SeedVR2/RIFE/GIMM-VFI), MMAudio, TTS, Blender | a `job.json` of **absolute local paths** (`postchain/runner.py:31`) | **No** — needs a worker on that host, not an endpoint |

A sequence's frames are hub-and-spoke: each is an edit of the *anchor*, never of its neighbour, and
`drift_report` measures each against the *anchor*. Nothing in a frame depends on another frame, and
each already carries its own `input_hash` marker — which is why splitting them across cards changes
the wall clock and nothing else.

---

# Part 1 — Adding a GPU host

Steps 1–4 need root on the new machine. The rest can be driven over SSH from the primary host.

## 1. SSH, and the driver

```bash
ssh-copy-id you@<new-host>
ssh you@<new-host> 'modinfo -F license nvidia; nvidia-smi --query-gpu=name,driver_version --format=csv; mokutil --sb-state'
```

`modinfo` **must print `NVIDIA`.** If it prints `Dual MIT/GPL` you are on the `-open` module, which
has a GSP firmware bug that hard-freezes these boxes under load — it cost this project a hard reboot
once. Install the proprietary metapackage instead (`nvidia-driver-<ver>`, no `-open` suffix) and
remove the open one. A clean Ubuntu install is exactly where `ubuntu-drivers autoinstall` picks
`-open`, because Ampere is on its supported list.

If Secure Boot is **enabled**, DKMS-built modules will not load; use Canonical's pre-signed
`linux-modules-nvidia-<ver>-$(uname -r)` and skip the MOK enrolment.

## 2. Storage, at the identical path

The weight store must be mounted at **`/mnt/fast`** — the same absolute path as on every other host.
Five places hardcode it with no environment escape: `shots/planner.py`, `cli/workflows_cmd.py`,
`controls/blender.py`, `reference/build.py`, `skills/video/postchain/common.py`. A different mount
point means patching all five, so don't.

```bash
sudo mkdir -p /mnt/fast
sudo mount -o ro /dev/sdXN /mnt/fast && ls -la /mnt/fast   # look before committing the disk
sudo umount /mnt/fast
echo "UUID=$(sudo blkid -s UUID -o value /dev/sdXN) /mnt/fast ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab
sudo mount /mnt/fast && sudo chown "$USER:$USER" /mnt/fast
```

Budget ~300 GB: 278 GB of weights plus ~13 GB of checkouts-with-venvs.

## 3. System packages

```bash
sudo apt install -y ffmpeg git-lfs build-essential python3-dev libcairo2-dev libpango1.0-dev tmux
git lfs install
node -v || (curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash - && sudo apt install -y nodejs)
```

`setup.sh` hard-fails without ffmpeg/ffprobe and enforces Node major 22 or 24 exactly.

**A GPU host does not need Docker or pnpm.** Those exist for the compose stack (Postgres, Temporal)
and the web build, which live on the control plane. Running the full `./setup.sh` on a worker would
install and start a database it will never read. Do this instead:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh          # setup.sh's only real prerequisite install
git clone <repo> ~/git/auto_content && cd ~/git/auto_content
cp .env.example .env
uv sync --frozen
```

Ubuntu 26.04 ships a Python newer than the repo's `>=3.12,<3.13`; that is fine, `uv` fetches its own
3.12. It matters later only because the HiDream flash-attn wheel is `cp312`-specific.

## 4. Weights

**Mirror, don't re-download.** The store is content you already have, and some of it cannot be
fetched by script at all.

```bash
# from the host that has them; ~45 min for 278 GB at 107 MB/s. Use tmux.
rsync -aHAX --info=progress2 --partial /mnt/fast/models/  <new-host>:/mnt/fast/models/
rsync -aHAX --info=progress2 --partial ~/git/auto_content/models/  <new-host>:~/git/auto_content/models/
```

The second is the 52 KB category symlink index; its links resolve on the new host because the store
sits at the same path. Check with `find models -xtype l` — that must print nothing.

**What a model download does *not* give you.** Several weights are not on Hugging Face and are
skipped silently by an `hf`-based pull:

| Weight | Source |
| --- | --- |
| `cutie/cutie-base-mega.pth` | a GitHub **release** (sczhou/ProPainter v0.1.0) |
| `Practical-RIFE/train_log/flownet.pkl` | Google Drive links in the upstream README |
| SAM 3.1 | manually gated by Meta |

The RIFE weights live *inside the checkout*, not in the store, so mirror
`external/Practical-RIFE/train_log/` separately (24 MB).

## 5. Source checkouts

A weight download gives you none of these, and this is the step that produces
"cutie is missing": the `.pth` is present, the *code* is not. Clone at the same commits the working
host runs:

| Directory | Repo | Commit |
| --- | --- | --- |
| `Cutie` | github.com/hkchengrex/Cutie | `ec5cdd4` |
| `ProPainter` | github.com/sczhou/ProPainter | `e870e79` |
| `GIMM-VFI` | github.com/GSeanCDAT/GIMM-VFI | `dbc5644` |
| `Practical-RIFE` | github.com/hzwer/Practical-RIFE | `bbfd2ea` |
| `seedvr2_videoupscaler` | github.com/numz/ComfyUI-SeedVR2_VideoUpscaler | `4490bd1` |
| `MMAudio` | github.com/hkchengrex/MMAudio | `974010a` |
| `hidream-o1-code` | github.com/HiDream-ai/HiDream-O1-Image | `2c2d29f` |
| `whisperX` | github.com/m-bain/whisperX | `2cfd7b7` |

`git -C external/<dir> remote get-url origin` on a working host prints the current set; several are
recorded there with `git@github.com:` URLs, which need the https form on a machine without GitHub
SSH keys. Source only — 377 MB for all eight.

## 6. Environments

```bash
just setup-postchain          # all six: cutie propainter gimm_vfi rife seedvr2 mmaudio
uv sync --project skills/image/hidream
```

`setup_envs.sh` refuses with a named path if a checkout is absent, rather than failing mid-install.
Each tool gets its own venv and its own Python (RIFE is 3.11 because it pins `numpy<=1.23.5`, which
has no 3.12 wheel; GIMM-VFI is 3.10). All six should land on the same torch build:

```bash
for r in Cutie ProPainter GIMM-VFI Practical-RIFE seedvr2_videoupscaler MMAudio; do
  printf '%-24s ' "$r"; external/$r/.venv/bin/python -c 'import torch;print(torch.__version__, torch.cuda.is_available())'
done
# → 2.14.0+cu130 True  (×6)
```

**flash-attn must be installed by hand, and re-installed after every `uv sync`.** These hosts have
no `nvcc`, so the wheel is prebuilt and `uv sync` removes it as an undeclared package. This is the
single most common way a HiDream host comes up broken:

```bash
uv pip install --python skills/image/hidream/.venv/bin/python \
  "https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3.post1/flash_attn-2.8.3.post1+cu12torch2.8cxx11abiTRUE-cp312-cp312-linux_x86_64.whl"
skills/image/hidream/.venv/bin/python -c 'import flash_attn; print(flash_attn.__version__)'
```

## 7. ComfyUI (only if the host is to serve clips or FLUX.2 anchors)

A ComfyUI *checkout* is not a ComfyUI install. It needs a venv, the GGUF custom node, and the weight
links — none of which come with `git clone`, and without which every LTX / FLUX.2 / Wan job fails
validation because the loaders do not exist.

```bash
cd ~/git/ComfyUI
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  --index-url https://download.pytorch.org/whl/cu130 --extra-index-url https://pypi.org/simple \
  torch torchvision torchaudio
uv pip install --python .venv/bin/python -r requirements.txt

# LTX-2.5, FLUX.2 and Wan are all GGUF: UnetLoaderGGUF / CLIPLoaderGGUF live here, and the
# package pins the commit (ltx_packages.py → custom_nodes PinnedNode).
cd custom_nodes && git clone https://github.com/city96/ComfyUI-GGUF.git \
  && git -C ComfyUI-GGUF checkout 6ea2651 \
  && uv pip install --python ../.venv/bin/python -r ComfyUI-GGUF/requirements.txt
```

The repo's version floor is `comfyui_min_version="0.33.0"` — a **minimum**, so a newer ComfyUI is
fine and does not need downgrading.

Then mirror the weight links. `services/local.link_required_models` does this automatically when the
control plane starts ComfyUI itself; with a remote instance and `link_models=false` it must be done
on the far side. Generate them from a working host rather than by hand:

```bash
# on the host that already works
cd ~/git/ComfyUI && find models -maxdepth 2 -type l -printf '%p\t%l\n' \
  | awk -F'\t' '{printf "mkdir -p \"$(dirname %s)\"; ln -sfn \"%s\" \"%s\"\n", $1, $2, $1}' > /tmp/links.sh
ssh <gpu-host> 'cd ~/git/ComfyUI && bash -s' < /tmp/links.sh
ssh <gpu-host> 'cd ~/git/ComfyUI && echo "$(find models -maxdepth 2 -type l | wc -l) links, $(find models -maxdepth 2 -xtype l | wc -l) broken"'
```

**Verify against the live catalogue, not by eye** — the check that matters is that every node the
packages actually submit exists on that server:

```bash
curl -s http://<tailnet-ip>:8188/object_info > /tmp/nodes.json
# every class_type in ltx_i2v_package().api_workflow must be a key in /tmp/nodes.json
# (16 of them for LTX i2v, including UnetLoaderGGUF and CLIPLoaderGGUF)
```

If you write that check yourself, make it **assert the want-list is non-empty first**. A typo in the
introspection returns zero nodes to check and then reports "missing: none", which reads exactly like
a pass.

**One tenant at a time.** ComfyUI idle holds no model, so it can sit alongside HiDream, but both
want the whole 24 GB the moment they generate. The `exclusive_gpu` arbitration in
`services/local.py` is local-only and does not reach a remote host, so do not enable both as
always-on units on the same card — start whichever the run needs.

## 8. Expose the servers on the tailnet

Bind to the host's **tailnet address, never `0.0.0.0`**. Neither server has any authentication and
both hand out a whole GPU; the tailnet is the only thing standing in front of them.

```bash
# on the GPU host
CF_HIDREAM_HOST=<tailnet-ip> CF_HIDREAM_PORT=8801 CF_HIDREAM_MODEL_TYPE=dev \
  PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  uv run --project skills/image/hidream python skills/image/hidream/server.py

# ComfyUI derives its bind from the endpoint URL already
python ~/git/ComfyUI/main.py --listen <tailnet-ip> --port 8188
```

`CF_HIDREAM_HOST` defaults to `127.0.0.1`, so exposure is always opt-in. Expect ~72 s to load the
dev checkpoint and ~17.3 GB resident.

**Make HiDream a systemd `--user` unit, not a `nohup`.** A backgrounded server dies with the machine
and takes its `/tmp` log with it, which is how the first attempt here vanished at the next reboot
with nothing to read. `~/.config/systemd/user/hidream.service`:

```ini
[Unit]
Description=HiDream-O1 image server (content-factory)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/<user>/git/auto_content
Environment=CF_HIDREAM_HOST=<tailnet-ip>
Environment=CF_HIDREAM_PORT=8801
Environment=CF_HIDREAM_MODEL_TYPE=dev
Environment=PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
ExecStart=/home/<user>/.local/bin/uv run --project skills/image/hidream python skills/image/hidream/server.py
Restart=on-failure
RestartSec=10
TimeoutStartSec=600

[Install]
WantedBy=default.target
```

```bash
sudo loginctl enable-linger <user>     # the one line needing root: run without being logged in
systemctl --user daemon-reload && systemctl --user enable --now hidream.service
journalctl --user -u hidream -f        # logs that survive reboots
```

Absolute paths in `ExecStart` are required — a user unit does not get your login `PATH`, so bare
`uv` will not be found. `TimeoutStartSec=600` exists because loading the checkpoint takes ~72 s and
systemd's 90 s default is uncomfortably close on a cold page cache. `Restart=on-failure` deliberately
does *not* restart after `systemctl --user stop`, because stopping it is how you hand the card over.

**Stop any manual instance before enabling the unit.** Running both is not additive: the first holds
the port and the VRAM, and the unit then crash-loops on `torch.OutOfMemoryError` while the manual
server keeps answering `/healthz` — so the service looks healthy and `NRestarts` climbs unnoticed.
Check `systemctl --user show hidream -p NRestarts` after enabling; it should be `0`.

## 9. Wire the pool

On the **control plane**, in `.env`:

```bash
CF__IMAGE_SEQUENCES__HIDREAM_ENDPOINTS='["http://127.0.0.1:8801","http://<tailnet-ip>:8801"]'
CF__IMAGE_SEQUENCES__FLUX2_ENDPOINTS='["http://127.0.0.1:8188","http://<tailnet-ip>:8188"]'
```

**Single-quote the JSON.** `.env` is sourced by bash, which eats the inner double quotes and hands
pydantic `[http://…,http://…]`; you get `SettingsError: error parsing value for field
"image_sequences"`, which does not mention quoting. Same convention as `CF__COMFYUI__EXTRA_MODEL_ROOTS`.

Each list **replaces** its singular field rather than extending it, so the local server must appear
in the list to keep a share of the work. Unset means one endpoint and the serial path, byte for byte.

Verify the pool resolves before running anything:

```bash
set -a; . ./.env; set +a
uv run python -c "from content_factory.config import get_settings; print(get_settings().image_sequences.hidream_pool())"
```

## 10. Verify

```bash
# 1. the far host agrees with this one about what it has
uv run content-factory video-stack                       # here
ssh <host> 'cd ~/git/auto_content && uv run content-factory video-stack'   # there — diff them

# 2. the far server is reachable and loaded
curl -sf http://<tailnet-ip>:8801/healthz                # {"loaded":true,...}

# 3. it really generates, through the repo's own client
#    (HiDreamReferenceEditBackend(endpoint=…).text_to_image(...) → ~31 s for 2048²)

# 4. a real run splits
just run-local picture-story
jq -r .served_by output/local-runs/*/deliverables/*/sequence/frames/*.done.json | sort | uniq -c
```

Step 4 is the one that matters: every frame marker records `served_by`, so two hosts in that output
is the proof. Watch `nvidia-smi` on both boxes during the run.

---

# Part 2 — Sharing one card with a higher-priority tenant

On a machine that also runs something else GPU-hungry — here, the flashcards agent at
`~/.openclaw/workspace/flashcards-agent` — rendering has to get out of the way. Its session LLM asks
`ensure_vram()` for `OLLAMA_VRAM_GB` plus a 1.5 GB margin (17.5 GB by default) and a Krea2 anchor
render is ~17.4 GB resident, so the two can never be co-resident. Its own `backend/vram.py` knows
only how to unload Ollama models, so when a render holds the card it runs out of options.

```bash
content-factory gpu status                                    # free VRAM, who holds it, what is parked
content-factory gpu yield --need-gb 17.5 --reason flashcards  # park the run; non-zero if it still cannot fit
content-factory gpu resume                                    # restart it from where it stopped
content-factory gpu resume --stale-after 5400                 # for a cron safety net
```

Parking reuses the stop the runner already survives: `stop_requested` is read at the next step
boundary, the report is written, finished stages stay on disk, and the registration's own `step` is
a node key that `run-local --from` accepts — so a resumed run continues as the film it was, with its
original `--style`/`--story`/`--subject`, not the workflow's defaults. A stage in flight can hold the
card for ten minutes, so `--deadline` (90 s) escalates to the signalling stop, still resumable from
the interrupted stage.

While a claim is held, `run_plan` refuses to start — priority that works one way is not priority. The
claim expires after `gpu_priority.MAX_CLAIM_HOLD_S` (2 h) because the other tenant is a separate
program that can crash, and `CF_IGNORE_GPU_CLAIM=1` overrides it deliberately.

**Hook it to demand, not to a clock.** The agent's 06:15 and 20:30 cron entries only send a Web Push
nudge — they touch no GPU. The demand arrives when somebody opens the app, so the call belongs in
`ensure_vram()`, which already computes exactly that. Preempting a six-hour film on a schedule
nobody consulted is how the film never finishes; a `*/15` heartbeat wired to preemption would make a
long render unfinishable.

---

# Things that will bite you

- **`external/` empty but weights present.** Reads as "cutie is missing" and looks like a download
  problem. It is not: the `.pth` is fine, the checkout and venv are absent. Part 1 steps 5 and 6.
- **flash-attn silently removed by `uv sync`.** The HiDream server then fails at load. Re-install the
  wheel after every sync.
- **Unquoted JSON in `.env`.** `SettingsError: error parsing value for field "image_sequences"`.
- **`pgrep -f` / `pkill -f` match your own command line.** The pattern you are searching for is
  itself in the argv of the shell running the search, so over SSH `pgrep -f "server.py"` reports a
  server that was never started, and **`pkill -f "server.py"` kills your own session** before it
  reaches the target. Both happened during this build. Two fixes, use them always:

  ```bash
  ss -ltn | grep :8801                      # to ask "is it up" — ask the port, not the process table
  pgrep -af "[s]kills/image/hidream/server.py"   # bracket the first char: the pattern cannot match itself
  systemctl --user stop hidream             # to stop a managed service — never pkill it
  ```

  The bracket works because `[s]kills…` is a regex matching `skills…`, while the literal text in
  your own argv is `[s]kills…`, which the regex does not match.
- **A different mount point than `/mnt/fast`.** Five modules hardcode it.
- **`ubuntu-drivers autoinstall` on a fresh Ubuntu picks `-open`.** Freezes the box under load.
- **Tests can leak into the live weight index.** A `models/frame_interpolation/GIMM-VFI` symlink was
  found pointing into `/tmp/pytest-of-*/…` on 2026-09-09 and mirrored to the second host before it
  was spotted. `find models -xtype l` after any rsync.
- **The control plane's own card may already be full.** Ollama holding a 13 GB model leaves no room
  for a local HiDream; the anchor stage's arbitration evicts it, which is designed behaviour but
  surprising the first time.

# Not distributed yet

`generate_video`'s clip loop (`stages.py`) still runs one clip at a time against
`CF__COMFYUI__ENDPOINT`. Its per-clip body calls `_ensure_backend_ready(backend, warmed)` against a
shared mutable set, reaching `services/local.ensure_service`, which with `auto_start=true` will
`Popen` and `pgrep`-kill GPU servers; fanning that out before the start/stop arbitration is
thread-safe is how two threads end up thrashing one card. It needs a `_video_backends` pool
mirroring `_reference_backends`, the same prepare/dispatch/collect split, and per-host readiness
instead of one `warmed` set.

The post chain, MMAudio, TTS and Blender are not endpoint-addressable at all — see the table at the
top. Distributing those means a second Temporal worker plus shared storage, which the artifact store
does not yet provide: it is a write-only publishing sink (11 `put_*` calls against 146 `ctx.ddir()`
path reads), so stages hand each other **local file paths**, not artifact keys.
