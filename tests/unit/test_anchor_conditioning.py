"""A skeleton of nobody is not conditioning — it is a scheduler switch."""

from __future__ import annotations

from pathlib import Path

from content_factory.schemas.fixtures import sample_motion_plan
from content_factory.schemas.shots import (
    CameraKeyframe,
    CameraSpec,
    ControlBundle,
    EnvironmentSpec,
    ShotSpec,
)
from content_factory.sequences.control_compile import compile_bundle_from_motion_plan
from content_factory.workflows.stages import _conditioning_for_frame

SUBJECT_LINE = "one open pine cone on a plain grey slate"
"""The label `audio-picture-story` put on the bundle's subject, which is the whole point: the
fixture is labelled with the film's subject while carrying the joints of two hands."""

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00"
    b"\x00\x00IEND\xaeB`\x82"
)


def _bundle(tmp: Path) -> ControlBundle:
    """The bundle `audio-picture-story` actually compiles, built the way that lane builds it."""
    plan = sample_motion_plan()
    subject = plan.subjects[0].model_copy(update={"label": SUBJECT_LINE})
    plan = plan.model_copy(update={"subjects": (subject,)})
    return compile_bundle_from_motion_plan(plan, tmp, shot_id="shot_pinecone01", anchor_frames=(0,))


def _staged(tmp: Path) -> tuple[ControlBundle, Path]:
    """A bundle with a pose_skeleton track, and the frames on disk for it to read."""
    bundle = _bundle(tmp)
    for track in bundle.tracks:
        d = tmp / track.kind.value / "frames"
        d.mkdir(parents=True, exist_ok=True)
        (d / "0000.png").write_bytes(PNG)
    return bundle, tmp


def _shot(**kw) -> ShotSpec:
    """What `plan_shots` writes for a lane that stages nobody: no characters at all."""
    base = {
        "shot_id": "shot_pinecone01",
        "beat_id": "beat_000000001",
        "camera": CameraSpec(
            keyframes=(
                CameraKeyframe(frame_index=0, position=(0.0, -4.0, 1.6), look_at=(0.0, 0.0, 1.0)),
            )
        ),
        "environment": EnvironmentSpec(),
        "characters": [],
        "frame_count": 9,
        "fps": 24,
        "order": 0,
        "width": 1024,
        "height": 576,
        "description": SUBJECT_LINE,
    }
    return ShotSpec.model_validate({**base, **kw})


def test_the_bundle_carries_the_hand_fixture_as_its_subject() -> None:
    """The fact that made the obvious fix wrong: there *is* a subject, and it is two hands."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        bundle = _bundle(Path(tmp))
    subject = bundle.subjects[0]
    assert subject.subject_id == "subj_hands0001"
    first = subject.poses[0]
    assert first is not None, "frame 0 must carry a pose; the bundle is keyed on first and last"
    assert set(first.joints) == {"l_wrist", "l_index", "r_wrist", "r_index"}
    assert subject.label == SUBJECT_LINE


def test_a_shot_that_stages_no_figures_sends_no_skeleton(tmp_path: Path) -> None:
    bundle, root = _staged(tmp_path)
    shot = _shot()
    assert not shot.characters  # what audio-picture-story plans: characters=none
    cond, slots = _conditioning_for_frame(bundle, root, 0, shot, wanted=("pose_skeleton",))
    assert [s["role"] for s in slots] == []
    assert cond.reference_pngs == ()


def test_a_shot_with_figures_still_gets_its_skeleton(tmp_path: Path) -> None:
    """The pass exists for staged figures and must keep working for them."""
    bundle, root = _staged(tmp_path)
    shot = _shot(
        characters=[
            {"id": "subj_one00000001", "asset": "figure", "appearance": "a person in plain clothes"}
        ]
    )
    assert shot.characters
    _cond, slots = _conditioning_for_frame(bundle, root, 0, shot, wanted=("pose_skeleton",))
    assert [s["role"] for s in slots] == ["pose_skeleton"]
