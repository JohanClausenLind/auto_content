#!/usr/bin/env bash
set -Eeuo pipefail

# Supplement to scripts/download_ai_video_stack.sh: fetches the pieces that script
# clones code for but never downloads weights for, plus the tier models it omits entirely.
# Every Hugging Face download is pinned to the revision verified on 2026-09-05.
#
# Usage:
#   ./scripts/download_video_stack_extras.sh          # small fixes: VFI checkpoints,
#                                                     #   LivePortrait, ProPainter (~5 GB)
#   ./scripts/download_video_stack_extras.sh all      # + Wan 2.2 T2V GGUF pair and
#                                                     #   Wan-Animate-2 community GGUF (~37 GB)
#
# NOT downloaded here, on purpose:
#   - Practical-RIFE weights: official source is Google Drive (repo README); the HF mirrors are
#     untrusted 0-download junk. Fetch train_log/ manually into models/rife/train_log/.
#   - LTX-2.3 editing IC-LoRAs: verified base_model=Lightricks/LTX-2.3 — they do not apply to
#     the LTX-2.5 distilled GGUF this stack uses. Download only if you also take the 2.3 base.
#   - facebook/sam3.1 weights: manually gated — request access on Hugging Face, then rerun the
#     ORIGINAL downloader (it resumes and picks up the checkpoints).
#   - JoyAI-Video-Edit: its documented low-VRAM path targets 32 GB-class GPUs, not a 24 GB 3090.

MODE="${1:-core}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # repo root
# Since 2026-09-05 the stack is laid out ComfyUI-style inside the repo: checkouts in external/,
# the hf download venv in .venvs/download, weights in the /mnt/fast store (models/ is only the
# symlink index). $AI_VIDEO_ROOT / $AI_VIDEO_MODELS (or MODEL_DIR, REPO_DIR) override.
ROOT="${AI_VIDEO_ROOT:-$HERE}"
if [[ -z "${MODEL_DIR:-}" ]]; then
  if [[ -n "${AI_VIDEO_MODELS:-}" ]]; then MODEL_DIR="$AI_VIDEO_MODELS"
  elif [[ -d /mnt/fast/models ]]; then MODEL_DIR=/mnt/fast/models
  else MODEL_DIR="$ROOT/models"; fi
fi
REPO_DIR="${REPO_DIR:-$ROOT/external}"
VENV="$ROOT/.venvs/download"
HF="$VENV/bin/hf"

[[ -x "$HF" ]] || { echo "expected $HF from download_ai_video_stack.sh — run that first"; exit 1; }
mkdir -p "$MODEL_DIR" "$REPO_DIR"

FAILED=()

hf_pinned() {
  local repo="$1" rev="$2" dst="$3"; shift 3
  mkdir -p "$dst"
  echo "[hf] $repo@${rev:0:12} -> $dst"
  "$HF" download "$repo" "$@" --revision "$rev" --local-dir "$dst" || FAILED+=("hf:$repo")
}

fetch() {
  local url="$1" dst="$2"
  if [[ -s "$dst" ]]; then echo "[curl] exists: $dst"; return 0; fi
  mkdir -p "$(dirname "$dst")"
  echo "[curl] $url"
  curl -fL --retry 3 -o "$dst.part" "$url" && mv "$dst.part" "$dst" || FAILED+=("curl:$url")
}

# ---- MMAudio: the base script asks for a bare filename but the repo stores it under
# weights/ — this is why models/mmaudio ended up empty. Correct path, pinned. -----------------
hf_pinned "hkchengrex/MMAudio" "eb13a1a98fdbec91753775c57b074ccdfc60587c" "$MODEL_DIR/mmaudio" \
  "weights/mmaudio_large_44k_v2.pth"

# ---- frame interpolation checkpoints (repos were cloned weightless) ------------------------
hf_pinned "GSean/GIMM-VFI" "ab7735cdcfbd2e03c1bf2819380a25e8a4f321d1" "$MODEL_DIR/gimm-vfi" \
  gimmvfi_f_arb.pt gimmvfi_r_arb.pt flowformer_sintel.pth raft-things.pth

hf_pinned "MCG-NJU/VFIMamba" "9c6ded6066f6d5cff211e7c466b36dca3137a61d" "$MODEL_DIR/vfimamba"

# ---- LivePortrait (official weights live under KlingTeam now) -------------------------------
# insightface dependency is non-commercial research: check rights before commercial output.
hf_pinned "KlingTeam/LivePortrait" "82a4fa6735ca58432b6ce39301b4b9ee066dea47" \
  "$MODEL_DIR/liveportrait" --include "liveportrait/*"

# ---- ProPainter: official GitHub release weights (S-Lab 1.0, NON-COMMERCIAL) ----------------
if [[ ! -d "$REPO_DIR/ProPainter/.git" ]]; then
  git clone --depth 1 "https://github.com/sczhou/ProPainter.git" "$REPO_DIR/ProPainter" \
    || FAILED+=("git:ProPainter")
fi
PP="https://github.com/sczhou/ProPainter/releases/download/v0.1.0"
for f in ProPainter.pth raft-things.pth recurrent_flow_completion.pth; do
  fetch "$PP/$f" "$MODEL_DIR/propainter/$f"
done

# ---- Cutie: object/person tracking + segmentation (ungated SAM alternative) -----------------
if [[ ! -d "$REPO_DIR/Cutie/.git" ]]; then
  git clone --depth 1 "https://github.com/hkchengrex/Cutie.git" "$REPO_DIR/Cutie" \
    || FAILED+=("git:Cutie")
fi
fetch "$PP/cutie-base-mega.pth" "$MODEL_DIR/cutie/cutie-base-mega.pth"

# ---- speech restoration: the voice chain's two model steps (2026-09-07) --------------------
# Both are permissively licensed (Resemble Enhance MIT code AND weights; ClearerVoice Apache-2.0),
# both preserve length exactly, and both are small enough to keep resident. See
# skills/audio/{resemble_enhance,clearervoice}/README.md and docs/setup.md.
if [[ ! -d "$REPO_DIR/resemble-enhance/.git" ]]; then
  git clone "https://github.com/resemble-ai/resemble-enhance.git" "$REPO_DIR/resemble-enhance" \
    || FAILED+=("git:resemble-enhance")
fi
git -C "$REPO_DIR/resemble-enhance" checkout --quiet 8e978149bfe8abab3eb77d965d579a111afdb0ff \
  || FAILED+=("git:resemble-enhance@8e97814")

# enhancer_stage2/ only: the repo also holds demo .mp4 files nothing here reads.
hf_pinned "ResembleAI/resemble-enhance" "4e3510ce4a8391159f665903544c5150bee7b2cb" \
  "$MODEL_DIR/resemble-enhance" --include "enhancer_stage2/*"

# The inference code the skill actually installs is the clearvoice wheel; this checkout is the
# readable reference for it.
if [[ ! -d "$REPO_DIR/ClearerVoice-Studio/.git" ]]; then
  git clone "https://github.com/modelscope/ClearerVoice-Studio.git" \
    "$REPO_DIR/ClearerVoice-Studio" || FAILED+=("git:ClearerVoice-Studio")
fi
git -C "$REPO_DIR/ClearerVoice-Studio" checkout --quiet 6b3774dc79c46ae8bed2a4fa5f706f0ac8c75c61 \
  || FAILED+=("git:ClearerVoice-Studio@6b3774d")

hf_pinned "alibabasglab/MossFormer2_SE_48K" "eff8c97925c8bec812af707814b3e5d777fd4503" \
  "$MODEL_DIR/mossformer2-se-48k" \
  --include "last_best_checkpoint" --include "last_best_checkpoint.pt" --include "README.md"

# NOT do_03925000 (1.7 GB): HiFi-GAN training state. The SR model's own last_best_checkpoint
# manifest names only the two files below, and a verified run never opens the third.
hf_pinned "alibabasglab/MossFormer2_SR_48K" "39eb1f25ea84f5e0315ade9ac0070fff216fc690" \
  "$MODEL_DIR/mossformer2-sr-48k" \
  --include "last_best_checkpoint" --include "last_best_checkpoint_g.pt" \
  --include "last_best_checkpoint_m.pt" --include "README.md"

if [[ "$MODE" == "all" ]]; then
  # ---- Wan 2.2 T2V for 24 GB: community GGUF quant pair + official support files ------------
  if [[ ! -d "$REPO_DIR/Wan2.2/.git" ]]; then
    git clone --depth 1 "https://github.com/Wan-Video/Wan2.2.git" "$REPO_DIR/Wan2.2" \
      || FAILED+=("git:Wan2.2")
  fi
  hf_pinned "QuantStack/Wan2.2-T2V-A14B-GGUF" "73eafba53a1a8f29254e4c77f92e74ea27d7cd6f" \
    "$MODEL_DIR/wan2.2/diffusion_models" \
    "HighNoise/Wan2.2-T2V-A14B-HighNoise-Q5_K_M.gguf" \
    "LowNoise/Wan2.2-T2V-A14B-LowNoise-Q5_K_M.gguf"
  hf_pinned "Comfy-Org/Wan_2.2_ComfyUI_Repackaged" "c4f60d30c55a624e35427060fdd217579a6c1d77" \
    "$MODEL_DIR/wan2.2" \
    "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" \
    "split_files/vae/wan2.2_vae.safetensors"

  # ---- Wan-Animate-2: COMMUNITY quant (official repo is BF16-only, no 24 GB fit) ------------
  hf_pinned "karcsiha/wan_animate_2_gguf" "33dc8c561b8ffddab5559c64839ed05c9c7fc8df" \
    "$MODEL_DIR/wan-animate-2" "wan_animate_2-Q5_K_M.gguf"
fi

echo
# ---- repo-local category index (models/ is symlinks into the weight store) ----------------
mkdir -p "$ROOT/models/speech_restoration"
ln -sfn "$MODEL_DIR/resemble-enhance"   "$ROOT/models/speech_restoration/ResembleEnhance"
ln -sfn "$MODEL_DIR/mossformer2-se-48k" "$ROOT/models/speech_restoration/MossFormer2_SE_48K"
ln -sfn "$MODEL_DIR/mossformer2-sr-48k" "$ROOT/models/speech_restoration/MossFormer2_SR_48K"

echo "Extras pass finished. Verify with: uv run content-factory video-stack"
if ((${#FAILED[@]})); then
  printf 'FAILED:\n'; printf '  - %s\n' "${FAILED[@]}"; exit 1
fi
