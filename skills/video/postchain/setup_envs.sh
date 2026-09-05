#!/usr/bin/env bash
# Create the upstream checkout environments the post-chain runners call into. Practical-RIFE and
# seedvr2_videoupscaler already carry their own .venv on this host; Cutie, ProPainter and GIMM-VFI
# do not. Torch wheels are pinned to the CUDA 12.8 index (RTX 3090, driver 595).
#   skills/video/postchain/setup_envs.sh [cutie|propainter|gimm_vfi ...]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
EXT="${CF_EXTERNAL_DIR:-$ROOT/external}"
TORCH_INDEX="https://download.pytorch.org/whl/cu128"
tools=("$@"); [ ${#tools[@]} -eq 0 ] && tools=(cutie propainter gimm_vfi)
post=()
for t in "${tools[@]}"; do
  case "$t" in
    # Cutie's pyproject pulls the GUI stack (PySide6, gradio, cchardet — cchardet does not build on
    # Python >= 3.11); the tracker only needs the inference deps, so install the package without
    # its declared deps and list the runtime ones explicitly.
    cutie)      repo="$EXT/Cutie";      py=3.11; post=(--no-deps -e .); extra=(cython "gitpython>=3.1"
                  "thinplate@git+https://github.com/cheind/py-thin-plate-spline" "hickle>=5.0"
                  "tensorboard>=2.11" "numpy>=1.21" "Pillow>=9.5" "opencv-python>=4.8" "scipy>=1.7"
                  "pycocotools>=2.0.7" "tqdm>=4.66.1" "gdown>=4.7.1" "einops>=0.6" "hydra-core>=1.3.2"
                  omegaconf easydict "av>=0.5.2" requests);;
    propainter) repo="$EXT/ProPainter"; py=3.11; extra=(-r requirements.txt);;
    # GIMM-VFI's requirements.txt pins 2021-era wheels (scikit-learn 0.24, fsspec 2023) that no
    # longer build; src/ imports only these third-party packages (softsplat needs cupy).
    gimm_vfi)   repo="$EXT/GIMM-VFI";   py=3.10; extra=(numpy "opencv-python" Pillow tqdm omegaconf
                  easydict einops scipy scikit-image timm loguru yacs pyyaml "cupy-cuda12x");;
    *) echo "unknown tool $t"; exit 2;;
  esac
  echo "==> $t: $repo (python $py)"
  ( cd "$repo" && uv venv --python "$py" .venv && \
    uv pip install --python .venv/bin/python --index-url "$TORCH_INDEX" --extra-index-url https://pypi.org/simple \
      torch torchvision "${extra[@]}" && \
    if [ ${#post[@]} -gt 0 ]; then uv pip install --python .venv/bin/python "${post[@]}"; fi )
  post=()
done
echo "done; verify with: for r in Cutie ProPainter GIMM-VFI; do $EXT/\$r/.venv/bin/python -c 'import torch;print(torch.__version__, torch.cuda.is_available())'; done"
