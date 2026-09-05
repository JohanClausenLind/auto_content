"""Phase-7 sequence gate: hub-and-spoke from the anchor with locks intact; injected drift caught
and regenerated from the anchor; flipbook PDF + animated preview assertions; a single-frame
revision rebuilds only that frame; the edit-instruction compiler emits the preserve list and
exactly one delta."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pypdf import PdfReader

from content_factory.schemas.sequences import (
    Box,
    FrameDelta,
    GenerationLock,
    MotionPlan,
    SubjectKeyframe,
    TrackedSubject,
)
from content_factory.sequences.engine import (
    ControlConditioning,
    MockReferenceEditBackend,
    ReferenceEditBackend,
    build_sequence,
)
from content_factory.sequences.instructions import PRESERVE_LIST, compile_edit_instruction

LOCK = GenerationLock(
    workflow_package_id="fixture.reference-edit",
    workflow_package_version="0.1.0",
    model_revision="mock-1",
    width=256,
    height=144,
    seed=7,
    sampler="euler",
    steps=8,
    guidance=2.5,
    style_prompt="flat editorial illustration",
    camera_prompt="fixed camera",
    lighting_prompt="soft daylight",
    background_prompt="plain warm paper",
    reference_asset_sha256="0" * 64,
)


def six_keyframe_plan() -> MotionPlan:
    kfs = tuple(
        SubjectKeyframe(frame_index=i, layout=Box(x=0.1 + 0.12 * i, y=0.45, w=0.16, h=0.25))
        for i in range(6)
    )
    return MotionPlan(
        plan_id="mp_flipbook0001",
        sequence_id="seq_flipbook001",
        frame_count=6,
        canvas_width=256,
        canvas_height=144,
        subjects=(
            TrackedSubject(subject_id="subj_char00001", label="the character", keyframes=kfs),
        ),
    )


def test_edit_instruction_contains_preserve_list_and_exactly_one_delta() -> None:
    text = compile_edit_instruction(
        FrameDelta(
            kind="move_subject",
            subject_label="hands",
            instruction="move both hands toward the centre by ~5% of image width",
        ),
        LOCK,
    )
    for item in PRESERVE_LIST:
        assert item in text
    assert text.count("exactly ONE change") == 1
    assert "move both hands toward the centre" in text
    assert "Everything else unchanged." in text


def test_flipbook_builds_hub_and_spoke_with_locks_intact(tmp_path: Path) -> None:
    backend = MockReferenceEditBackend()
    result = build_sequence(six_keyframe_plan(), LOCK, backend, tmp_path)
    assert not result.failed and len(result.frames) == 6
    # Hub-and-spoke: every generation call referenced the ANCHOR bytes, never a previous frame.
    assert {c["anchor_sha"] for c in backend.calls} == {result.anchor_sha256}
    # Locks intact: every frame recorded the identical lock hash and reference=anchor.
    assert len({f["lock_sha256"] for f in result.frames}) == 1
    assert all(f["reference"] == "anchor" for f in result.frames)
    # Packaging assertions: contact sheet, preview, and print-ready flipbook PDF.
    pkg = result.packaging
    assert pkg["frames"] == 6
    reader = PdfReader(pkg["flipbook_pdf"])
    assert len(reader.pages) == 6
    box = reader.pages[0].mediabox
    assert box.width > box.height  # binding margin makes pages landscape
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", pkg["preview"]],
        capture_output=True,
        text=True,
        check=True,
    )
    stream = json.loads(probe.stdout)["streams"][0]
    assert int(stream["nb_frames"]) == 6 and stream["codec_name"] == "h264"


def test_injected_drifted_frame_is_caught_and_regenerated_from_anchor(tmp_path: Path) -> None:
    backend = MockReferenceEditBackend(drift_at=(3, 1))  # frame 3, first attempt drifts
    result = build_sequence(six_keyframe_plan(), LOCK, backend, tmp_path)
    assert not result.failed
    assert 3 in result.regenerated
    frame3 = next(f for f in result.frames if f["frame_index"] == 3)
    assert frame3["attempts"] == 2
    # The regeneration also came from the anchor, not from frame 2.
    calls_f3 = [c for c in backend.calls if "frame 3" in c["instruction"]]
    assert len(calls_f3) == 2 and all(c["anchor_sha"] == result.anchor_sha256 for c in calls_f3)


def test_persistent_drift_fails_honestly(tmp_path: Path) -> None:
    class AlwaysDrift(MockReferenceEditBackend):
        def edit(self, anchor_png, control_png, instruction, lock, *, attempt):
            self.drift_at = None
            png = super().edit(anchor_png, control_png, instruction, lock, attempt=attempt)
            import io

            from PIL import Image

            img = Image.open(io.BytesIO(png)).point(lambda v: 255 - v)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

    result = build_sequence(
        six_keyframe_plan(), LOCK, AlwaysDrift(), tmp_path, max_regen_attempts=2
    )
    assert result.failed  # bounded attempts, then honest failure — never silently accepted


def test_single_frame_revision_rebuilds_only_that_frame(tmp_path: Path) -> None:
    backend = MockReferenceEditBackend()
    first = build_sequence(six_keyframe_plan(), LOCK, backend, tmp_path)
    calls_before = len(backend.calls)
    assert calls_before == 6
    # Revision Box on frame 4: a new instruction for that frame only.
    second = build_sequence(
        six_keyframe_plan(),
        LOCK,
        backend,
        tmp_path,
        frame_instructions={4: "Move the character slightly higher for frame 4 ."},
    )
    assert len(backend.calls) == calls_before + 1  # exactly one new generation
    rebuilt = next(f for f in second.frames if f["frame_index"] == 4)
    cached = [f for f in second.frames if f["frame_index"] != 4]
    assert rebuilt["cache_hit"] is False
    assert all(f.get("cache_hit") for f in cached)
    del first


def test_conditioning_changes_the_marker_and_reaches_the_backend(tmp_path: Path) -> None:
    class Recording(MockReferenceEditBackend):
        def __init__(self) -> None:
            super().__init__()
            self.conditioned: list[ControlConditioning] = []

        def edit_conditioned(self, anchor_png, conditioning, instruction, lock, *, attempt):
            self.conditioned.append(conditioning)
            return ReferenceEditBackend.edit_conditioned(
                self, anchor_png, conditioning, instruction, lock, attempt=attempt
            )

    plain = build_sequence(six_keyframe_plan(), LOCK, MockReferenceEditBackend(), tmp_path / "a")
    backend = Recording()
    refs = ControlConditioning(
        reference_pngs=(b"identity-ref",), layout_boxes=(Box(x=0.1, y=0.4, w=0.2, h=0.3),)
    )
    conditioned = build_sequence(
        six_keyframe_plan(), LOCK, backend, tmp_path / "b", conditioning_for=lambda _idx: refs
    )
    assert not plain.failed and not conditioned.failed
    assert len(backend.conditioned) == 6
    assert all(
        c.control_png and c.reference_pngs == (b"identity-ref",) for c in backend.conditioned
    )
    # same frames, different cache keys: conditioning is part of the marker
    assert [f["input_hash"] for f in plain.frames] != [f["input_hash"] for f in conditioned.frames]
    # and a rerun with identical conditioning is a full cache hit
    again = build_sequence(
        six_keyframe_plan(), LOCK, Recording(), tmp_path / "b", conditioning_for=lambda _idx: refs
    )
    assert all(f["cache_hit"] for f in again.frames)
