"""The recipe-driven library bake: which trials become clips, and what the manifest says about them.

The manifest exists so a mislabelled clip shows up in a table instead of on screen. Two things
here are load-bearing for that. A recipe entry names either a two-person ``pair`` or a solo
``subject``, and the two must not be confusable, because handing a renderer an actor id the clip
cannot answer for fails deep inside Blender. And speed is measured rather than inferred from the
posture tag, which is how the two-person set's "running" clips were found to be a scramble for a
chair at 1.8 m/s while an actual sprint sustains 3.2.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mocap.bake_library import RUN_MPS, actors_for, flags, measure

RECIPE = Path(__file__).resolve().parents[1] / "mocap" / "cmu_clips.json"
PAIRS = {"18": "19", "20": "21", "22": "23", "33": "34"}


def _clip(frames: list[tuple[float, float, float]], *, fps: int = 24) -> dict:
    return {
        "fps": fps,
        "frame_count": len(frames),
        "ground_offset": 0.0,
        "actors": [
            {
                "actor_id": "a",
                "frames": [
                    {"root_translation": list(p), "root_yaw_deg": 0.0, "directions": {}}
                    for p in frames
                ],
            }
        ],
    }


def test_a_pair_entry_bakes_both_halves_of_the_take() -> None:
    assert actors_for({"pair": "22", "trial": "08"}, PAIRS) == [("a", "22"), ("b", "23")]


def test_a_subject_entry_bakes_one_performer_and_no_phantom_second() -> None:
    assert actors_for({"subject": "35", "trial": "18"}, PAIRS) == [("a", "35")]


@pytest.mark.parametrize(
    "entry",
    [
        {"trial": "01"},
        {"pair": "18", "subject": "35", "trial": "01"},
    ],
)
def test_an_entry_naming_neither_or_both_is_refused(entry: dict) -> None:
    with pytest.raises(ValueError, match="exactly one of pair or subject"):
        actors_for(entry, PAIRS)


def test_an_unpaired_pair_is_refused() -> None:
    with pytest.raises(ValueError, match="not in the recipe's pairs map"):
        actors_for({"pair": "35", "trial": "18"}, PAIRS)


def test_speed_separates_a_peak_from_a_sustained_run() -> None:
    """A scramble peaks high and averages low; the two numbers have to disagree for that."""
    burst = [(0.0, 0.0, 0.9)] * 24 + [(i * 0.2, 0.0, 0.9) for i in range(1, 4)]
    m = measure(_clip(burst))
    speed = m["speed_mps"]["a"]
    assert speed["peak"] == pytest.approx(4.8, abs=0.01)
    assert speed["sustained_1s"] < speed["peak"]
    assert speed["sustained_1s"] < RUN_MPS


def test_a_steady_sprint_sustains_its_speed() -> None:
    steady = [(i * 3.0 / 24.0, 0.0, 0.9) for i in range(48)]
    speed = measure(_clip(steady))["speed_mps"]["a"]
    assert speed["peak"] == pytest.approx(3.0, abs=0.01)
    assert speed["sustained_1s"] == pytest.approx(3.0, abs=0.01)
    assert speed["sustained_1s"] > RUN_MPS


def test_a_single_frame_clip_reports_no_speed_rather_than_dividing_by_nothing() -> None:
    assert measure(_clip([(0.0, 0.0, 0.9)]))["speed_mps"]["a"] == {
        "peak": 0.0,
        "sustained_1s": 0.0,
    }


def test_a_running_tag_that_the_measurement_contradicts_is_flagged() -> None:
    m = measure(_clip([(i * 1.0 / 24.0, 0.0, 0.9) for i in range(48)]))
    entry = {"posture": ["running"], "contact": ["none"]}
    assert any("tagged running but sustains only" in f for f in flags(entry, m))


def test_a_run_that_nobody_tagged_is_flagged_too() -> None:
    m = measure(_clip([(i * 3.0 / 24.0, 0.0, 0.9) for i in range(48)]))
    entry = {"posture": ["walking"], "contact": ["none"]}
    assert any("not tagged running but sustains" in f for f in flags(entry, m))


def test_a_run_that_travels_is_not_flagged_for_travelling() -> None:
    """Before running was locomotion, every sprint was flagged 'not tagged walking but travels'."""
    m = measure(_clip([(i * 3.0 / 24.0, 0.0, 0.9) for i in range(48)]))
    assert flags({"posture": ["running"], "contact": ["none"]}, m) == []


def test_the_recipe_holds_a_sustained_run_because_the_two_person_set_has_none() -> None:
    recipe = json.loads(RECIPE.read_text(encoding="utf-8"))
    solo = [c for c in recipe["clips"] if "subject" in c]
    assert [c["subject"] for c in solo] == ["35", "16"]
    assert all(c["posture"] == ["running"] for c in solo)
    assert all(c["interaction"] == "no_contact" for c in solo)
    assert all("pair" not in c for c in solo)
    assert all(c["subject"] not in PAIRS and c["subject"] not in PAIRS.values() for c in solo)
