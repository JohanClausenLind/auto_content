"""Whether retrieval can change a film, and whether the run says so either way.

The reference library was queried three times on this host and recorded ``selected: 0`` every
time. That one number covers three unrelated situations and distinguishes none of them, which is
why 19 GB of assembled data spent five days looking broken when it was working correctly.
"""

from __future__ import annotations

from pathlib import Path

from content_factory.workflows.stages import _first_stageable, _selection_reason


class _Match:
    def __init__(self, clip_id: str) -> None:
        self.clip_id = clip_id


def test_only_a_retargeted_clip_is_stageable(tmp_path: Path, monkeypatch) -> None:
    """An SBU sequence is a skeleton to look at; a baked CMU take can drive a rig.

    Only 58 of the index's 15789 clips are retargeted, so "matched well" and "can be staged" come
    apart constantly — and before this they were reported by the same number.
    """
    clips = tmp_path / "clips"
    clips.mkdir()
    (clips / "cmu_22_23_08.json").write_text("{}")
    monkeypatch.setattr("content_factory.shots.planner.CLIPS_DIR", clips)

    assert _first_stageable([_Match("sbu_s01s02_06_001")]) is None
    assert _first_stageable([_Match("h4d_test_002_hugging")]) is None
    # Ranked order is preserved: the first stageable match wins, not the first match.
    assert _first_stageable([_Match("sbu_s01s02_06_001"), _Match("cmu_22_23_08")]) == "cmu_22_23_08"
    assert _first_stageable([]) is None


def test_the_planner_and_the_stage_apply_the_same_predicate(tmp_path: Path, monkeypatch) -> None:
    """``plan_shots_from_reference`` picks a clip with its own copy of this test; if the two
    disagree, the selection file promises a staging the planner will not perform."""
    from content_factory.shots import planner

    clips = tmp_path / "clips"
    clips.mkdir()
    (clips / "cmu_18_19_04.json").write_text("{}")
    monkeypatch.setattr(planner, "CLIPS_DIR", clips)

    matches = [_Match("sbu_s01s02_02_001"), _Match("cmu_18_19_04")]
    stage_choice = _first_stageable(matches)
    # The planner's own loop, reproduced from plan_shots_from_reference.
    planner_choice = next(
        (m.clip_id for m in matches if (planner.CLIPS_DIR / f"{m.clip_id}.json").is_file()), None
    )
    assert stage_choice == planner_choice == "cmu_18_19_04"


def test_each_of_the_three_zero_selected_cases_reads_differently() -> None:
    no_people = _selection_reason(5, 0, 0)
    nothing_stageable = _selection_reason(5, 5, 0)
    working = _selection_reason(5, 5, 3)
    assert "no people in it" in no_people
    assert "not rigs to stage from" in nothing_stageable
    assert "can be staged" in working
    assert len({no_people, nothing_stageable, working}) == 3
    assert _selection_reason(0, 0, 0) != no_people
