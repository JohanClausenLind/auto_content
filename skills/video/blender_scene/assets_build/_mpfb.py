"""Shared MPFB bootstrap for the asset-build scripts (runs inside Blender's Python)."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType

import bpy  # type: ignore[import-not-found]

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
for p in (str(SKILL_DIR), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

MPFB_MODULE = os.environ.get("CF_MPFB_MODULE", "bl_ext.blender_org.mpfb")

# OpenPose COCO names (MPFB's _openposeconstants) -> the skill's OpenPose-18 joint names.
COCO_TO_OPENPOSE18 = {
    "Nose": "nose",
    "Neck": "neck",
    "Right Shoulder": "r_shoulder",
    "Right Elbow": "r_elbow",
    "Right Wrist": "r_wrist",
    "Left Shoulder": "l_shoulder",
    "Left Elbow": "l_elbow",
    "Left Wrist": "l_wrist",
    "Right Hip": "r_hip",
    "Right Knee": "r_knee",
    "Right Ankle": "r_ankle",
    "Left Hip": "l_hip",
    "Left Knee": "l_knee",
    "Left Ankle": "l_ankle",
    "Right Eye": "r_eye",
    "Left Eye": "l_eye",
    "Right Ear": "r_ear",
    "Left Ear": "l_ear",
}


class Mpfb:
    def __init__(self) -> None:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        res = bpy.ops.preferences.addon_enable(module=MPFB_MODULE)
        if "FINISHED" not in res or MPFB_MODULE not in bpy.context.preferences.addons:
            raise RuntimeError(
                f"could not enable {MPFB_MODULE}; set BLENDER_USER_EXTENSIONS to the tree that holds it"
            )
        self.module: ModuleType = importlib.import_module(MPFB_MODULE)
        self.HumanService = importlib.import_module(
            f"{MPFB_MODULE}.services.humanservice"
        ).HumanService
        self.RigService = importlib.import_module(f"{MPFB_MODULE}.services.rigservice").RigService
        self.TargetService = importlib.import_module(
            f"{MPFB_MODULE}.services.targetservice"
        ).TargetService
        self.AnimationService = importlib.import_module(
            f"{MPFB_MODULE}.services.animationservice"
        ).AnimationService
        self.LocationService = importlib.import_module(
            f"{MPFB_MODULE}.services.locationservice"
        ).LocationService
        self.openpose = importlib.import_module(
            f"{MPFB_MODULE}.ui.operations.ai.operators._openposeconstants"
        )

    @property
    def version(self) -> str:
        info = getattr(self.module, "bl_info", None) or {}
        v = info.get("version")
        if v:
            return ".".join(str(x) for x in v)
        manifest = Path(self.module.__file__).parent / "blender_manifest.toml"
        if manifest.exists():
            for line in manifest.read_text().splitlines():
                if line.startswith("version"):
                    return line.split("=", 1)[1].strip().strip('"')
        return "unknown"

    def data_path(self, *parts: str) -> Path:
        return Path(self.module.__file__).parent / "data" / Path(*parts)

    def create_human(self, macro_overrides: dict | None = None):
        macro = self.TargetService.get_default_macro_info_dict()
        for k, v in (macro_overrides or {}).items():
            if k == "race" and isinstance(v, dict):
                macro["race"].update(v)
            else:
                macro[k] = v
        return self.HumanService.create_human(macro_detail_dict=macro)

    def add_rig(self, basemesh, rig_name: str = "default"):
        rig = self.HumanService.add_builtin_rig(basemesh, rig_name)
        if rig is None:
            raise RuntimeError(f"MPFB could not build rig {rig_name!r}")
        return rig


def args_after_dashes() -> list[str]:
    """Script arguments: everything after Blender's ``--`` separator, tolerating a second ``--``."""
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    while argv and argv[0] == "--":
        argv = argv[1:]
    return argv
