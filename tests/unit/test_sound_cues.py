"""The curated sound library reaches the mix, and the cue sheet says what it did and why.

``assets/sfx`` has held 49 loudness-measured, provenance-tracked sounds since 2026-09-07 and
nothing placed a single one of them: a chart drew itself in silence, a hard cut between two cards
had nothing on it, and the fifteen `ui` sounds written for exactly those moments were files with a
manifest entry and no caller. These tests cover the cutter (rules, deterministic), the renderer
(one ffmpeg call, exact length) and the two ways a cue sheet can be wrong.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from content_factory.audio.cues import (
    ACCENTS,
    BED_TAGS_IGNORED,
    MIN_SCENE_MS_FOR_TRANSITION,
    TRANSITION_SOUND,
    CueError,
    SceneSpan,
    bed_for,
    bed_score,
    cut_cue_sheet,
    load_library,
    resolve,
    spans_from_beats,
    spans_from_timeline,
)
from content_factory.audio.mix import place_sfx
from content_factory.schemas.audio import SoundCueRole
from content_factory.schemas.fixtures import sample_story_plan
from content_factory.timeline.compiler import compile_timeline


@pytest.fixture(scope="module")
def library():
    return load_library()


def _duration_s(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def test_the_library_loads_and_every_sound_is_on_disk(library) -> None:
    assert len(library.sounds) == 49
    for sound in library.sounds.values():
        assert sound.path.is_file(), sound.sound_id
        assert sound.duration_s > 0
    # The digest ties a cue sheet to the bytes it was cut against, so it must depend on them.
    assert len(library.digest) == 64


def test_every_accent_names_a_sound_the_library_actually_has(library) -> None:
    """The table is a hand-written mapping into another file's ids, which is exactly the kind of
    list that goes stale silently — a missing id would simply place no sound."""
    for kind, (sound_id, gain, reason) in ACCENTS.items():
        assert sound_id in library.sounds, f"{kind} -> {sound_id}"
        assert gain <= 0
        assert reason
    assert TRANSITION_SOUND in library.sounds


def test_the_bed_is_chosen_by_tag_fraction_not_by_raw_count(library) -> None:
    """A raw count tied three candidates on the demo plan and picked a *park* ambience for a
    coastal wind farm, because `place` is scanned before `weather`."""
    plan = sample_story_plan()
    subject = plan.visual_subject or ""
    assert "wind farm" in subject
    chosen = bed_for(plan, library)
    assert chosen is not None
    assert chosen.sound_id == "wind_open_loop"
    words = frozenset(w.strip(".,").lower() for w in subject.split())
    assert bed_score(words, library.get("wind_open_loop")) > bed_score(
        words, library.get("park_wind_trees_loop")
    )


def test_a_subject_matching_nothing_gets_no_bed_rather_than_a_guess(library) -> None:
    """A forest ambience under a film about interest rates is worse than silence."""
    plan = sample_story_plan().model_copy(update={"visual_subject": "the yield curve inverting"})
    assert bed_for(plan, library) is None


def test_use_tags_do_not_count_toward_a_match(library) -> None:
    """Every bed is tagged `bed`, so counting it would give every candidate the same head start."""
    assert bed_score(frozenset({"bed"}), library.get("wind_open_loop")) == 0.0
    assert "bed" in BED_TAGS_IGNORED


def test_a_cue_sheet_is_cut_from_the_measured_beats_the_mix_already_has(library) -> None:
    """The cutter takes spans, not a compiled timeline, because **mix_audio runs before
    compile_timeline** — the timeline's durations are compiled from the mix's measurements. A
    cutter needing a compiled timeline would have found none and placed nothing, on every run."""
    plan = sample_story_plan()
    beats = [(b.beat_id, i * 4000, i * 4000 + 3800) for i, b in enumerate(plan.beats)]
    spans = spans_from_beats(plan, beats)
    assert [s.kind for s in spans] == ["title", "big_number", "bullet_sequence", "source_card"]
    sheet = cut_cue_sheet(plan, spans, library, deliverable_id=plan.deliverable_id, total_ms=16000)
    kinds = {c.role for c in sheet.cues}
    assert SoundCueRole.bed in kinds or SoundCueRole.room_tone in kinds
    assert SoundCueRole.transition in kinds and SoundCueRole.accent in kinds
    # Every cue says why it is there: a cue sheet is read, not only heard.
    assert all(c.reason for c in sheet.cues)
    # And in time order, which the contract also enforces.
    assert [c.at_ms for c in sheet.cues] == sorted(c.at_ms for c in sheet.cues)


def test_the_same_film_always_gets_the_same_cues(library) -> None:
    plan = sample_story_plan()
    timeline = compile_timeline(plan, timeline_id="tl_cues0000001", narrated=False)
    spans = spans_from_timeline(plan, timeline)
    total = round(timeline.total_frames * 1000 / timeline.fps)
    first = cut_cue_sheet(plan, spans, library, deliverable_id=plan.deliverable_id, total_ms=total)
    second = cut_cue_sheet(plan, spans, library, deliverable_id=plan.deliverable_id, total_ms=total)
    assert first.model_dump_json() == second.model_dump_json()


def test_no_transition_on_the_first_scene_or_on_a_flash(library) -> None:
    plan = sample_story_plan()
    spans = [
        SceneSpan(scene_id="scn_title000001", kind="title", start_ms=0, duration_ms=4000),
        SceneSpan(scene_id="scn_flash000001", kind="callout", start_ms=4000, duration_ms=300),
        SceneSpan(scene_id="scn_number00001", kind="big_number", start_ms=4300, duration_ms=4000),
    ]
    sheet = cut_cue_sheet(
        plan, spans, library, deliverable_id=plan.deliverable_id, total_ms=8300, bed=False
    )
    marked = {c.scene_id for c in sheet.cues if c.role == SoundCueRole.transition}
    # Nothing to cut *from* on the first scene, and a whoosh on every scene of a run of flashes
    # is a stutter rather than an edit.
    assert "scn_title000001" not in marked
    assert "scn_flash000001" not in marked
    assert "scn_number00001" in marked
    assert 300 < MIN_SCENE_MS_FOR_TRANSITION


def test_a_scene_kind_with_no_unambiguous_sound_gets_no_accent(library) -> None:
    """An invented sound on a quote card is worse than a silent one."""
    plan = sample_story_plan()
    spans = [
        SceneSpan(scene_id="scn_quote000001", kind="quote", start_ms=0, duration_ms=4000),
        SceneSpan(scene_id="scn_image000001", kind="image", start_ms=4000, duration_ms=4000),
    ]
    sheet = cut_cue_sheet(
        plan, spans, library, deliverable_id=plan.deliverable_id, total_ms=8000, bed=False
    )
    assert not [c for c in sheet.cues if c.role == SoundCueRole.accent]
    assert "quote" not in ACCENTS and "image" not in ACCENTS


def test_a_beat_with_several_scenes_splits_its_span(library) -> None:
    """The mix has no finer information than the beat, and dividing the beat is closer than
    stacking every scene's accent on its first frame."""
    plan = sample_story_plan()
    beat_id = plan.scenes[0].beat_id
    doubled = plan.model_copy(
        update={
            "scenes": (
                plan.scenes[0],
                plan.scenes[1].model_copy(update={"beat_id": beat_id}),
            )
        }
    )
    spans = spans_from_beats(doubled, [(beat_id, 0, 4000)])
    assert [s.start_ms for s in spans] == [0, 2000]
    assert [s.duration_ms for s in spans] == [2000, 2000]


def test_a_scene_whose_beat_was_never_spoken_contributes_nothing(library) -> None:
    plan = sample_story_plan()
    assert spans_from_beats(plan, [("beat_999999999", 0, 4000)]) == []


def test_place_sfx_renders_exactly_the_length_of_the_mix(library, tmp_path: Path) -> None:
    plan = sample_story_plan()
    beats = [(b.beat_id, i * 4000, i * 4000 + 3800) for i, b in enumerate(plan.beats)]
    sheet = cut_cue_sheet(
        plan,
        spans_from_beats(plan, beats),
        library,
        deliverable_id=plan.deliverable_id,
        total_ms=16000,
    )
    out = tmp_path / "cues.wav"
    facts = place_sfx(sheet, library, out)
    assert facts["cues"] == len(sheet.cues)
    assert out.is_file()
    # Exact, because the cue track is folded into a mix of a known length.
    assert abs(_duration_s(out) - 16.0) < 0.05


def test_an_empty_cue_sheet_renders_silence_of_the_right_length(library, tmp_path: Path) -> None:
    """So callers need no special case: they fold in a track that changes nothing."""
    plan = sample_story_plan()
    sheet = cut_cue_sheet(plan, [], library, deliverable_id=plan.deliverable_id, total_ms=5000)
    empty = sheet.model_copy(update={"cues": ()})
    out = tmp_path / "silence.wav"
    facts = place_sfx(empty, library, out)
    assert facts["cues"] == 0
    assert abs(_duration_s(out) - 5.0) < 0.05


def test_a_sheet_cut_against_a_different_library_is_refused(library) -> None:
    """A sound id that resolves to different bytes is a different mix."""
    plan = sample_story_plan()
    beats = [(b.beat_id, 0, 4000) for b in plan.beats[:1]]
    sheet = cut_cue_sheet(
        plan,
        spans_from_beats(plan, beats),
        library,
        deliverable_id=plan.deliverable_id,
        total_ms=4000,
    )
    moved = sheet.model_copy(update={"library_sha256": "0" * 64})
    with pytest.raises(CueError, match="different sound library"):
        resolve(moved, library)


def test_a_cue_naming_a_sound_the_library_lacks_is_refused_by_name(library) -> None:
    plan = sample_story_plan()
    beats = [(b.beat_id, 0, 4000) for b in plan.beats[:1]]
    sheet = cut_cue_sheet(
        plan,
        spans_from_beats(plan, beats),
        library,
        deliverable_id=plan.deliverable_id,
        total_ms=4000,
    )
    broken = json.loads(sheet.model_dump_json())
    broken["cues"][0]["sound_id"] = "nonexistent_sound"
    from content_factory.schemas.audio import CueSheet

    with pytest.raises(CueError, match="nonexistent_sound"):
        resolve(CueSheet.model_validate(broken), library)


def test_a_non_loopable_sound_cannot_be_stretched_over_a_span(library) -> None:
    """`thunder_distant` is 16.5 s and not a loop; running it as a 60 s bed would either
    truncate it or splice it audibly, and both are decisions a cue sheet has to make explicitly."""
    from content_factory.schemas.audio import CueSheet, SoundCue

    sheet = CueSheet(
        deliverable_id="dlv_short0000001",
        library_sha256=library.digest,
        total_ms=60000,
        cues=(
            SoundCue(
                cue_id="cue_00000000001",
                sound_id="thunder_distant",
                role=SoundCueRole.bed,
                at_ms=0,
                gain_db=-9.0,
                duration_ms=60000,
                reason="a bed of something that is not a loop",
            ),
        ),
    )
    assert not library.get("thunder_distant").loopable
    with pytest.raises(CueError, match="not marked loopable"):
        resolve(sheet, library)
