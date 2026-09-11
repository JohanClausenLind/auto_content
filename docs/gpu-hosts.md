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

---

# Part 3 — The second host as a producer, and bringing the work home

Parts 1 and 2 make a second host *draw*. That is only half of what a card can do, and the smaller
half: HiDream is endpoint-addressable so its output never touches the far disk, but the post chain,
MMAudio, the TTS skills, Blender and ffmpeg all take absolute local paths (the table at the top),
so the only way to use a second card for those is to run whole lanes there. Then the finished work
is on the wrong machine.

## What comes home, and what does not

Only what the lane actually delivers. `compile_destination_packages` already writes
`deliverables/<id>/destination-packages/packages.json` naming every shippable file with its role,
its deliverable-relative path and its **sha256** — so the harvest reads a manifest a stage
produced rather than inventing a second idea of what a lane delivers, and verifies every byte it
receives against it. Measured on the first real pull, `o04-single-clip-post` from nova:

```
47 files / 8.0 MB on nova   ->   11 files / 3.7 MB here
```

The frames, the control passes and the chain steps stay where they were made. Alongside the
package come a few hundred KB of **evidence** (`run.json`, `qc/report.json`, `sequence/chain.json`,
the frame-review batch and verdict), so a harvested run is diagnosable rather than only watchable.

The direction is always **pull**. The control plane reaches the worker; a worker is never given
write access to this machine's run directories, and never needs a key here.

```bash
# .env on the control plane. Single-quote the JSON, same trap as the endpoint lists above.
CF__REMOTE__HOSTS='[{"name":"nova","ssh":"nova@100.82.150.94","runs_root":"output/runs"}]'
```

```bash
just remote-list                 # what each host has; one ssh per host, not a byte of media
just harvest --dry-run           # names what would come home
just harvest                     # fetch, verify every digest, land, link the film into videos/
just harvest --limit 0 --interval 60    # keep going; a pass that finds nothing costs one ssh
```

A harvested run lands at `output/harvest/<host>/<slug>/` and carries a `harvest.json` sidecar
written **last** — origin host, origin path, the far host's git rev, the digest, `qc_passed`. That
sidecar is the only record: there is no ledger, "already here" is derived from it, a directory with
bytes and no sidecar is a harvest that failed part way and gets redone, and
`rm -rf output/harvest/nova/<slug>` is a complete undo.

## Before nova can produce anything worth harvesting

1. **Code parity.** A worker one commit behind cannot run the current stages at all. `git pull`,
   `uv sync --frozen`, then **re-install the flash-attn wheel** — `uv sync` removes it every time
   (step 6 above). The far host's rev is recorded in every `harvest.json`, so drift is visible
   after the fact rather than guessed at.
2. **A real backend selection in the far host's `.env`.** A stock `.env.example` runs every lane on
   **mock** backends, and mock output still writes a valid `packages.json` — so it would be
   harvested and filed as a real deliverable. Set what `run-local`'s docstring names:
   `CF__IMAGE_SEQUENCES__BACKEND=hidream`, `CF__VIDEO__BACKEND=comfyui`,
   `CF__NARRATION__TTS=qwen3tts`, `CF__CONTROLS__COMPILER=blender`,
   `CF__COMFYUI__EXTRA_MODEL_ROOTS='["/mnt/fast/models"]'`. Look at the first harvested film; do
   not just read its exit code.
3. **Decide who owns the card.** A host cannot be a producer and a drawing endpoint at full speed
   at once: its HiDream server is single-worker, so the control plane's frames queue behind the
   worker's own run and the server looks wedged (it is not — it is busy). While the far host
   produces, take it out of `CF__IMAGE_SEQUENCES__HIDREAM_ENDPOINTS` here.
4. **Unique run names.** `run-local` defaults its project directory to
   `output/local-runs/<workflow>` — the *same path for every run of a lane* — and `deliverable_id`
   is the constant `dlv_short0000001`. Two hosts running `image-set` produce byte-identical paths.
   Use `--project-dir output/runs/<slug>`, the convention `output/overnight/` already follows.

## The human review gate is where an image lane actually stops

Worth knowing before you go looking for deliverables that are not there. `image-set` runs
`drift -> frames_gate -> package -> qc -> pack`, and `frames_gate` (`review_frames`) **blocks**: a
verdict binds to the digests of the exact images reviewed. So a finished-looking image set ends
with `passed: false` and **no `destination-packages/` at all** — six of seven overnight image sets
sat exactly there.

`remote list` reports those as `awaiting review` rather than staying silent about them, because
silence reads as "there is nothing to collect". Getting them the rest of the way still means
reviewing on the host that drew them (`content-factory frames review`, then
`run-local --from package`); shipping the review material here and the verdict back is not built.

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
- **The distro Blender has no bundled OpenImageIO.** The control passes are read back out of
  multilayer EXR with Blender's own OIIO, and `controls.blender_bin` defaults to the bare name
  `"blender"`, so **PATH decides which Blender runs** — and `/usr/bin` sits far ahead of `/snap/bin`.
  Measured on these hosts: `/usr/bin/blender` (apt) is 4.0.2 on vegaserv and 5.0.1 on nova, **both
  without OpenImageIO**; the snap is 5.2.1 LTS **with** OIIO 3.1.13.1. `content-factory video-stack`
  reports Blender READY either way, because it only checks that a binary exists — so this passes
  every check and then dies inside Blender's Python. Install the snap and pin the path:

  ```bash
  sudo snap install blender --classic
  # .env, on every host that compiles control passes
  CF__CONTROLS__BLENDER_BIN=/snap/bin/blender
  ```

  Verify the binary, not the package: `blender --background --python-expr "import OpenImageIO"`.
- **A different mount point than `/mnt/fast`.** Five modules hardcode it.
- **`rsync --files-from` treats a missing name as an error**, exits 23, and does it *after*
  transferring everything else — so the first real harvest refused a run whose files had all
  arrived. The evidence list is optimistic by design (which of `sequence/chain.json`,
  `reviews/frames/*` exists depends on the lane), so `--ignore-missing-args` is mandatory. Missing
  *manifest* files are still caught, by the digest check on arrival.
- **`rsync -a` preserves symlinks, and sha256 follows them.** A symlink that arrived as a symlink
  would be hashed *through* — reading a local file and calling it harvested. `--safe-links` on the
  wire, and a refusal before hashing on this side.
- **A file list is newline-delimited unless you ask otherwise**, and `DeliveryFile` permits a
  newline in a path. `--from0` closes the class.
- **The repo is on `/home` (465 GB free), not on `/` (30 GB).** `df -h .`, not `df -h /` — the
  wrong one has been used to argue for a design decision.
- **`ubuntu-drivers autoinstall` on a fresh Ubuntu picks `-open`.** Freezes the box under load.
- **Tests can leak into the live weight index.** A `models/frame_interpolation/GIMM-VFI` symlink was
  found pointing into `/tmp/pytest-of-*/…` on 2026-09-09 and mirrored to the second host before it
  was spotted. `find models -xtype l` after any rsync.
- **The control plane's own card may already be full.** Ollama holding a 13 GB model leaves no room
  for a local HiDream; the anchor stage's arbitration evicts it, which is designed behaviour but
  surprising the first time.

# When a card falls off the bus

Measured on vegaserv, 2026-09-10 03:12, during an LTX-2.5 22B generation:

```
pcieport 0000:00:01.0: PCIe Bus Error: severity=Uncorrectable (Non-Fatal), TLP UnsupReq
nvidia 0000:01:00.0: AER: can't recover (no error_detected callback)
NVRM: Xid (PCI:0000:01:00): 79, GPU has fallen off the bus.
NVRM: Xid (PCI:0000:01:00): 154, GPU recovery action changed from 0x0 (None) to 0x2
                                 (Node Reboot Required)
```

**Xid 79 is a reboot, not a restart.** The driver says so itself in Xid 154, and `nvidia-smi -r`
cannot reset a device the desktop compositor and any other tenant still hold open. It is a
hardware-side event — PCIe link, power delivery, riser, thermals — provoked by a heavy sustained
load, and no amount of restarting a model server touches it.

How to tell, and what the pipeline does about it:

```bash
nvidia-smi                       # "Unable to determine the device handle ... No devices were found"
journalctl -k | grep -E 'Xid|fell off the bus'
```

`_ensure_backend_ready` — the moment before a run puts weights on the local card — checks this and
refuses with a `BlockedError` (exit 5) that names Xid 79 and the reboot. Without it the failure
arrives later wearing someone else's clothes: ComfyUI answers `All connection attempts failed`, and
SeedVR2 places its VAE on the CPU and dies on `device cpu:0 is invalid`. A queue will otherwise
spend every remaining job discovering the same thing.

Until the reboot, the work that still runs is anything pointed at the other host
(`CF__IMAGE_SEQUENCES__HIDREAM_ENDPOINTS='["http://<other>:8801"]'`) and anything on the mock
backends. The TTS, the post chain, MMAudio and Blender are not endpoint-addressable, so the lanes
that need them wait for the reboot — see the table at the top.

## Why it presents as "the whole PC froze"

Reconstructed from `journalctl -b -1` after the 06:07 reboot. The GPU dying is the first event,
not the visible one; three things follow it in the same second and it is the second and third
that make the machine unusable.

1. **03:12:22.881** — ComfyUI's log stops mid-sampling. It had LTXAV fully resident
   (`loaded completely; 18708.18 MB usable, 16204.69 MB loaded`) and was on step 0 of 8. Its last
   words are `CUDA error: unspecified launch failure` → `Fatal Python error: Aborted`. That is
   the *consequence*: `cudaErrorLaunchFailure` is what the runtime reports when the device
   disappears under a running kernel.
2. **03:12:22 → 03:12:29** — `nvidia-modeset` floods the kernel log:
   **190,960 lines of `ERROR: GPU:0: Failed to query display engine channel state` in seven
   seconds**, about 27,000 a second. `systemd-journald` cannot keep up and logs ~171,000
   `Missed N kernel messages` of its own. This is the moment the machine visibly locks up.
3. **03:12:22 onwards** — `Xorg` (pid 1897) goes to state **R and spins forever inside the dead
   driver, holding the `nvidia_modeset` semaphore**. The kernel names it explicitly:

   ```
   INFO: task nvidia-modeset/:943 blocked for more than 122 seconds.
   INFO: task nvidia-modeset/:943 blocked on a semaphore likely last held by task Xorg:1897
   INFO: task Discord:149253 blocked on a semaphore likely last held by task Xorg:1897
   ```

   Both blocked tasks were still blocked at 614 seconds, when the kernel gave up reporting
   (`Future hung task reports are suppressed`). The display server never came back.

So the screen freezes and the keyboard does nothing from 03:12, **and everything that is not the
display keeps working**: the journal shows normal service activity for the next three hours, load
average stayed between 3.5 and 7.3 on 24 cores, and an agent session went on running shell
commands, rendering with Remotion and writing files until the power cycle at 06:06:08 (the
journal ends mid-line — no clean shutdown).

**What it was not.** Worth writing down because these are the first things anyone checks: memory
peaked at **31 %** and never came near it again (20-23 % for the rest of the night), there were
**zero** OOM kills, disks were at 72 % and 41 %, no thermal event, no soft/hard lockup, no panic.
And it is not a recurring fault: across the five boots the journal still holds — including one of
43 days and one of six weeks — this is the **only** occurrence of either `AER: Uncorrectable` or
`fallen off the bus`.

**The load it happened under.** One heavy tenant, not two — and it is worth being exact,
because the obvious guess is wrong. ComfyUI reports `Total VRAM 24117 MB` and saw
`18708.18 MB usable` immediately before loading LTX-2.5, and that figure is steady across all
eight of its loads that night. So about **5.4 GB was held by the desktop** (Xorg, KDE, Discord)
and nothing else: `exclusive_gpu` did its job and no second model server was resident. The card
was carrying ~16.2 GB of LTX-2.5 plus ~5.4 GB of desktop, roughly 21.6 of 24 GB, under sustained
sampling in the eighth hour of a continuous run.

The two later CUDA failures are wreckage, not causes, and the clock proves it: the AER is at
**03:12:22**, SeedVR2 (`m12-video-finish`) started at **03:12:24** and failed `exit 3`, and the
local HiDream server was launched by the next job's `generate_anchor` and died in
`caching_allocator_warmup` → `RuntimeError: CUDA unknown error` at **03:12:48**. Everything
after 03:12:22 met a card that was already gone.

## Making it less likely, and making it visible

Nothing in this repo can stop a card leaving the PCIe bus — it is a link and power event, below
anything software touches. And there is no single published fix: every account of this failure
that ends in "solved" was solved by a *different* cause. That is the important finding, so it
goes first.

### What other people found

| case | what did **not** help | what fixed it |
| --- | --- | --- |
| [3090, Linux, Xid 79 under load (Level1Techs)](https://forum.level1techs.com/t/3090-keeps-falling-off-the-bus-xid-79-on-linux/244958) | forcing Gen3, disabling ASPM, two different PSUs, drivers 550 / 575 / 590, power limiting | **locking clocks** — `nvidia-smi -lgc 800,1600`, stable 4+ days |
| ["GPU has fallen off the bus" (Arch, Solved)](https://bbs.archlinux.org/viewtopic.php?id=304020) | — | `nvidia.NVreg_EnableGpuFirmware=0` **+** `pcie_aspm=off`, plus cleaning and rewiring GPU power |
| [5090, Xid 79 under sustained CUDA load (NVIDIA forums)](https://forums.developer.nvidia.com/t/bug-report-fix-rtx-5090-xid-79-gsp-firmware-crash-under-sustained-cuda-load/369440) | `pcie_aspm=off`, `NVreg_EnableGpuFirmware=0`, `nvidia_drm.fbdev=1`, three driver branches | **case airflow** — harder fan curve, higher-CFM intakes, an extra fan under the card |
| [Xid 79 while idle with ASPM in UEFI (NVIDIA forums)](https://forums.developer.nvidia.com/t/nvidia-driver-xid-79-gpu-crash-while-idling-if-aspm-l0s-is-enabled-in-uefi-bios-gpu-has-fallen-off-the-bus/314453) | — | disabling **L0s** in BIOS; that reporter's conclusion was *"ASPM L1 mode is perfectly okay but L0 and L0s do not work correctly"* |
| [3090 during model training (NVIDIA forums)](https://forums.developer.nvidia.com/t/xid-79-gpu-has-fallen-off-the-bus-training-a-deep-learning-model-on-nvidia-3090/267115) | ruled out software, memcheck, temperature, a 1500 W PSU | unresolved |

Read together: ASPM, GSP, power and cooling each fixed exactly one of these and each failed in at
least one other. Anyone who tells you which one it is without having measured your machine is
guessing.

### What that means for *this* host

**ASPM is a weaker suspect here than it first looked.** `sudo lspci -vv` shows
`LnkCtl: ASPM L1 Enabled` on both the root port and the card, with the L1 substates off
(`L1SubCtl1: PCI-PM_L1.2- ASPM_L1.2-`) and **no L0s**. The one thread that pinned Xid 79 on ASPM
pinned it on L0s specifically and called L1 fine. Still worth turning off — it is free and
reversible — but it should not be first.

**GSP firmware is the better-supported candidate**, because this host has form. `nvidia-smi -q`
reports `GSP Firmware Version: 595.84`, so GSP is live, and the reason this machine runs the
proprietary module rather than `-open` is an earlier GSP-related freeze. The proprietary module
can run without GSP; the open module cannot, which is why `NVreg_EnableGpuFirmware=0` is reported
as a no-op by open-module users.

**Thermals cannot be ruled in or out, and that is a gap, not an answer.** The 5090 case above was
airflow, and the 3090's known weak point is the GDDR6X on the back of the board — but this driver
reports `Memory Current Temp: N/A` for consumer cards and `lm-sensors` is not installed, so *no
thermal data exists for the night at all*. Saying "no thermal event" only means the kernel logged
no ACPI thermal trip and the driver logged no slowdown; the memory junction was never sampled.

**The 5GT/s in `LnkSta` is not a fault.** `content-factory gpu watch` catches the link stepping
2 → 3 as the card moves P5 → P3: the driver parks the link at idle. `hostmax 3` on a 12th-gen x16
slot is a BIOS setting worth a look, but it is not this.

### Change one thing at a time, in this order

The point of the ordering is that each step is cheap, reversible, and *distinguishable in the
telemetry* from the ones before it. Applying all four at once buys stability with no knowledge.

**1. Start recording** (do this first, whatever else you do):

```bash
mkdir -p ~/.config/systemd/user
tee ~/.config/systemd/user/cf-gpu-telemetry.service >/dev/null <<'UNIT'
[Unit]
Description=Sample GPU temperature, power, clocks, throttle reasons and PCIe errors

[Service]
Type=simple
WorkingDirectory=%h/git/auto_content
ExecStart=%h/.local/bin/uv run content-factory gpu watch --interval 10
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now cf-gpu-telemetry.service
loginctl enable-linger "$USER"      # so it survives logout
```

It appends to `.services/gpu-telemetry.csv` (header + ~110 bytes a row, one rotation kept) and
prints once to stderr the first time a non-fatal PCIe error appears. If the card goes again, the
minute before it is on disk.

**2. Close the thermal blind spot.** `sudo apt install lm-sensors && sudo sensors-detect --auto`
gets CPU, board and NVMe temperatures. GDDR6X junction temperature needs the out-of-tree
[`gddr6`](https://github.com/olealgoritme/gddr6) module; on a 3090 that number is the one most
worth having, because it is both the hottest part of the card and the one nothing else reports.

**3. Lock the clocks** — the only remedy that worked in the closest-matching case, a 3090 on
Linux:

```bash
sudo nvidia-smi -pm 1
sudo nvidia-smi -lgc 800,1600      # undo with: sudo nvidia-smi -rgc
```

**4. Cap the power** and make it persistent. A 3090 at its stock 350 W ceiling draws microsecond
transients well above it:

```bash
sudo nvidia-smi -pl 300
sudo tee /etc/systemd/system/nvidia-power-cap.service >/dev/null <<'UNIT'
[Unit]
Description=Cap the GPU power limit below the stock ceiling
After=nvidia-persistenced.service
Wants=nvidia-persistenced.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/nvidia-smi -pm 1
ExecStart=/usr/bin/nvidia-smi -pl 300

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl enable --now nvidia-power-cap.service
```

**5. Kernel parameters**, both at once since they are one reboot:

```bash
sudo cp /etc/default/grub /etc/default/grub.bak
# append to GRUB_CMDLINE_LINUX_DEFAULT:  pcie_aspm=off nvidia.NVreg_EnableGpuFirmware=0
sudo update-grub && sudo reboot
```

Confirm GSP actually went away — `nvidia-smi -q | grep "GSP Firmware"` should say `N/A`, not a
version. If `doctor` still warns about ASPM afterwards the firmware is holding ASPM control: set
**Native ASPM** and **PEG ASPM** to Disabled in the BIOS.

### What `doctor` holds you to

`content-factory doctor` carries three checks, none of which needs root:

| check | what it reads | when it complains |
| --- | --- | --- |
| `gpu_pcie_health` | `aer_rootport_total_err_{cor,nonfatal,fatal}` on the card's root port | **fail** on any fatal or non-fatal error since boot — one non-fatal was this whole event; **warn** past 100 correctable, which is a link on its way out |
| `gpu_aspm` | `/proc/cmdline` and `/sys/module/pcie_aspm/parameters/policy` | until `pcie_aspm=off` or the `performance` policy is in force |
| `gpu_power_cap` | `nvidia-smi --query-gpu=power.limit,power.max_limit` | while the limit sits at the stock ceiling |

`gpu_pcie_health` is the one that earns its place. The counters are reset by a reboot and
readable without root, and nothing else in the stack looks at them — a link that has begun
retrying shows up in a routine `just doctor` days before it drops the card.

**If it happens twice, it is the hardware.** In that order: reseat the card and both PCIe power
cables (two separate cables to the PSU, not one cable with two tails — the Level1Techs case
suspected exactly this), then another slot, then the PSU. One occurrence in five boots, two of
them 43 days and six weeks long, is not yet a pattern.

# When a host goes off the network

Different failure, same effect on a run, and worth telling apart from the one above. Measured on
nova, 2026-09-10 05:23:31, between two frames of an `image-set` run:

```
$ curl -m 8 http://100.82.150.94:8801/          # no response, exit 28
$ ssh nova@100.82.150.94                        # connect to port 22: Connection timed out
$ ping -c 3 100.82.150.94                       # 3 transmitted, 0 received, 100% packet loss
$ tailscale status | grep nova
100.82.150.94  nova  linux  active; relay "ams"; offline, last seen 9m ago
```

`tailscale status` is the one that answers the question, and `tailscale status --json` answers it
precisely: `LastSeen` is an exact timestamp where the CLI only prints a rounded "9m ago". On nova
that was `2026-09-10T03:23:31.1Z` — 05:23:31 local, about 66 seconds into a frame, against a run
whose previous frame had landed at 05:22:25.

**Telling this apart from a dead GPU matters, and one command does it:**

```bash
ip neigh show | grep <lan-ip>      # INCOMPLETE = nothing is answering ARP
```

A card that has fallen off the bus leaves the host **up**: it still answers ping and ssh,
`nvidia-smi` says `Unable to determine the device handle`, and the kernel log holds an Xid.
vegaserv proved it two hours before nova went — its GPU died at 03:12 and the machine stayed
pingable and fully usable for three more hours. A dead GPU silences the display, not the NIC.

A host that is off, hard-hung **or asleep** answers nothing at layer 2. `INCOMPLETE` in the ARP
table proves only that: it rules the GPU out and nothing else. Check the gateway and sweep the
/24 first so you know it is not your own end — and then **check suspend before you assume
damage**, because it is the only one of the four that is not a fault and it looks exactly like
the other three from outside.

That is what nova turned out to be, and the lesson cost two hours of wrong conclusions:

```
05:21:39  systemd-logind: Power key pressed short.
05:22:54  systemd-logind: The system will suspend now!
05:22:55  Starting nvidia-suspend.service - NVIDIA system suspend actions...
05:23:04  nvidia-suspend.service: Finished. Consumed 8.4s CPU, 18.1G memory peak
05:23:04  systemd-sleep: Performing sleep operation 'suspend'...
07:10:19  systemd-sleep: System returned from sleep operation 'suspend'.
```

A short press of the power button, and a stock `/etc/systemd/logind.conf` under a desktop
session treats that as `suspend`. The 18.1 GB peak in `nvidia-suspend.service` is the driver
copying resident VRAM into system RAM before S3; add the 16.9 s filesystem sync after it and you
have the gap between the last frame served (05:22:25) and the last tailnet contact (05:23:31).
The machine came back on its own with the GPU healthy, HiDream still resident and the same pid
still listening.

**When you get back in, `uptime` is the first thing to type.** A machine that suspended has an
uptime spanning the outage on a single boot; one that crashed or lost power does not.

### Stop a GPU worker having a path to sleep

Suspending mid-generation destroys the run whatever else survives, so on a headless worker
neither the button nor an idle timer should be able to do it. Both need root:

```bash
# 1. no path to sleep at all — takes effect immediately, no logind restart
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

# 2. and a stray press of the power button does nothing
sudo mkdir -p /etc/systemd/logind.conf.d
sudo tee /etc/systemd/logind.conf.d/10-gpu-worker.conf >/dev/null <<'CONF'
[Login]
HandlePowerKey=ignore
HandleSuspendKey=ignore
HandleLidSwitch=ignore
CONF
sudo systemctl restart systemd-logind    # ends graphical sessions on some setups; a reboot is safer
```

Check afterwards with `systemctl is-enabled suspend.target` (want: `masked`) and
`systemd-inhibit --list`. Worth knowing what is *not* the problem on nova: GNOME idle suspend was
never armed — `gsettings get org.gnome.settings-daemon.plugins.power sleep-inactive-ac-timeout`
returns `0`, which means never.

What it looks like from inside a run: the stage that was mid-request simply stops. The HiDream
client has no request timeout, so `generate_keyframes` sits on a socket that will never answer
until the job's outer `timeout` fires. There is nothing to recover; stop the queue by PID (never a
bare self-matching `pkill -f` over ssh), delete the half-written run directory so it cannot be
mistaken for a result later, and move the pool to the other host — if there is one.

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
