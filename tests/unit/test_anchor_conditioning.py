"""A skeleton of nobody is not conditioning — it is a scheduler switch.

`_conditioning_for_frame`'s own docstring warns that "the number of slots selects the whole
editing recipe", because upstream branches on `len(ref_images) == 1`. It names one way in: a run
whose identity sheet was missing. This is the other way in, and it is the one that bit.

Measured on `audio-picture-story` 2026-09-10. Six anchors of "one open pine cone on a plain grey
slate" came back covered in white speckle. The marker said `references: 1`, the single slot a
`pose_skeleton` — and the bundle's one "subject" was `subj_hands0001`, the motion_plan compiler's
**builtin hand-gesture fixture**, carrying joints for two wrists and two index fingers and
labelled with the pine cone's own subject line. So a skeleton of two hands was drawn over an
object and then sent as the anchor's only reference, which is upstream's `is_editing` branch, and
that routes the dev recipe onto `flow_match`. `skills/image/hidream/server.py` has the number for
what that costs: speckle at 0.175 of pixels above a luma gradient of 60, against 0.0004 on
`flash`. The same prompt with no references renders clean on the same server at either aspect.

The switch is therefore the *shot's* character list, not the bundle's subject list — the bundle
has a subject either way.
"""

from __future__ import annotations

import json
from pathlib import Path

from content_factory.schemas.shots import ControlBundle, ShotSpec
from content_factory.workflows.stages import _conditioning_for_frame

BUNDLE_JSON = Path(
    "output/overnight/ps1-pinecone/deliverables/dlv_short0000001/controls/"
    "shot_1cc947940c5d/bundle.json"
)
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00"
    b"\x00\x00IEND\xaeB`\x82"
)


def _staged(tmp: Path) -> tuple[ControlBundle, Path]:
    """A bundle with a pose_skeleton track, and the frames on disk for it to read."""
    raw = json.loads(BUNDLE_JSON.read_text())
    bundle = ControlBundle.model_validate(raw)
    for track in bundle.tracks:
        d = tmp / track.kind.value / "frames"
        d.mkdir(parents=True, exist_ok=True)
        (d / "0000.png").write_bytes(PNG)
    return bundle, tmp


def _shot(**kw) -> ShotSpec:
    plan = json.loads(
        Path(
            "output/overnight/ps1-pinecone/deliverables/dlv_short0000001/shots/plan.json"
        ).read_text()
    )
    return ShotSpec.model_validate({**plan["shots"][0], **kw})


def test_the_bundle_carries_the_hand_fixture_as_its_subject() -> None:
    """The fact that made the obvious fix wrong: there *is* a subject, and it is two hands."""
    raw = json.loads(BUNDLE_JSON.read_text())
    subject = raw["subjects"][0]
    assert subject["subject_id"] == "subj_hands0001"
    assert set(subject["poses"][0]["joints"]) == {"l_wrist", "l_index", "r_wrist", "r_index"}
    assert subject["label"] == "one open pine cone on a plain grey slate"


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
