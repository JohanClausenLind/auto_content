#!/usr/bin/env bash
# Create the upstream checkout environments the post-chain runners call into. Torch wheels come
# from the CUDA 12.8 index (RTX 3090, driver 595+); pip resolves the newest build that index
# offers, which on a 595 host is cu130.
#   skills/video/postchain/setup_envs.sh [cutie|propainter|gimm_vfi|rife|seedvr2|mmaudio ...]
#
# All six by default. The first three were the only ones listed until 2026-09-09, because on the
# machine this was written for the other three already happened to carry a .venv. On a fresh host
# they do not, and "post chain is installed" then meant a chain that fails at its third tool.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../.." && pwd)"
EXT="${CF_EXTERNAL_DIR:-$ROOT/external}"
TORCH_INDEX="https://download.pytorch.org/whl/cu128"
tools=("$@"); [ ${#tools[@]} -eq 0 ] && tools=(cutie propainter gimm_vfi rife seedvr2 mmaudio)
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
    # tensorboard is not optional and is not obvious: `src/utils/utils.py` imports `.writer`, which
    # imports `torch.utils.tensorboard`, which imports `tensorboard` — at module load, before the
    # interpolator does anything. Without it every `interpolate` step died at
    # "ModuleNotFoundError: No module named 'tensorboard'" three tools into the chain.
    # cupy-cuda13x, not 12x, and pinned: this venv's torch is a CUDA 13 build and ships
    # libcudart.so.13, so a cupy-cuda12x wheel installs and then fails to import. It was
    # unpinned and resolved to a cuda12x build that imported anyway and died later, inside the
    # kernel launch, with "module 'cupy.cuda' has no attribute 'compile_with_cache'" — CuPy
    # removed that call in 13.0 and softsplat still uses it. compat/sitecustomize.py puts it back
    # (see its docstring); tools/gimm_vfi.py is what puts compat/ on PYTHONPATH.
    gimm_vfi)   repo="$EXT/GIMM-VFI";   py=3.10; extra=(numpy "opencv-python" Pillow tqdm omegaconf
                  easydict einops scipy scikit-image timm loguru yacs pyyaml "cupy-cuda13x==14.2.0"
                  "tensorboard>=2.11");;
    # RIFE pins numpy<=1.23.5, which has no wheel for 3.12 — hence 3.11, not a preference.
    rife)       repo="$EXT/Practical-RIFE"; py=3.11; extra=(-r requirements.txt);;
    seedvr2)    repo="$EXT/seedvr2_videoupscaler"; py=3.12; extra=(-r requirements.txt);;
    # MMAudio declares torch >= 2.5.1 and nothing tighter, so it takes the index's current build.
    mmaudio)    repo="$EXT/MMAudio";    py=3.12; post=(-e .); extra=();;
    *) echo "unknown tool $t (known: cutie propainter gimm_vfi rife seedvr2 mmaudio)"; exit 2;;
  esac
  if [ ! -d "$repo" ]; then
    echo "!! $t: no checkout at $repo — clone it first (see docs/gpu-hosts.md)"; exit 3
  fi
  echo "==> $t: $repo (python $py)"
  ( cd "$repo" && uv venv --python "$py" .venv && \
    uv pip install --python .venv/bin/python --index-url "$TORCH_INDEX" --extra-index-url https://pypi.org/simple \
      torch torchvision "${extra[@]}" && \
    if [ ${#post[@]} -gt 0 ]; then uv pip install --python .venv/bin/python "${post[@]}"; fi )
  post=()
done
echo "done; verify with:"
echo "  for r in Cutie ProPainter GIMM-VFI Practical-RIFE seedvr2_videoupscaler MMAudio; do \\"
echo "    printf '%-24s ' \"\$r\"; $EXT/\$r/.venv/bin/python -c 'import torch;print(torch.__version__, torch.cuda.is_available())'; done"
