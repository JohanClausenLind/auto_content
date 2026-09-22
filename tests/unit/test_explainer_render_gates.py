"""Phase 2 render gates C1-C4: they drive Remotion through Node, so they carry the render marker."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from content_factory.explainer import render
from content_factory.explainer.compile import compile_episode, write_bundle
from content_factory.explainer.qc import check_rendered, encode_like_youtube
from content_factory.schemas.explainer import EvidencePack, ScriptPlan, VisualSpec

pytestmark = pytest.mark.render

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures" / "explainer"


def _trio(name: str) -> tuple[EvidencePack, ScriptPlan, VisualSpec]:
    d = FIXTURES / name
    return (
        EvidencePack.model_validate_json((d / "pack.json").read_text()),
        ScriptPlan.model_validate_json((d / "script.json").read_text()),
        VisualSpec.model_validate_json((d / "spec.json").read_text()),
    )


def _pixel_sha(path: Path) -> str:
    with Image.open(path) as image:
        return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()


def _ffprobe(path: Path) -> dict[str, str]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=nb_frames,width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    return {
        "duration": data["format"]["duration"],
        "nb_frames": stream["nb_frames"],
        "width": str(stream["width"]),
        "height": str(stream["height"]),
        "r_frame_rate": stream["r_frame_rate"],
    }


@pytest.fixture(scope="module")
def amdahl_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    pack, script, spec = _trio("amdahl")
    bundle = compile_episode(pack, script, spec)
    path = tmp_path_factory.mktemp("amdahl") / "bundle.json"
    write_bundle(bundle, path)
    return path


@pytest.fixture(scope="module")
def sixty_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    pack, script, spec = _trio("sixty")
    bundle = compile_episode(pack, script, spec)
    path = tmp_path_factory.mktemp("sixty") / "bundle.json"
    write_bundle(bundle, path)
    return path


def test_c1_random_access_and_sequential_frames_are_pixel_identical(
    amdahl_bundle: Path, tmp_path: Path
) -> None:
    frames = [0, 7, 31, 60, 95, 140]
    sequential = render.render_frames(amdahl_bundle, tmp_path / "seq", frames, "sequential")
    shuffled = [95, 0, 140, 31, 60, 7]
    stills = render.render_frames(amdahl_bundle, tmp_path / "stills", shuffled, "stills")
    by_name_seq = {p.name: _pixel_sha(p) for p in sequential}
    by_name_stills = {p.name: _pixel_sha(p) for p in stills}
    assert by_name_seq.keys() == by_name_stills.keys()
    assert by_name_seq == by_name_stills


def test_c4_sixty_second_timeline_compiles_and_renders_end_to_end(
    sixty_bundle: Path, tmp_path: Path
) -> None:
    result = render.render_bundle(sixty_bundle, tmp_path / "sixty.mp4")
    probe = _ffprobe(Path(result.out))
    assert probe["width"] == "1920" and probe["height"] == "1080"
    assert probe["r_frame_rate"] == "30/1"
    assert abs(float(probe["duration"]) - 60.0) <= 1.0
    assert abs(int(probe["nb_frames"]) - 1800) <= 30


def test_c3_encoded_frames_keep_text_and_line_contrast(sixty_bundle: Path, tmp_path: Path) -> None:
    result = render.render_bundle(sixty_bundle, tmp_path / "master.mp4")
    encoded = tmp_path / "youtube.mp4"
    encode_like_youtube(Path(result.out), encoded)
    bundle = json.loads(sixty_bundle.read_text())
    from content_factory.schemas.explainer import ExplainerRenderBundle

    findings = check_rendered(ExplainerRenderBundle.model_validate(bundle), encoded)
    ran = [f for f in findings if f.passed is not None]
    assert ran, "no check produced a measurement"
    failed = [f for f in ran if f.passed is False]
    assert not failed, "\n".join(
        f"{f.check} {f.scene_id} {f.measured} < {f.threshold}" for f in failed
    )
    text_checks = [f for f in ran if f.check == "text_apca"]
    line_checks = [f for f in ran if f.check == "line_edge"]
    assert text_checks and line_checks


def _ink_mask(image: Image.Image, background: tuple[int, int, int], threshold: int = 60) -> Any:
    """True where a pixel differs from the canvas colour by more than the threshold."""
    array = np.asarray(image.convert("RGB"), dtype=np.int32)
    return np.abs(array - np.array(background, dtype=np.int32)).sum(axis=2) > threshold


def _with_series_label(spec: VisualSpec, label: str, short_label: str = "") -> VisualSpec:
    entities = tuple(
        e.model_copy(update={"label": label, "short_label": short_label})
        if e.entity_id == "ent_series_compute"
        else e
        for e in spec.entities
    )
    return VisualSpec.model_validate(spec.model_copy(update={"entities": entities}).model_dump())


def test_c2_forty_character_label_renders_inside_its_measured_box(tmp_path: Path) -> None:
    from content_factory.explainer.color import rgb_of
    from content_factory.explainer.tokens_gen import SRGB_HEX

    pack, script, spec = _trio("amdahl")
    label = "Computation time on the slower processor"
    assert len(label) == 40
    bundle = compile_episode(pack, script, _with_series_label(spec, label))
    scene = next(s for s in bundle.timeline.scenes if s.scene_id == "scn_latency_bars")
    box = next(b for b in scene.boxes if b.entity_id == "ent_series_compute")
    assert box.text == label and box.font_px is not None and box.font_px >= 26
    path = tmp_path / "bundle.json"
    write_bundle(bundle, path)
    frame = scene.start_frame + scene.duration_frames - 1
    [png] = render.render_frames(path, tmp_path / "stills", [frame], "stills")
    with Image.open(png) as image:
        mask = _ink_mask(image, rgb_of(SRGB_HEX["ui.surface.0"]))
    b = box.box
    inside = int(mask[b.y : b.y + b.height, b.x : b.x + b.width].sum())
    outer = mask[max(0, b.y - 6) : b.y + b.height + 6, max(0, b.x - 6) : b.x + b.width + 6]
    ring = int(outer.sum()) - inside
    assert inside > 200, "the label did not draw inside its measured box"
    assert ring == 0, f"{ring} ink pixels spill outside the measured box"


def test_c2_overlong_label_fails_naming_the_alternative() -> None:
    from content_factory.explainer.errors import EpisodeInvalidError

    pack, script, spec = _trio("amdahl")
    long_label = "Computation on the original processor before any speed-up is applied to it"
    with pytest.raises(EpisodeInvalidError) as excinfo:
        compile_episode(pack, script, _with_series_label(spec, long_label))
    text = str(excinfo.value)
    assert "ent_series_compute" in text and "short_label" in text and "split scene" in text
    bundle = compile_episode(pack, script, _with_series_label(spec, long_label, "Computation"))
    scene = next(s for s in bundle.timeline.scenes if s.scene_id == "scn_latency_bars")
    box = next(b for b in scene.boxes if b.entity_id == "ent_series_compute")
    assert box.text == "Computation"
