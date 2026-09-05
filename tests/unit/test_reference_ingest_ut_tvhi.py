"""The two labelled video ingesters: UT-Interaction and TV Human Interactions.

Split in two: the helpers that align the UT spreadsheet and order the TV-HI head orientations are
pure and run everywhere, while anything that needs the 19 GB library under /mnt/fast is skipped on
a host that does not have it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from content_factory.reference.ingest import tvhi as tvhi_ingest
from content_factory.reference.ingest import ut as ut_ingest
from content_factory.schemas.base import file_sha256
from content_factory.schemas.reference import (
    Affection,
    ContactTag,
    InteractionTag,
    Modality,
    Posture,
    ReferenceClip,
    ReferenceSource,
    UsageClass,
)

ROOT = Path("/mnt/fast/reference")
INGESTED_AT = "2026-09-07T12:00:00Z"

needs_library = pytest.mark.skipif(
    not Path("/mnt/fast/reference").is_dir(), reason="reference library not on this host"
)


@pytest.fixture(scope="module")
def ut_result() -> tuple[list[ReferenceClip], list[str]]:
    return ut_ingest.ingest(ROOT, ingested_at=INGESTED_AT)


@pytest.fixture(scope="module")
def tvhi_result() -> tuple[list[ReferenceClip], list[str]]:
    return tvhi_ingest.ingest(ROOT, ingested_at=INGESTED_AT)


@pytest.fixture(scope="module")
def class_maps() -> dict[str, Any]:
    return json.loads((ROOT / "_index" / "measured" / "class_maps.json").read_text())


# --- shape both ingesters must have, so the build driver can call them the same way -------------


def test_both_modules_expose_the_ingester_shape() -> None:
    assert ut_ingest.INGESTER_VERSION == "0.1.0"
    assert tvhi_ingest.INGESTER_VERSION == "0.1.0"
    assert ut_ingest.SOURCE is ReferenceSource.ut_interaction
    assert tvhi_ingest.SOURCE is ReferenceSource.tv_human_interactions


@pytest.mark.parametrize("module", [ut_ingest, tvhi_ingest])
def test_a_root_without_the_measured_index_says_so_instead_of_raising(
    module: Any, tmp_path: Path
) -> None:
    clips, skipped = module.ingest(tmp_path, ingested_at=INGESTED_AT)
    assert clips == []
    assert len(skipped) == 1
    assert "could not be read" in skipped[0]
    assert str(tmp_path) in skipped[0]


# --- pure helpers -------------------------------------------------------------------------------


def test_label_rows_split_at_the_spreadsheets_own_others_marker() -> None:
    rows = [
        {"sequence #": "seq1", "Activity Class ID": 4.0},
        {"sequence #": "seq2", "Activity Class ID": 1.0},
        {"sequence #": "others:", "Activity Class ID": ""},
        {"sequence #": "seq2", "Activity Class ID": 3.0},
        {"sequence #": "seq2", "Activity Class ID": 5.0},
    ]
    primary, others = ut_ingest._split_label_rows(rows)
    assert sorted(primary) == [1, 2]
    assert [row["Activity Class ID"] for row in primary[2]] == [1.0]
    assert others == {2: 2}


def test_row_pairing_stops_at_the_first_class_id_that_disagrees() -> None:
    clips = [{"field_c": 0}, {"field_c": 1}, {"field_c": 2}, {"field_c": 3}]
    rows = [
        {"Activity Class ID": 0.0},
        {"Activity Class ID": 5.0},
        {"Activity Class ID": 2.0},
    ]
    paired = ut_ingest._paired_rows(clips, rows)
    # The second row is for another action, so it is not used and nothing after it slides forward.
    assert paired[0] is rows[0]
    assert paired[1:] == [None, None, None]


def test_a_clip_with_more_actions_than_rows_gets_none_for_the_remainder() -> None:
    clips = [{"field_c": 0}, {"field_c": 4}, {"field_c": 2}]
    rows = [{"Activity Class ID": 0.0}, {"Activity Class ID": 4.0}]
    assert ut_ingest._paired_rows(clips, rows) == [rows[0], rows[1], None]


def test_head_orientations_are_ordered_by_count_then_name() -> None:
    assert tvhi_ingest._angles({"profile_left": 3, "backwards": 9, "frontal_left": 3}) == (
        "backwards",
        "frontal_left",
        "profile_left",
    )


def test_people_counts_report_largest_smallest_and_modal() -> None:
    assert tvhi_ingest._people({"1": 49, "2": 48}) == (2, 1, 1)
    assert tvhi_ingest._people({"2": 10, "3": 10}) == (3, 2, 3)


# --- UT-Interaction against the real library ----------------------------------------------------


@needs_library
def test_ut_emits_every_segmented_clip_and_every_uncut_sequence(
    ut_result: tuple[list[ReferenceClip], list[str]],
) -> None:
    clips, _ = ut_result
    uncut = [clip for clip in clips if clip.clip_id.endswith("_uncut")]
    assert len(clips) == 140
    assert len(uncut) == 20
    assert len(clips) - len(uncut) == 120
    assert [clip.clip_id for clip in clips] == sorted(clip.clip_id for clip in clips)
    assert len({clip.clip_id for clip in clips}) == 140
    assert {clip.source for clip in clips} == {ReferenceSource.ut_interaction}
    assert {clip.modality for clip in clips} == {Modality.video_rgb}
    # Real daylight footage with nothing between the lens and the subjects.
    assert {clip.usage for clip in clips} == {UsageClass.pixels_usable}
    assert {clip.people_count for clip in clips} == {2}
    assert {clip.ingested_at for clip in clips} == {INGESTED_AT}
    assert {clip.ingester_version for clip in clips} == {"0.1.0"}


@needs_library
def test_ut_class_distribution_matches_the_segmented_index(
    ut_result: tuple[list[ReferenceClip], list[str]],
) -> None:
    clips, _ = ut_result
    measured = json.loads((ROOT / "_index" / "measured" / "ut_segmented.json").read_text())
    expected: dict[int, int] = {}
    for entry in measured:
        expected[entry["field_c"]] = expected.get(entry["field_c"], 0) + 1
    tag_for = {
        0: InteractionTag.handshake,
        1: InteractionTag.hug,
        2: InteractionTag.kick,
        3: InteractionTag.point,
        4: InteractionTag.punch,
        5: InteractionTag.push,
    }
    counted: dict[InteractionTag, int] = {}
    for clip in clips:
        if clip.clip_id.endswith("_uncut"):
            continue
        assert len(clip.interaction_tags) == 1
        tag = clip.interaction_tags[0]
        counted[tag] = counted.get(tag, 0) + 1
    assert counted == {tag_for[class_id]: count for class_id, count in expected.items()}


@needs_library
def test_ut_tags_never_contradict_the_verified_class_map(
    ut_result: tuple[list[ReferenceClip], list[str]], class_maps: dict[str, Any]
) -> None:
    clips, _ = ut_result
    verified = class_maps["ut_interaction"]["classes"]
    affectionate = {entry["label"] for entry in verified.values() if entry["affection"] is True}
    assert affectionate == {"hand_shake", "hug"}
    for clip in clips:
        if clip.clip_id.endswith("_uncut"):
            continue
        label = clip.clip_id.split("_seq", 1)[1].split("_", 1)[1].rsplit("_", 1)[0]
        assert label in {entry["label"] for entry in verified.values()}
        if label in affectionate:
            assert clip.affection is Affection.affection
        else:
            assert clip.affection is Affection.aggression


@needs_library
def test_ut_segmented_clip_carries_its_spreadsheet_row_and_the_xls_it_came_from(
    ut_result: tuple[list[ReferenceClip], list[str]],
) -> None:
    clips, _ = ut_result
    hug = next(clip for clip in clips if clip.clip_id == "ut_seq1_hug_1064")
    assert hug.interaction_tags == (InteractionTag.hug,)
    assert hug.contact_tags == (ContactTag.back, ContactTag.torso)
    assert hug.postures == (Posture.standing,)
    assert hug.camera_angles == ("high_angle",)
    assert hug.pose_format == "bbox_only"
    assert hug.pose_root == ut_ingest.LABELS_XLS
    assert hug.source_ref == "segmented_set1/2_1_1.avi"
    assert hug.measured["sequence_start_frame"] == 1064.0
    assert hug.measured["sequence_end_frame"] == 1207.0
    assert hug.measured["main_actor"] == -1.0
    assert [file.role for file in hug.files] == ["video", "bbox"]
    assert [file.path for file in hug.files] == [
        "UT-Interaction/raw/segmented_set1/2_1_1.avi",
        ut_ingest.LABELS_XLS,
    ]
    assert "frames 1064 to 1207" in hug.caption


@needs_library
def test_ut_reports_the_one_segmented_clip_the_spreadsheet_never_annotated(
    ut_result: tuple[list[ReferenceClip], list[str]],
) -> None:
    clips, skipped = ut_result
    assert len(skipped) == 1
    assert skipped[0].startswith("ut 59_10_2: no row for it")
    orphan = next(clip for clip in clips if clip.clip_id == "ut_seq10_kick_clip59")
    assert orphan.pose_format == "none"
    assert orphan.pose_root is None
    assert orphan.measured == {}
    assert [file.role for file in orphan.files] == ["video"]


@needs_library
def test_ut_uncut_sequence_is_the_union_of_its_actions_and_reads_as_aggression(
    ut_result: tuple[list[ReferenceClip], list[str]],
) -> None:
    clips, _ = ut_result
    for clip in clips:
        if not clip.clip_id.endswith("_uncut"):
            continue
        # Every take contains a punch, a kick and a push, so a search for tenderness that excludes
        # aggression must not be answered with one.
        assert clip.affection is Affection.aggression
        assert clip.postures == (Posture.standing, Posture.walking)
        assert ContactTag.none not in clip.contact_tags
        assert "uncut" in clip.caption
        actions = clip.measured["annotated_actions"]
        assert isinstance(actions, float) and actions >= 5.0
    seq1 = next(clip for clip in clips if clip.clip_id == "ut_seq1_uncut")
    assert seq1.interaction_tags == (
        InteractionTag.handshake,
        InteractionTag.hug,
        InteractionTag.kick,
        InteractionTag.point,
        InteractionTag.punch,
        InteractionTag.push,
    )
    assert seq1.contact_tags == (ContactTag.back, ContactTag.hands, ContactTag.torso)
    assert seq1.frame_count == 2031
    assert seq1.measured["annotated_start_frames"] == [
        762.0,
        919.0,
        1064.0,
        1311.0,
        1408.0,
        1593.0,
    ]


# --- TV Human Interactions against the real library ---------------------------------------------


@needs_library
def test_tvhi_emits_all_three_hundred_clips_in_the_verified_class_proportions(
    tvhi_result: tuple[list[ReferenceClip], list[str]], class_maps: dict[str, Any]
) -> None:
    clips, skipped = tvhi_result
    assert skipped == []
    assert len(clips) == 300
    assert [clip.clip_id for clip in clips] == sorted(clip.clip_id for clip in clips)
    assert len({clip.clip_id for clip in clips}) == 300
    counted: dict[tuple[InteractionTag, ...], int] = {}
    for clip in clips:
        counted[clip.interaction_tags] = counted.get(clip.interaction_tags, 0) + 1
    assert counted == {
        (InteractionTag.handshake,): 50,
        (InteractionTag.high_five,): 50,
        (InteractionTag.hug,): 50,
        (InteractionTag.kiss,): 50,
        (InteractionTag.no_contact,): 100,
    }
    expected = class_maps["tv_human_interactions"]["clip_classes"]
    assert counted[(InteractionTag.hug,)] == expected["hug"]
    assert counted[(InteractionTag.kiss,)] == expected["kiss"]
    assert counted[(InteractionTag.no_contact,)] == expected["negative"]


@needs_library
def test_tvhi_negatives_carry_no_contact_and_nothing_else(
    tvhi_result: tuple[list[ReferenceClip], list[str]],
) -> None:
    clips, _ = tvhi_result
    negatives = [clip for clip in clips if clip.clip_id.startswith("tvhi_negative_")]
    assert len(negatives) == 100
    for clip in negatives:
        assert clip.interaction_tags == (InteractionTag.no_contact,)
        assert clip.contact_tags == ()
        assert clip.affection is Affection.neutral
        assert "negative example" in clip.caption


@needs_library
def test_tvhi_puts_the_datasets_own_head_orientations_into_camera_angles(
    tvhi_result: tuple[list[ReferenceClip], list[str]], class_maps: dict[str, Any]
) -> None:
    clips, _ = tvhi_result
    allowed = set(class_maps["tv_human_interactions"]["head_orientation_values"])
    assert allowed == {
        "backwards",
        "frontal_left",
        "frontal_right",
        "profile_left",
        "profile_right",
    }
    seen: set[str] = set()
    for clip in clips:
        assert clip.camera_angles, clip.clip_id
        assert set(clip.camera_angles) <= allowed
        counts = [clip.measured[f"orient_person_frames_{a}"] for a in clip.camera_angles]
        assert counts == sorted(counts, reverse=True)
        seen.update(clip.camera_angles)
    assert seen == allowed


@needs_library
def test_tvhi_people_count_comes_from_the_annotations_own_num_bbxs(
    tvhi_result: tuple[list[ReferenceClip], list[str]],
) -> None:
    clips, _ = tvhi_result
    measured = {
        str(entry["clip"]): entry
        for entry in json.loads((ROOT / "_index" / "measured" / "tvhi.json").read_text())
    }
    counts: dict[int, int] = {}
    for clip in clips:
        entry = measured[clip.source_ref]
        assert clip.people_count == max(int(key) for key in entry["people_per_frame"])
        assert clip.frame_count == entry["nb_frames"]
        assert clip.width == entry["width"] and clip.height == entry["height"]
        counts[clip.people_count] = counts.get(clip.people_count, 0) + 1
    # Not assumed to be two: a third of these clips have somebody else in shot.
    assert counts[2] == 170
    assert max(counts) == 8


@needs_library
def test_tvhi_kiss_is_the_only_kissing_and_keeps_its_annotation_file(
    tvhi_result: tuple[list[ReferenceClip], list[str]],
) -> None:
    clips, _ = tvhi_result
    kiss = next(clip for clip in clips if clip.clip_id == "tvhi_kiss_0001")
    assert kiss.source_ref == "kiss_0001"
    assert kiss.contact_tags == (ContactTag.face,)
    assert kiss.affection is Affection.affection
    assert kiss.pose_format == "bbox_only"
    assert kiss.pose_root == f"{tvhi_ingest.ANNOTATION_DIR}/kiss_0001.annotations"
    assert [file.role for file in kiss.files] == ["video", "bbox"]
    assert kiss.measured["person_frames_kiss"] == 92.0
    assert kiss.usage is UsageClass.pixels_usable


# --- promises both make about their output ------------------------------------------------------


@needs_library
def test_every_listed_file_is_on_disk_with_the_digest_that_was_recorded(
    ut_result: tuple[list[ReferenceClip], list[str]],
    tvhi_result: tuple[list[ReferenceClip], list[str]],
) -> None:
    clips = ut_result[0] + tvhi_result[0]
    checked = 0
    for clip in clips:
        for file in clip.files:
            path = ROOT / file.path
            assert not Path(file.path).is_absolute()
            assert path.is_file(), file.path
            assert file.size_bytes == path.stat().st_size
        # Digesting 750 MB twice is wasteful, so only a sample is re-hashed.
        if checked < 4:
            first = clip.files[0]
            assert first.sha256 == file_sha256(ROOT / first.path)
            checked += 1
    assert checked == 4


@needs_library
def test_both_ingesters_are_byte_identical_on_a_second_run(
    ut_result: tuple[list[ReferenceClip], list[str]],
    tvhi_result: tuple[list[ReferenceClip], list[str]],
) -> None:
    for first, module in ((ut_result, ut_ingest), (tvhi_result, tvhi_ingest)):
        clips, skipped = module.ingest(ROOT, ingested_at=INGESTED_AT)
        assert [clip.canonical_json() for clip in clips] == [
            clip.canonical_json() for clip in first[0]
        ]
        assert skipped == first[1]
