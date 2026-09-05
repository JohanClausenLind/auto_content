"""Baking CMU trials into cf.clip.v2 documents.

The properties that matter are the ones a broken bake would violate silently: both actors of a
two-person trial stay frame-aligned and on one floor, directions stay unit length, the document is
byte-deterministic so a rebake is a no-op, and a contact trial still shows contact after
decimation.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from mocap.clip import (
    CLIP_SCHEMA,
    SEGMENTS,
    bake_trial,
    contact_gap,
    source_fps,
    write_clip,
)

CMU_ROOT = Path("/mnt/fast/reference/CMU-Mocap/all_asfamc")
DESCRIPTIONS = Path("/mnt/fast/reference/CMU-Mocap/descriptions")
cmu = pytest.mark.skipif(not CMU_ROOT.is_dir(), reason="CMU mocap not on this host")


@pytest.fixture(scope="module")
def hold_hands():
    """22/23 trial 08: 'hold hands, swing arms, walk' - the two-person contact case."""
    return bake_trial(
        CMU_ROOT,
        [("a", "22"), ("b", "23")],
        "08",
        fps=24,
        descriptions_root=DESCRIPTIONS,
        description="hold hands, swing arms, walk",
    )


@cmu
def test_document_shape(hold_hands) -> None:
    doc = hold_hands
    assert doc["schema"] == CLIP_SCHEMA
    assert doc["name"] == "cmu_22_23_08"
    assert doc["fps"] == 24
    assert doc["up_axis"] == "z"
    assert doc["units"] == "m"
    assert doc["segments"] == list(SEGMENTS)
    assert doc["source"]["source_fps"] == 120
    assert doc["source"]["decimation_step"] == 5
    assert [a["actor_id"] for a in doc["actors"]] == ["a", "b"]


@cmu
def test_both_actors_are_frame_aligned(hold_hands) -> None:
    a, b = hold_hands["actors"]
    assert len(a["frames"]) == len(b["frames"]) == hold_hands["frame_count"]
    assert [f["source_frame"] for f in a["frames"]] == [f["source_frame"] for f in b["frames"]]


@cmu
def test_every_segment_direction_is_a_unit_vector(hold_hands) -> None:
    worst = 0.0
    for actor in hold_hands["actors"]:
        for frame in actor["frames"]:
            assert set(frame["directions"]) <= set(SEGMENTS)
            for name, d in frame["directions"].items():
                n = float(np.linalg.norm(d))
                worst = max(worst, abs(n - 1.0))
                assert abs(n - 1.0) < 1e-3, (name, n)
    assert worst < 1e-3


@cmu
def test_the_clip_carries_the_full_segment_set(hold_hands) -> None:
    first = hold_hands["actors"][0]["frames"][0]["directions"]
    assert len(first) == len(SEGMENTS)


@cmu
def test_one_ground_offset_for_the_whole_clip_and_it_is_small(hold_hands) -> None:
    assert isinstance(hold_hands["ground_offset"], float)
    assert abs(hold_hands["ground_offset"]) < 0.15


@cmu
def test_contact_survives_decimation(hold_hands) -> None:
    """Holding hands means the wrists come close. If decimation lost the contact frame, this
    catches it."""
    gaps = contact_gap(hold_hands, "a", "b", "rwrist", "lwrist")
    other = contact_gap(hold_hands, "a", "b", "lwrist", "rwrist")
    assert len(gaps) == hold_hands["frame_count"]
    assert min(min(gaps), min(other)) < 0.40


@cmu
def test_actors_stand_at_a_plausible_separation(hold_hands) -> None:
    a, b = hold_hands["actors"]
    dists = [
        float(np.linalg.norm(np.array(x["root_translation"]) - np.array(y["root_translation"])))
        for x, y in zip(a["frames"], b["frames"], strict=True)
    ]
    assert 0.3 < min(dists), min(dists)
    assert max(dists) < 1.5, max(dists)


@cmu
def test_root_height_is_hip_height(hold_hands) -> None:
    z = [f["root_translation"][2] for f in hold_hands["actors"][0]["frames"]]
    assert 0.7 < min(z) and max(z) < 1.3, (min(z), max(z))


@cmu
def test_write_is_byte_deterministic(hold_hands, tmp_path: Path) -> None:
    p1 = write_clip(hold_hands, tmp_path / "one")
    again = bake_trial(
        CMU_ROOT,
        [("a", "22"), ("b", "23")],
        "08",
        fps=24,
        descriptions_root=DESCRIPTIONS,
        description="hold hands, swing arms, walk",
    )
    p2 = write_clip(again, tmp_path / "two")
    assert p1.read_bytes() == p2.read_bytes()
    assert json.loads(p1.read_text())["name"] == "cmu_22_23_08"


@cmu
def test_solo_bake_has_one_actor() -> None:
    doc = bake_trial(CMU_ROOT, [("a", "22")], "03", fps=24, descriptions_root=DESCRIPTIONS)
    assert len(doc["actors"]) == 1
    assert doc["name"] == "cmu_22_03"
    assert doc["frame_count"] > 10


@cmu
def test_decimation_step_follows_the_source_rate() -> None:
    # Subject 33/34 are a 60 fps pair, so a 24 fps clip decimates by 2 (60/24 = 2.5 -> 2), not 5.
    assert source_fps(DESCRIPTIONS, "33") == 60
    assert source_fps(DESCRIPTIONS, "22") == 120
    doc = bake_trial(
        CMU_ROOT, [("a", "33"), ("b", "34")], "01", fps=24, descriptions_root=DESCRIPTIONS
    )
    assert doc["source"]["source_fps"] == 60
    assert doc["source"]["decimation_step"] == 2


@cmu
def test_missing_descriptions_falls_back_rather_than_failing(tmp_path: Path) -> None:
    assert source_fps(tmp_path, "22") == 120
    assert source_fps(None, "22") == 120


@cmu
def test_mismatched_actors_are_refused() -> None:
    # 18 is 120 fps and 33 is 60 fps: baking them as one take is a mistake, not a resample.
    with pytest.raises(ValueError, match="source fps"):
        bake_trial(CMU_ROOT, [("a", "18"), ("b", "33")], "01", descriptions_root=DESCRIPTIONS)
