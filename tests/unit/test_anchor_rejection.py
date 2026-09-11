"""A rejected anchor has to be redrawn, and has to come back different.

The keyframe lanes have had this since 2026-09-10. The anchor lanes did not, and it was found the
hard way on `amber-refix` 2026-09-12: three of six frames rejected with written reasons, the resume
reported `cache_hits: 6` on generate_anchor, and the gate blocked again on the identical pictures.
The verdict the gate exists to collect was the one thing the run could not act on.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from content_factory.workflows.stages import (
    _anchor_frame_paths,
    _anchor_rejection_offsets,
)


def test_a_shot_frame_id_maps_to_its_picture(tmp_path: Path) -> None:
    resolved = _anchor_frame_paths(tmp_path, "shot_9ff49b91f418:0003")
    assert resolved is not None
    png, marker, key = resolved
    assert png == tmp_path / "shot_9ff49b91f418" / "0003.png"
    assert marker == tmp_path / "shot_9ff49b91f418" / "0003.done.json"
    assert key == "shot_9ff49b91f418_0003"


def test_the_single_anchor_is_reachable_under_both_spellings(tmp_path: Path) -> None:
    """`review_frames` now writes "anchor:0000", but verdicts already on disk say "None:0000"
    because the manifest records `shot_id: None`. Both have to resolve or a live run breaks."""
    for frame_id in ("anchor:0000", "None:0000", ":0000"):
        resolved = _anchor_frame_paths(tmp_path, frame_id)
        assert resolved is not None, frame_id
        png, marker, key = resolved
        assert png == tmp_path / "anchor.png"
        assert marker == tmp_path / "anchor.done.json"
        assert key == "anchor_0000"


def test_a_keyframe_id_is_not_an_anchors_business(tmp_path: Path) -> None:
    """`frame:0007` belongs to the sequence lanes, which clear their own rejections."""
    assert _anchor_frame_paths(tmp_path, "frame:0007") is not None  # it parses...
    # ...but a malformed one does not, and must not be guessed at.
    assert _anchor_frame_paths(tmp_path, "shot_abc:not-a-number") is None
    assert _anchor_frame_paths(tmp_path, "nonsense") is None


def test_no_rejections_means_no_offset(tmp_path: Path) -> None:
    """The ordinary path has to stay byte-identical: offset 0 is `lock.seed`, attempt 1."""
    assert _anchor_rejection_offsets(tmp_path) == {}
    (tmp_path / "rejected").mkdir()
    assert _anchor_rejection_offsets(tmp_path) == {}


def test_each_rejection_walks_the_seed_a_whole_run_further(tmp_path: Path) -> None:
    """Eight per rejection, not one.

    The three blocker retries inside a single run already walk the seed by 0, 1, 2. An offset of
    one would land the redraw on the *second attempt of the run that was rejected* — a picture the
    reviewer has, in effect, already seen and turned down.
    """
    reject = tmp_path / "rejected"
    reject.mkdir()
    (reject / "shot_a_0000.reviewed1.png").write_bytes(b"")
    (reject / "shot_b_0002.reviewed1.png").write_bytes(b"")
    (reject / "shot_b_0002.reviewed2.png").write_bytes(b"")
    offsets = _anchor_rejection_offsets(tmp_path)
    assert offsets == {"shot_a_0000": 8, "shot_b_0002": 16}


def test_clearing_moves_the_picture_aside_rather_than_deleting_it(tmp_path: Path) -> None:
    """What was turned down still has to be lookable-at — it is the evidence for the reason."""
    from content_factory.workflows.stages import _clear_rejected_anchors

    class _Ctx:
        """Only what `_clear_rejected_anchors` touches: the deliverable dir, and the project dir
        `_log_execution` writes its line into."""

        def __init__(self, root: Path) -> None:
            self._root = root
            self.project_dir = root.parent

        def ddir(self) -> Path:
            return self._root

    ddir = tmp_path / "dlv"
    anchors = ddir / "anchors" / "shot_x"
    anchors.mkdir(parents=True)
    (anchors / "0000.png").write_bytes(b"rejected picture")
    (anchors / "0000.done.json").write_text(json.dumps({"input_hash": "abc"}))
    kept = ddir / "anchors" / "shot_x" / "0001.png"
    kept.write_bytes(b"accepted picture")

    batch = {
        "schema_version": 1,
        "deliverable_id": "dlv_testvideo001",
        "contact_sheet_sha256": "0" * 64,
        "contact_sheet_path": "reviews/frames/contact-sheet.png",
        "created_at": "2026-09-12T12:00:00Z",
        "frames": [
            {
                "frame_id": "shot_x:0000",
                "png_sha256": "1" * 64,
                "findings": [],
                "verdict": "reject",
                "reason": "an open rim: a vessel, not a solid",
            },
            {
                "frame_id": "shot_x:0001",
                "png_sha256": "2" * 64,
                "findings": [],
                "verdict": "accept",
                "reason": "",
            },
        ],
    }
    review_dir = ddir / "reviews" / "frames"
    review_dir.mkdir(parents=True)
    (review_dir / "batch.json").write_text(json.dumps(batch))

    cleared = _clear_rejected_anchors(_Ctx(ddir))  # type: ignore[arg-type]

    assert cleared == ["shot_x:0000"]
    # The marker is gone, so the next run misses its cache and redraws.
    assert not (anchors / "0000.done.json").exists()
    assert not (anchors / "0000.png").exists()
    # The picture is kept, numbered, beside the drift-rejected ones.
    moved = ddir / "anchors" / "rejected" / "shot_x_0000.reviewed1.png"
    assert moved.read_bytes() == b"rejected picture"
    # And the accepted frame is untouched: rejecting one drawing costs one drawing.
    assert kept.read_bytes() == b"accepted picture"
    # A second rejection of the same frame does not overwrite the first.
    assert _anchor_rejection_offsets(ddir / "anchors") == {"shot_x_0000": 8}


def test_a_rejection_that_exists_only_in_the_verdict_still_clears(tmp_path: Path) -> None:
    """The bug this nearly shipped with.

    `batch.json` is what the gate wrote when it last ran; `verdict.json` beside it is what the
    operator answered *afterwards*. Reading only the first works the first time — the gate had
    already merged the previous answer — and then silently stops. Measured on `amber-refix`
    2026-09-12: a second rejection reported `cache_hits: 6` and redrew nothing, because at the
    moment generate_anchor runs the gate's file still describes the previous round, in which that
    frame was `unreviewed`.
    """
    from content_factory.workflows.stages import _clear_rejected_anchors

    class _Ctx:
        def __init__(self, root: Path) -> None:
            self._root = root
            self.project_dir = root.parent

        def ddir(self) -> Path:
            return self._root

    ddir = tmp_path / "dlv"
    shot = ddir / "anchors" / "shot_x"
    shot.mkdir(parents=True)
    (shot / "0000.png").write_bytes(b"picture")
    (shot / "0000.done.json").write_text("{}")

    review = ddir / "reviews" / "frames"
    review.mkdir(parents=True)
    common = {
        "schema_version": 1,
        "deliverable_id": "dlv_testvideo001",
        "contact_sheet_sha256": "0" * 64,
        "contact_sheet_path": "reviews/frames/contact-sheet.png",
        "created_at": "2026-09-12T12:00:00Z",
    }
    frame = {"frame_id": "shot_x:0000", "png_sha256": "1" * 64, "findings": []}
    # The gate's own file says nothing was decided...
    (review / "batch.json").write_text(
        json.dumps({**common, "frames": [{**frame, "verdict": "unreviewed", "reason": ""}]})
    )
    # ...and the operator's answer, written afterwards, rejects it.
    (review / "verdict.json").write_text(
        json.dumps(
            {**common, "frames": [{**frame, "verdict": "reject", "reason": "a corked bottle"}]}
        )
    )

    assert _clear_rejected_anchors(_Ctx(ddir)) == ["shot_x:0000"]  # type: ignore[arg-type]
    assert not (shot / "0000.done.json").exists()


# --- the other half: a redraw must not cost a re-review of the whole set -----------------------


def _batch(frames: list[tuple[str, str, str]]):
    """(frame_id, digest, verdict) -> a FrameReviewBatch."""
    from content_factory.schemas.review import FrameRecord, FrameReviewBatch

    return FrameReviewBatch(
        deliverable_id="dlv_testvideo001",
        contact_sheet_sha256="0" * 64,
        contact_sheet_path="reviews/frames/contact-sheet.png",
        created_at=dt.datetime(2026, 9, 12, 12, 0, tzinfo=dt.UTC),
        frames=tuple(
            FrameRecord(frame_id=fid, png_sha256=sha, findings=(), verdict=v)  # type: ignore[arg-type]
            for fid, sha, v in frames
        ),
    )


def test_an_agent_need_not_re_decide_a_frame_that_was_not_redrawn() -> None:
    """`image-set` promises "rejecting one drawing costs one drawing, not the film".

    Until anchors could actually be redrawn this was untestable: the resume never produced a new
    picture, so nobody reached the second review. Now the merged batch arrives with three accepts
    carried forward and three redraws unreviewed, and only the three have to be named.
    """
    from content_factory.qc.verdict import decide

    merged = _batch(
        [
            ("shot_a:0000", "1" * 64, "accept"),
            ("shot_b:0000", "2" * 64, "unreviewed"),  # redrawn
            ("shot_c:0000", "3" * 64, "accept"),
        ]
    )
    decided = decide(merged, reviewer="agent", accept=["shot_b:0000"])
    by_id = {f.frame_id: f for f in decided.frames}
    assert by_id["shot_b:0000"].verdict == "accept"
    # The two nobody mentioned keep the decision they already carried about the same picture.
    assert by_id["shot_a:0000"].verdict == "accept"
    assert by_id["shot_c:0000"].verdict == "accept"


def test_a_fresh_batch_still_needs_every_frame_named() -> None:
    """The discipline is unchanged where it matters: nothing has been looked at yet."""
    import pytest

    from content_factory.qc.verdict import VerdictRefusedError, decide

    fresh = _batch(
        [("shot_a:0000", "1" * 64, "unreviewed"), ("shot_b:0000", "2" * 64, "unreviewed")]
    )
    with pytest.raises(VerdictRefusedError) as refused:
        decide(fresh, reviewer="agent", accept=["shot_a:0000"])
    assert "shot_b:0000" in str(refused.value)


def test_a_carried_rejection_keeps_its_reason() -> None:
    """The reason is what `prompting propose` reads; dropping it on an unrelated verdict would
    quietly lose the only account of what was wrong."""
    from content_factory.qc.verdict import decide

    merged = _batch([("shot_a:0000", "1" * 64, "reject"), ("shot_b:0000", "2" * 64, "unreviewed")])
    merged = merged.model_copy(
        update={
            "frames": (
                merged.frames[0].model_copy(update={"reason": "an open rim: a vessel"}),
                merged.frames[1],
            )
        }
    )
    decided = decide(merged, reviewer="agent", accept=["shot_b:0000"])
    by_id = {f.frame_id: f for f in decided.frames}
    assert by_id["shot_a:0000"].verdict == "reject"
    assert by_id["shot_a:0000"].reason == "an open rim: a vessel"


# --- the loop closed: what the reviewer said reaches the model ---------------------------------


def test_a_correction_is_appended_as_a_description_not_a_negation() -> None:
    """At guidance 0 a negation is a hint and a description is an instruction.

    `sequences.styles` measured this and the contract asks the reviewer for the positive form, so
    nothing here wraps the text in "avoid" or "not" — it joins the sentence list as written.
    """
    from content_factory.workflows.stages import _with_redirects

    base = "photographic, fine grain. one piece of amber on plain ground."
    out = _with_redirects(base, ["a solid lump of resin with no opening"])
    assert out == (
        "photographic, fine grain. one piece of amber on plain ground."
        " a solid lump of resin with no opening."
    )
    assert "avoid" not in out.lower() and " not " not in out.lower()


def test_no_correction_leaves_the_prompt_byte_identical() -> None:
    """The ordinary path cannot move, or every frame ever drawn regenerates."""
    from content_factory.workflows.stages import _with_redirects

    base = "photographic. one piece of amber."
    assert _with_redirects(base, []) == base
    assert _with_redirects(base, ["", "   "]) == base


def test_corrections_accumulate_across_rounds(tmp_path: Path) -> None:
    """Two rejections give two corrections and both hold — the second does not replace the first."""
    from content_factory.workflows.stages import _redirects_for_frame

    reject = tmp_path / "rejected"
    reject.mkdir()
    assert _redirects_for_frame(tmp_path, "shot_x_0000", "a solid lump") == ("a solid lump",)

    # Round 1 was drawn with nothing and rejected; its marker moved aside.
    (reject / "shot_x_0000.reviewed1.done.json").write_text(json.dumps({"redirects": []}))
    first = _redirects_for_frame(tmp_path, "shot_x_0000", "a solid lump")
    assert first == ("a solid lump",)

    # Round 2 was drawn with that one and rejected in turn.
    (reject / "shot_x_0000.reviewed2.done.json").write_text(
        json.dumps({"redirects": ["a solid lump"]})
    )
    assert _redirects_for_frame(tmp_path, "shot_x_0000", "deeper orange") == (
        "a solid lump",
        "deeper orange",
    )


def test_a_correction_is_not_repeated_if_the_reviewer_says_it_again(tmp_path: Path) -> None:
    from content_factory.workflows.stages import _redirects_for_frame

    reject = tmp_path / "rejected"
    reject.mkdir()
    (reject / "shot_x_0000.reviewed1.done.json").write_text(
        json.dumps({"redirects": ["a solid lump"]})
    )
    assert _redirects_for_frame(tmp_path, "shot_x_0000", "a solid lump") == ("a solid lump",)


def test_a_surviving_frame_keeps_exactly_what_it_was_drawn_with(tmp_path: Path) -> None:
    """The stickiness that stops a silent revert.

    A redirect read straight from the verdict vanishes the moment the frame is redrawn — the new
    digest makes it `unreviewed` again — so the prompt would revert, the input hash with it, and
    the frame would regenerate *without* the correction it had just been given. A frame whose
    marker survived is not being corrected and keeps its own set.
    """
    from content_factory.workflows.stages import _applied_redirects

    marker = tmp_path / "0000.done.json"
    marker.write_text(json.dumps({"redirects": ["a solid lump with no opening"]}))
    # Even with something pending for a *different* round, the surviving marker wins.
    assert _applied_redirects(tmp_path, "shot_x_0000", marker, "deeper orange") == (
        "a solid lump with no opening",
    )


def test_a_cleared_frame_picks_up_the_pending_correction(tmp_path: Path) -> None:
    from content_factory.workflows.stages import _applied_redirects

    missing = tmp_path / "0000.done.json"
    assert not missing.exists()
    assert _applied_redirects(tmp_path, "shot_x_0000", missing, "a solid lump") == ("a solid lump",)
