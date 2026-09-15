"""Finding a Blender that can actually run the scene skill."""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

CANDIDATES: tuple[str, ...] = (
    "/snap/bin/blender",
    "/usr/local/bin/blender",
    "/opt/blender/blender",
)
"""Where an upstream build lands, in the order a host is likely to have one. PATH's answer is
tried too — last, because it is the one that may be the distro package."""

PROBE_TIMEOUT_S = 120


def _has_oiio(binary: str) -> bool:
    try:
        proc = subprocess.run(
            [
                binary,
                "--background",
                "--factory-startup",
                "--python-exit-code",
                "9",
                "--python-expr",
                "import OpenImageIO",
            ],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


@lru_cache(maxsize=1)
def blender_with_oiio() -> str | None:
    """A Blender whose Python has OpenImageIO, or None when this host has none."""
    on_path = shutil.which("blender")
    for candidate in (*CANDIDATES, on_path):
        if candidate and Path(candidate).exists() and _has_oiio(candidate):
            return candidate
    return None


SKIP_REASON = (
    "no Blender with a bundled OpenImageIO on this host: the scene skill reads its multilayer EXR"
    " passes back through it, and a distro package does not carry it. Install an upstream build"
    " (this repo verified 5.2.1) or point CF__CONTROLS__BLENDER_BIN at one."
)
