"""ShotPlan / ShotSpec / ControlBundle contracts: validators, hashing, registry export."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from content_factory.schemas import shots
from content_factory.schemas.fixtures import (
    ZERO_HASH,
    all_fixtures,
    invalid_fixtures,
    sample_control_bundle,
    sample_shot_plan,
)
from content_factory.schemas.registry import SCHEMA_REGISTRY, json_schema_for
from content_factory.schemas.sequences import ControlAsset, ControlKind


def _shot(**overrides: object) -> shots.ShotSpec:
    base = sample_shot_plan().shots[0].model_dump()
    base.update(overrides)
    return shots.ShotSpec.model_validate(base)


def test_fixture_plan_is_valid_and_hash_stable() -> None:
    a, b = sample_shot_plan(), sample_shot_plan()
    assert a.content_hash() == b.content_hash()
    assert len(a.shots) == 2
    assert a.shots[0].anchor_frames == (0, 96)
    assert a.shots[0].render.passes == shots.DEFAULT_PASSES


def test_json_round_trip_preserves_hash() -> None:
    plan = sample_shot_plan()
    again = shots.ShotPlan.model_validate_json(plan.model_dump_json())
    assert again == plan
    assert again.content_hash() == plan.content_hash()


def test_anchor_frame_must_be_within_shot() -> None:
    with pytest.raises(ValidationError, match="anchor frame 97 >= frame_count"):
        _shot(anchor_frames=(0, 97))


def test_anchor_frames_must_start_at_zero_and_increase() -> None:
    with pytest.raises(ValidationError, match="start with 0"):
        _shot(anchor_frames=(5, 96))
    with pytest.raises(ValidationError, match="start with 0"):
        _shot(anchor_frames=(0, 96, 48))


def test_render_frames_subset_must_cover_anchors() -> None:
    spec = _shot(render={"frames": (0, 48, 96)})
    assert spec.render.frames == (0, 48, 96)
    with pytest.raises(ValidationError, match="must include every anchor frame"):
        _shot(render={"frames": (0, 48)})


def test_camera_keyframes_start_at_zero_and_increase() -> None:
    kf = sample_shot_plan().shots[0].camera.keyframes
    with pytest.raises(ValidationError, match="start at frame 0"):
        shots.CameraSpec(keyframes=(kf[1],))
    with pytest.raises(ValidationError, match="strictly increasing"):
        shots.CameraSpec(keyframes=(kf[0], kf[1], kf[1]))


def test_camera_keyframe_needs_exactly_one_orientation() -> None:
    with pytest.raises(ValidationError, match="exactly one of look_at"):
        shots.CameraKeyframe(frame_index=0, position=(0, 5, 1))
    with pytest.raises(ValidationError, match="exactly one of look_at"):
        shots.CameraKeyframe(
            frame_index=0, position=(0, 5, 1), look_at=(0, 0, 1), rotation_euler_deg=(90, 0, 0)
        )


def test_ids_and_seg_ids_unique_within_shot() -> None:
    man = sample_shot_plan().shots[0].characters[0]
    with pytest.raises(ValidationError, match="ids must be unique"):
        _shot(characters=(man, man))
    with pytest.raises(ValidationError, match="seg_id values must be unique"):
        _shot(
            characters=(man.model_copy(update={"seg_id": 3}),),
            props=(
                shots.PropSpec(id="crate", seg_id=3),
                shots.PropSpec(id="lamp", seg=False, seg_id=3),  # seg=False: not counted
                shots.PropSpec(id="box", seg_id=3),
            ),
        )


def test_plan_requires_shared_size_and_sequential_order() -> None:
    plan = sample_shot_plan()
    first, second = plan.shots
    with pytest.raises(ValidationError, match="share fps, width and height"):
        shots.ShotPlan.model_validate(
            {**plan.model_dump(), "shots": (first, second.model_copy(update={"width": 512}))}
        )
    with pytest.raises(ValidationError, match="expected 1"):
        shots.ShotPlan.model_validate(
            {**plan.model_dump(), "shots": (first, second.model_copy(update={"order": 5}))}
        )


def test_pose_ref_discriminator() -> None:
    clip = shots.CharacterSpec.model_validate(
        {
            "id": "walker",
            "asset": "man_01",
            "pose": {"kind": "clip", "name": "walk_cycle", "speed": 1.5},
        }
    )
    assert isinstance(clip.pose, shots.ClipPose)
    with pytest.raises(ValidationError):
        shots.CharacterSpec.model_validate(
            {"id": "x", "asset": "man_01", "pose": {"kind": "mocap"}}
        )


def test_control_bundle_fixture_is_valid() -> None:
    bundle = sample_control_bundle()
    assert {t.kind for t in bundle.tracks} == {ControlKind.pose_skeleton, ControlKind.layout_boxes}
    assert bundle.subjects[0].segmentation_index == 1
    assert len(bundle.subjects[0].layouts) == bundle.frame_count
    assert bundle.camera == ()


def test_control_bundle_rejects_duplicate_kinds_and_bad_lengths() -> None:
    bundle = sample_control_bundle()
    dump = bundle.model_dump()
    with pytest.raises(ValidationError, match="unique kinds"):
        shots.ControlBundle.model_validate({**dump, "tracks": (bundle.tracks[0], bundle.tracks[0])})
    short_subject = bundle.subjects[0].model_copy(update={"layouts": (None, None)})
    with pytest.raises(ValidationError, match="frame_count entries"):
        shots.ControlBundle.model_validate({**dump, "subjects": (short_subject,)})
    with pytest.raises(ValidationError, match="frame index >= frame_count"):
        shots.ControlBundle.model_validate({**dump, "frame_count": 4})


def test_control_track_rejects_kind_mismatch() -> None:
    asset = ControlAsset(
        kind=ControlKind.depth,
        frame_index=0,
        width=64,
        height=64,
        png_sha256=ZERO_HASH,
        motion_plan_hash=ZERO_HASH,
        compiler_version="0.1.0",
        compiler="blender",
    )
    with pytest.raises(ValidationError, match="contains an asset of kind"):
        shots.ControlTrack(
            kind=ControlKind.normals, encoding=shots.ControlEncoding.rgb8, frames=(asset,)
        )


def test_control_asset_defaults_keep_existing_payloads_valid() -> None:
    asset = ControlAsset(
        kind=ControlKind.pose_skeleton,
        frame_index=0,
        width=64,
        height=64,
        png_sha256=ZERO_HASH,
        motion_plan_hash=ZERO_HASH,
        compiler_version="0.1.0",
    )
    assert asset.compiler == "motion_plan"
    assert asset.shot_id is None


def test_registry_exports_the_new_contracts() -> None:
    for name in ("ShotSpec", "ShotPlan", "ControlBundle"):
        assert name in SCHEMA_REGISTRY
        schema = json_schema_for(name)
        assert schema["$id"].endswith(f"{name}.schema.json")
    valid, invalid = all_fixtures(), invalid_fixtures()
    assert len(valid["ShotSpec"]) == 2 and valid["ControlBundle"]
    # Five: unknown field, bad planner, bad render pass, an empty character appearance and an
    # over-long end_state. The last two are prompt text, where "" is a shot that silently tells
    # the image model nothing rather than a permissive default.
    assert len(invalid["ShotPlan"]) == 5
