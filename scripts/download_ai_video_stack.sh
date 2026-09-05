#!/usr/bin/env bash
set -Eeuo pipefail

# AI video stack downloader for Ubuntu/Linux (the base pass; scripts/download_video_stack_extras.sh
# adds the pieces this one omits). Lived in ~/Downloads until 2026-09-05; now part of the repo.
# Usage (from the repo root):
#   ./scripts/download_ai_video_stack.sh          # core RTX 3090-friendly set
#   ./scripts/download_ai_video_stack.sh all      # also download very large / >24GB-oriented models
#
# Defaults: code checkouts -> <repo>/external, weights -> /mnt/fast/models (if present).
# Optional overrides:
#   AI_VIDEO_ROOT=/mnt/models/ai-video ./scripts/download_ai_video_stack.sh
#   MODEL_DIR=/mnt/models REPO_DIR=/mnt/repos ./scripts/download_ai_video_stack.sh
#
# Gated Hugging Face repos require:
#   1. Accepting their license/terms on Hugging Face
#   2. Running: hf auth login

MODE="${1:-core}"
if [[ "$MODE" != "core" && "$MODE" != "all" ]]; then
  echo "Usage: $0 [core|all]"
  exit 2
fi

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

mkdir -p "$MODEL_DIR" "$REPO_DIR"

echo "Root:       $ROOT"
echo "Models:     $MODEL_DIR"
echo "Repos:      $REPO_DIR"
echo "Mode:       $MODE"
echo

# ---------- prerequisites ----------
missing=()
for c in git python3 curl; do
  command -v "$c" >/dev/null 2>&1 || missing+=("$c")
done

if ((${#missing[@]})); then
  echo "Missing required commands: ${missing[*]}"
  echo "On Ubuntu install with:"
  echo "  sudo apt update && sudo apt install -y git git-lfs python3 python3-venv curl ffmpeg"
  exit 1
fi

git lfs install >/dev/null 2>&1 || true

if [[ ! -x "$VENV/bin/python" ]]; then
  python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install -q -U pip huggingface_hub

HF="$VENV/bin/hf"

echo "Hugging Face auth status:"
"$HF" auth whoami 2>/dev/null || {
  echo "  Not logged in. Public downloads will work, gated downloads will be skipped/fail."
  echo "  Run: $HF auth login"
}
echo

FAILED=()

clone_repo() {
  local url="$1"
  local name="$2"
  local branch="${3:-}"

  local dst="$REPO_DIR/$name"
  if [[ -d "$dst/.git" ]]; then
    echo "[git] exists: $name"
    return 0
  fi

  echo "[git] cloning: $name"
  if [[ -n "$branch" ]]; then
    git clone --depth 1 --branch "$branch" "$url" "$dst" || FAILED+=("git:$name")
  else
    git clone --depth 1 "$url" "$dst" || FAILED+=("git:$name")
  fi
}

hf_download() {
  local repo="$1"
  local dst="$2"
  shift 2

  mkdir -p "$dst"
  echo "[hf] $repo -> $dst"
  if ! "$HF" download "$repo" "$@" --local-dir "$dst"; then
    echo "  WARNING: failed: $repo"
    FAILED+=("hf:$repo")
  fi
}

# ============================================================================
# CODE REPOSITORIES
# ============================================================================

clone_repo "https://github.com/Lightricks/LTX-2.git"                "LTX-2"
clone_repo "https://github.com/facebookresearch/sam3.git"           "sam3"
clone_repo "https://github.com/Stability-AI/stable-audio-3.git"     "stable-audio-3"
clone_repo "https://github.com/ace-step/ACE-Step-1.5.git"           "ACE-Step-1.5"
clone_repo "https://github.com/m-bain/whisperX.git"                 "whisperX"
clone_repo "https://github.com/hkchengrex/MMAudio.git"              "MMAudio"
clone_repo "https://github.com/QwenAudio/ThinkSound.git"            "PrismAudio" "prismaudio"
clone_repo "https://github.com/GSeanCDAT/GIMM-VFI.git"              "GIMM-VFI"
clone_repo "https://github.com/MCG-NJU/VFIMamba.git"                "VFIMamba"
clone_repo "https://github.com/hzwer/Practical-RIFE.git"            "Practical-RIFE"

# ============================================================================
# CORE MODEL WEIGHTS — selected for a 24 GB RTX 3090-oriented local stack
# ============================================================================

# --- LTX-2.5 Q5_K_M: 3090-friendly quantized transformer + text encoder ---
# ~16.9 GB transformer + ~9.5 GB text encoder.
hf_download \
  "elix3r/LTX-2.5-22b-distilled-GGUF" \
  "$MODEL_DIR/ltx25/diffusion_models" \
  "ltx-2.5-22b-distilled-transformer-Q5_K_M.gguf"

hf_download \
  "elix3r/gemma4-12b-with-proj-ltx-2.5-GGUF" \
  "$MODEL_DIR/ltx25/text_encoders" \
  "gemma4-12b-with-proj-ltx-2.5-Q5_K_M.gguf"

# Official LTX support models. Gated: accept Lightricks terms first.
hf_download \
  "Lightricks/LTX-2.5" \
  "$MODEL_DIR/ltx25" \
  "vae/ltx-2.5-audio-vae-bf16.safetensors" \
  "vae/ltx-2.5-video-vae-conv-bf16.safetensors" \
  "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"

# --- SeedVR2: use 3B FP16 instead of downloading the whole ~113 GB repo ---
hf_download \
  "Comfy-Org/SeedVR2" \
  "$MODEL_DIR/seedvr2" \
  "diffusion_models/seedvr2_3b_fp16.safetensors" \
  "vae/seedvr2_ema_vae_fp16.safetensors"

# --- Stable Audio 3 Small SFX ---
# Gated: accept Stability AI / Gemma terms first.
hf_download \
  "stabilityai/stable-audio-3-small-sfx" \
  "$MODEL_DIR/stable-audio-3-small-sfx"

# --- ACE-Step 1.5: main model pack (~10 GB) ---
hf_download \
  "ACE-Step/Ace-Step1.5" \
  "$MODEL_DIR/ace-step-1.5"

# --- SAM 3.1: current segmentation/tracking checkpoints ---
# Gated: request/accept Meta checkpoint access first.
hf_download \
  "facebook/sam3.1" \
  "$MODEL_DIR/sam3.1"

# --- MMAudio: recommended large 44.1 kHz v2 flow model only.
# MMAudio auto-fetches its other shared dependencies when first run.
hf_download \
  "hkchengrex/MMAudio" \
  "$MODEL_DIR/mmaudio" \
  "mmaudio_large_44k_v2.pth"

# ============================================================================
# OPTIONAL VERY LARGE MODELS
# ============================================================================

if [[ "$MODE" == "all" ]]; then
  echo
  echo "=== ALL mode: downloading very large models ==="
  echo

  clone_repo "https://github.com/Wan-Video/Wan2.2.git" \
             "Wan2.2"

  clone_repo "https://github.com/jd-opensource/JoyAI-Video-Edit.git" \
             "JoyAI-Video-Edit"

  # Wan-Animate-2 repository is ~82.5 GB at time of writing.
  # The BF16 14B model is not a comfortable native fit for 24 GB VRAM.
  hf_download \
    "Wan-AI/Wan2.2-Animate-2-14B" \
    "$MODEL_DIR/wan2.2-animate-2-14b"

  # JoyAI current weights. Official deployment lists ~51 GB total dependencies.
  # Its documented low-VRAM path targets 32 GB-class GPUs, not a 24 GB 3090.
  hf_download \
    "jdopensource/JoyAI-Video-Edit" \
    "$MODEL_DIR/joyai-video-edit" \
    --include "dit/joyai_video_edit_dit_0811.pth" "vae/*"

  hf_download \
    "XiaomiMiMo/MiMo-VL-7B-RL-2508" \
    "$MODEL_DIR/joyai-video-edit/MiMo-VL-7B-RL-2508"

  # Optional: full MMAudio model repository.
  # This duplicates additional variants; core mode already downloads the recommended v2 model.
  hf_download \
    "hkchengrex/MMAudio" \
    "$MODEL_DIR/mmaudio-full"
fi

echo
echo "================================================================"
echo "Download pass finished."
echo "Models: $MODEL_DIR"
echo "Repos:  $REPO_DIR"
echo "================================================================"

if ((${#FAILED[@]})); then
  echo
  echo "Some downloads failed:"
  printf '  - %s\n' "${FAILED[@]}"
  echo
  echo "Most commonly this means a gated Hugging Face model needs license acceptance/authentication."
  echo "After accepting the model terms, run:"
  echo "  $HF auth login"
  echo "Then simply rerun this script; Hugging Face resumes cached downloads."
  exit 1
fi

echo
echo "All requested downloads completed successfully."
