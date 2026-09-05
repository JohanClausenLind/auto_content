"""Renders the cube-dolly fixture through the real Blender twice: byte-identical outputs, correct
depth ordering, segmentation ids, growing layout box, camera math matching Blender's own."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "skills" / "video" / "blender_scene"
FIXTURE = SKILL / "tests" / "fixtures" / "shot_cube_dolly.json"

pytestmark = pytest.mark.blender


def _blender() -> str | None:
    return shutil.which("blender") or (
        "/snap/bin/blender" if Path("/snap/bin/blender").exists() else None
    )


def _render(out: Path) -> dict:
    cmd = [
        "uv",
        "run",
        "--project",
        str(SKILL),
        "python",
        str(SKILL / "render.py"),
        str(FIXTURE),
        str(out),
        "--timeout",
        "600",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, cwd=REPO, timeout=900)
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "logs" not in p.parts and p.name != "run.json"
    }


@pytest.mark.skipif(_blender() is None, reason="Blender is not installed")
def test_cube_dolly_renders_deterministically(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    summary = _render(a)
    _render(b)
    assert summary["ok"] and summary["frames"] == 4
    assert summary["engines"]["data"] == "cycles_cpu"
    assert _tree(a) == _tree(b), "outputs differ between two identical runs"

    seg0 = np.asarray(Image.open(a / "segmentation" / "frames" / "0000.png"))
    h, w = seg0.shape
    assert Image.open(a / "segmentation" / "frames" / "0000.png").mode == "P"
    assert seg0[h // 2, w // 2] == 1 and seg0[2, 2] == 0  # cube in the centre, sky in the corner
    assert set(np.unique(seg0).tolist()) == {0, 1, 2}

    d16 = [np.asarray(Image.open(a / "depth16" / "frames" / f"{i:04d}.png")) for i in range(4)]
    centre = [int(d[h // 2, w // 2]) for d in d16]
    assert centre == sorted(centre, reverse=True) and centre[0] > centre[-1], (
        centre
    )  # dolly in: closer each frame
    assert d16[0][seg0 == 1].mean() < d16[0][seg0 == 2].mean()  # cube nearer than backdrop
    d8 = np.asarray(Image.open(a / "depth" / "frames" / "0000.png"))
    assert d8[seg0 == 1].mean() > d8[seg0 == 2].mean() and d8[2, 2] == 0

    normals = np.asarray(Image.open(a / "normals" / "frames" / "0000.png"))
    front = normals[h // 2 + 8, w // 2]
    assert front[2] > 240 and abs(int(front[0]) - 128) < 8  # cube front faces the camera (+Z)
    assert normals[2, 2].tolist() == [0, 0, 0]  # sky is black

    boxes = []
    for i in range(4):
        doc = json.loads((a / "layout" / "frames" / f"{i:04d}.json").read_text())
        cube = next(o for o in doc["objects"] if o["object_id"] == "cube")
        boxes.append(cube["box"]["w"] * cube["box"]["h"])
        assert len(doc["hidream_layout_bboxes"]) == 2
    assert boxes == sorted(boxes) and boxes[-1] > 3 * boxes[0]

    cam = json.loads((a / "camera.json").read_text())
    assert cam["frames"][0]["position"] == [0.0, -6.0, 1.2]
    for f in cam["frames"]:
        q, tq = np.abs(np.array(f["rotation_quat_wxyz"])), np.abs(np.array(f["track_quat_check"]))
        assert np.abs(q - tq).max() < 1e-6

    meta = json.loads((a / "metadata.json").read_text())
    assert meta["seg"]["objects"] == {"backdrop": 2, "cube": 1, "ground": 0}
    assert meta["depth"]["source"].startswith("auto")
    assert (a / "canny" / "frames" / "0000.png").exists()
    edges = np.asarray(Image.open(a / "canny" / "frames" / "0000.png"))
    assert (edges > 0).sum() > 50
    assert sys.version_info >= (3, 12)


@pytest.mark.skipif(_blender() is None, reason="Blender is not installed")
def test_control_bundle_from_live_render(tmp_path: Path) -> None:
    """The control plane's runner + bundle builder against the real skill, on a prop-only shot at
    the fixture plan's size, two frames only."""
    from content_factory.controls import build_control_bundle, run_blender_scene
    from content_factory.schemas.fixtures import sample_shot_plan
    from content_factory.schemas.sequences import ControlKind

    base = sample_shot_plan().shots[0]
    shot = base.model_copy(
        update={"characters": (), "render": base.render.model_copy(update={"frames": (0, 96)})}
    )
    spec_path = tmp_path / "shot_spec.json"
    spec_path.write_text(shot.model_dump_json(indent=1))
    run = run_blender_scene(
        spec_path, tmp_path / "out", blender_bin=_blender() or "blender", timeout_s=600
    )
    assert run.summary["ok"] and run.summary["frames"] == 2
    bundle = build_control_bundle(shot, tmp_path / "out")
    assert (
        bundle.compiler == "blender" and bundle.blender_version and "5." in bundle.blender_version
    )
    assert {t.kind for t in bundle.tracks} == set(shot.render.passes)
    assert [c.frame_index for c in bundle.camera] == [0, 96]
    assert [(s.subject_id, s.segmentation_index) for s in bundle.subjects] == [("bench", 1)]
    seg = bundle.tracks[[t.kind for t in bundle.tracks].index(ControlKind.segmentation)]
    assert [f.frame_index for f in seg.frames] == [0, 96]
    assert (tmp_path / "out" / "bundle.json").exists()


ASSETS = Path(os.environ.get("CF_BLENDER_ASSETS", "/mnt/fast/models/blender-assets"))


@pytest.mark.skipif(
    _blender() is None or not (ASSETS / "characters" / "man_01" / "man_01.blend").exists(),
    reason="Blender or the man_01 character asset is not available",
)
def test_character_skeleton_export(tmp_path: Path) -> None:
    """A rigged MPFB character renders with all 18 OpenPose joints in frame, mirrored the way
    OpenPose expects (the person's right side on the viewer's left when facing the camera), and the
    bundle carries per-frame poses for it."""
    from content_factory.controls import build_control_bundle, run_blender_scene
    from content_factory.schemas.fixtures import sample_shot_plan

    base = sample_shot_plan().shots[0]
    shot = base.model_copy(update={"render": base.render.model_copy(update={"frames": (0, 96)})})
    spec_path = tmp_path / "shot_spec.json"
    spec_path.write_text(shot.model_dump_json(indent=1))
    run_blender_scene(
        spec_path,
        tmp_path / "out",
        blender_bin=_blender() or "blender",
        assets_root=ASSETS,
        timeout_s=600,
    )
    doc = json.loads((tmp_path / "out" / "skeleton" / "frames" / "0000.json").read_text())
    (person,) = doc["people"]
    joints = person["joints"]
    assert len(joints) == 18 and all(j["in_frame"] for j in joints.values())
    assert joints["r_shoulder"]["x"] < joints["l_shoulder"]["x"]
    assert joints["neck"]["y"] < joints["r_hip"]["y"] < joints["r_ankle"]["y"]
    assert len(person["openpose18"]) == 54
    bundle = build_control_bundle(shot, tmp_path / "out")
    man = next(s for s in bundle.subjects if s.subject_id == "man")
    assert man.segmentation_index == 1 and man.poses[0] is not None and man.poses[96] is not None
    assert len(man.poses[0].joints) == 18
    seg = np.asarray(Image.open(tmp_path / "out" / "segmentation" / "frames" / "0096.png"))
    assert (seg == 1).sum() > 2000  # the man fills a real area at the close frame
