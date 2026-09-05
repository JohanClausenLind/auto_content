#!/usr/bin/env bash
# Run a build script inside the system Blender with MPFB2 available.
#
# MPFB2 (MakeHuman for Blender, GPL) is installed under the Blender 5.1 user config on this host;
# Blender 5.2 loads it happily when BLENDER_USER_EXTENSIONS points at that tree, so nothing has to
# be installed into the 5.2 config. Override CF_MPFB_EXTENSIONS / CF_MPFB_MODULE / CF_BLENDER_BIN
# if MPFB lives elsewhere.
#
#   assets_build/run_in_blender.sh <script.py> [args passed after --]
set -euo pipefail
BLENDER="${CF_BLENDER_BIN:-/snap/bin/blender}"
export BLENDER_USER_EXTENSIONS="${CF_MPFB_EXTENSIONS:-$HOME/.config/blender/5.1/extensions}"
export CF_MPFB_MODULE="${CF_MPFB_MODULE:-bl_ext.blender_org.mpfb}"
script="$1"; shift
exec "$BLENDER" --background --factory-startup --python-exit-code 3 --python "$script" -- "$@"
