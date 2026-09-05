"""Shot planner: one shot per beat, camera presets by scene kind, LTX-friendly frame counts."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from content_factory.schemas.fixtures import sample_shot_plan, sample_story_plan
from content_factory.schemas.shots import CameraPreset
from content_factory.shots import (
    PLANNER_VERSION,
    SCENE_KIND_PRESET,
    anchor_frames_for,
    aspect_scale,
    camera_keyframes,
    load_fixture_plan,
    plan_shots_from_story,
)
from content_factory.shots.planner import snap_ltx_length

REPO = Path(__file__).resolve().parents[2]


def test_snap_to_ltx_length() -> None:
    assert snap_ltx_length(1) == 9
    assert snap_ltx_length(96) == 97
    assert snap_ltx_length(100) == 97
    assert snap_ltx_length(102) == 105
    assert snap_ltx_length(10_000) == 257
    for n in range(1, 400):
        assert (snap_ltx_length(n) - 1) % 8 == 0


def test_one_shot_per_beat_in_order_and_deterministic() -> None:
    story = sample_story_plan()
    a = plan_shots_from_story(story, width=1024, height=576, fps=24)
    b = plan_shots_from_story(story, width=1024, height=576, fps=24)
    assert a == b
    assert a.content_hash() == b.content_hash()
    assert len(a.shots) == len(story.beats)
    assert [s.order for s in a.shots] == list(range(len(story.beats)))
    assert [s.beat_id for s in a.shots] == [
        beat.beat_id for beat in sorted(story.beats, key=lambda x: x.order)
    ]
    assert a.story_plan_hash == story.content_hash()
    assert a.planner == "story_presets" and a.planner_version == PLANNER_VERSION
    assert a.deliverable_id == story.deliverable_id


def test_frame_counts_are_ltx_lengths_and_anchors_land_on_legible_frames() -> None:
    """Anchors used to sit at both ends unconditionally. They still do wherever the last frame is
    a frame worth generating, and a push-in's is not: it ends nearer than a whole figure fits, so
    its second anchor moves back to where the body is still in frame."""
    plan = plan_shots_from_story(sample_story_plan(), width=1024, height=576, fps=24)
    for shot in plan.shots:
        assert (shot.frame_count - 1) % 8 == 0
        assert shot.anchor_frames == anchor_frames_for(shot.camera.preset, shot.frame_count)
        if shot.camera.preset is not CameraPreset.slow_push_in:
            assert shot.anchor_frames == (0, shot.frame_count - 1)
        else:
            assert shot.anchor_frames[-1] < shot.frame_count - 1
        assert shot.camera.keyframes[-1].frame_index <= shot.frame_count - 1
        assert shot.width == 1024 and shot.height == 576 and shot.fps == 24
        assert shot.characters[0].asset == "man_01"


def test_presets_follow_scene_kind() -> None:
    story = sample_story_plan()
    plan = plan_shots_from_story(story, width=1024, height=576, fps=24)
    kind_by_beat = {scene.beat_id: scene.kind for scene in reversed(story.scenes)}
    for shot in plan.shots:
        expected = SCENE_KIND_PRESET.get(
            kind_by_beat.get(shot.beat_id or "", ""), CameraPreset.static
        )
        assert shot.camera.preset == expected


def test_unsnapped_planner_keeps_raw_frame_counts() -> None:
    story = sample_story_plan()
    plan = plan_shots_from_story(story, width=512, height=896, fps=30, snap_to_ltx_length=False)
    for shot, beat in zip(plan.shots, sorted(story.beats, key=lambda b: b.order), strict=True):
        ms = beat.planned_duration_ms or 4000
        assert shot.frame_count == min(max(round(ms / 1000 * 30), 1), 600)


def test_camera_presets_geometry() -> None:
    static = camera_keyframes(CameraPreset.static, 97)
    assert len(static) == 1 and static[0].position[1] < 0  # in front of the subject (-Y side)
    push = camera_keyframes(CameraPreset.slow_push_in, 97)
    assert [k.frame_index for k in push] == [0, 96]
    assert abs(push[0].position[1]) > abs(push[1].position[1])  # closer at the end
    pan = camera_keyframes(CameraPreset.pan_right, 49)
    assert pan[0].position[0] < 0 < pan[1].position[0]
    orbit = camera_keyframes(CameraPreset.orbit_left, 25)
    assert orbit[0].position[0] > 0 > orbit[1].position[0]
    crane = camera_keyframes(CameraPreset.crane_up, 9)
    assert crane[0].position[2] < crane[1].position[2]
    one = camera_keyframes(CameraPreset.slow_pull_out, 1)
    assert len(one) == 1
    with pytest.raises(ValueError, match="frame_count"):
        camera_keyframes(CameraPreset.static, 0)
    for preset in CameraPreset:
        for k in camera_keyframes(preset, 33):
            assert k.look_at is not None and k.focus_distance > 0


def test_fixture_file_matches_the_fixture_builder() -> None:
    loaded = load_fixture_plan(REPO / "fixtures" / "shots" / "demo.json")
    assert loaded == sample_shot_plan()
    assert loaded.content_hash() == sample_shot_plan().content_hash()


def test_sixteen_by_nine_is_the_calibration_aspect_and_does_not_move() -> None:
    """The preset distances were chosen by eye on 16:9, so correcting other aspects must leave
    that one untouched. A default call and an explicit 16:9 call have to agree exactly, or every
    plan hash in the repo shifts for no reason."""
    for preset in CameraPreset:
        assert camera_keyframes(preset, 33) == camera_keyframes(preset, 33, width=1024, height=576)
    assert aspect_scale(1024, 576) == pytest.approx(1.0)


def test_a_narrower_frame_brings_the_camera_in() -> None:
    """A 35 mm lens sees far more vertically in a portrait frame, so the same distance leaves the
    figure at a fraction of the height it had in widescreen. Measured on a rendered vertical film,
    every preset shot opened at 0.216 of frame height, under the 0.33 cliff."""
    assert aspect_scale(576, 1024) == pytest.approx(0.3164, abs=1e-4)
    assert aspect_scale(1024, 1024) == pytest.approx(0.5625, abs=1e-4)
    wide = camera_keyframes(CameraPreset.static, 33)[0]
    tall = camera_keyframes(CameraPreset.static, 33, width=576, height=1024)[0]
    assert abs(tall.position[1]) < abs(wide.position[1])
    assert tall.look_at == wide.look_at, "the subject did not move, only the camera"


def test_scaling_about_the_look_at_point_preserves_every_angle() -> None:
    """This is what keeps a preset a preset. Scaling the ground distance alone would steepen the
    camera as it came in and turn a crane's arc into a different arc; scaling the whole offset
    holds elevation and azimuth exactly and changes only how far away the move happens."""
    for preset in CameraPreset:
        wide = camera_keyframes(preset, 33)
        tall = camera_keyframes(preset, 33, width=576, height=1024)
        assert len(wide) == len(tall)
        for a, b in zip(wide, tall, strict=True):
            assert a.look_at is not None and b.look_at is not None
            assert a.look_at == b.look_at
            wo = [a.position[i] - a.look_at[i] for i in range(3)]
            to = [b.position[i] - b.look_at[i] for i in range(3)]
            wn = math.dist(wo, (0.0, 0.0, 0.0))
            tn = math.dist(to, (0.0, 0.0, 0.0))
            if wn < 1e-9:
                continue
            # Same direction, shorter offset: the dot product of the unit vectors is 1.
            unit = sum(wo[i] * to[i] for i in range(3)) / (wn * tn)
            assert unit == pytest.approx(1.0, abs=1e-6), preset.value
            assert tn < wn


def test_a_frame_with_no_area_is_refused() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        aspect_scale(0, 576)


def test_a_push_in_does_not_anchor_the_frame_it_crops() -> None:
    """The move ends nearer than a whole figure fits, so the last frame is the wrong one to hand
    the image model: a cropped body gives the pose skeleton fewer joints to place. Measured off
    rendered layout boxes, the figure stops fitting at 0.36 of the move in 16:9 and 0.32 in 9:16.
    """
    anchors = anchor_frames_for(CameraPreset.slow_push_in, 145)
    assert anchors[0] == 0
    assert anchors[-1] < 144, "anchoring the last frame is the bug"
    assert anchors[-1] / 144 < 0.32, "and it has to sit under both measurements"


def test_every_other_preset_still_anchors_the_last_frame() -> None:
    """Only a move that ends closer than it starts has this problem, and pulling anchors in for
    the rest would cost temporal coverage for nothing."""
    for preset in CameraPreset:
        if preset is CameraPreset.slow_push_in:
            continue
        assert anchor_frames_for(preset, 145) == (0, 144), preset.value


def test_a_pull_out_is_deliberately_left_alone() -> None:
    """Its close end is frame 0, which ShotSpec requires to be an anchor, so no choice of anchors
    can avoid the crop. That one needs a camera change or nothing, and pretending otherwise by
    moving the *other* anchor would hide it."""
    assert anchor_frames_for(CameraPreset.slow_pull_out, 145) == (0, 144)


def test_anchor_frames_always_satisfy_the_shot_contract() -> None:
    """``ShotSpec`` requires anchors to start at 0, strictly increase, and stay inside the shot."""
    for preset in CameraPreset:
        for frames in (1, 2, 3, 9, 49, 97, 145, 600):
            anchors = anchor_frames_for(preset, frames)
            assert anchors[0] == 0
            assert list(anchors) == sorted(set(anchors))
            assert anchors[-1] < frames, (preset.value, frames)


def test_a_single_frame_shot_has_one_anchor_and_no_frames_is_refused() -> None:
    for preset in CameraPreset:
        assert anchor_frames_for(preset, 1) == (0,)
    with pytest.raises(ValueError, match="frame_count must be >= 1"):
        anchor_frames_for(CameraPreset.static, 0)
