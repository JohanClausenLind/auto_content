#!/usr/bin/env bash
# Build every character recipe, bake the pose library and render turnarounds.
#   skills/video/blender_scene/assets_build/build_all.sh [assets_root]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL="$(dirname "$HERE")"
ASSETS="${1:-${CF_BLENDER_ASSETS:-/mnt/fast/models/blender-assets}}"
mkdir -p "$ASSETS"/{characters,poses,clips}
"$HERE/run_in_blender.sh" "$HERE/verify_mpfb.py" | tail -1
"$HERE/run_in_blender.sh" "$HERE/bake_poses.py" "$ASSETS" | tail -1
for recipe in "$HERE"/recipes/*.json; do
  "$HERE/run_in_blender.sh" "$HERE/build_character.py" "$recipe" "$ASSETS" | tail -1
  name="$(basename "$recipe" .json)"
  (cd "$SKILL" && uv run python "$HERE/render_turnaround.py" "$name" --assets "$ASSETS" | tail -1)
done
echo "assets under $ASSETS:"; find "$ASSETS" -maxdepth 3 -name '*.blend' -o -maxdepth 3 -name 'index.json' | sort
