"""The boundary around a vision model's opinion of a set of frames.

The model itself is not under test — a real call is 42 s of GPU and a different sentence each
time, which belongs in a live test. What is under test is everything that keeps a fluent answer
from becoming a decision or a claim about pictures it never saw:

* the whole set goes in **one call**, because "do these belong together" cannot be answered one
  picture at a time;
* every frame arrives **downscaled**, because six frames at their native size were refused by the
  context window at 22,494 tokens;
* an opinion about a frame that was not attached is **dropped**, and an answer about none of them
  is an error rather than an empty review;
* the review **binds to the digests** it saw, so a redrawn frame makes it stale;
* it **writes no verdict**, ever.
"""

from __future__ import annotations

import base64
import datetime as dt
import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from content_factory.budgets.ledger import CostLedger
from content_factory.hardware.probe import mock_inventory
from content_factory.models.catalog import default_catalog, default_endpoints
from content_factory.models.gateway import ModelGateway
from content_factory.qc import vlm_review
from content_factory.qc.vlm_review import VlmReviewUnavailableError
from content_factory.schemas.base import sha256_hex
from content_factory.schemas.review import FrameRecord, FrameReviewBatch
from content_factory.services import frame_reviews

DELIVERABLE = "dlv_short0000001"


def _png(shade: int, size: tuple[int, int] = (2560, 1440)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (shade, shade, shade)).save(buf, "PNG")
    return buf.getvalue()


def gated_run(root: Path, *, frames: int = 3) -> tuple[Path, Path]:
    """A run parked at `review_frames`, with a story plan, as the real ones are."""
    run_dir = root / "iceberg"
    deliverable = run_dir / "deliverables" / DELIVERABLE
    frames_dir = deliverable / "sequence" / "frames"
    frames_dir.mkdir(parents=True)
    records = []
    for index in range(frames):
        png = _png(40 + index * 40)
        (frames_dir / f"{index:04d}.png").write_bytes(png)
        records.append(FrameRecord(frame_id=f"frame:{index:04d}", png_sha256=sha256_hex(png)))
    sheet = deliverable / "reviews" / "frames" / "contact-sheet.png"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(_png(200, (640, 360)))
    batch = FrameReviewBatch(
        deliverable_id=DELIVERABLE,
        contact_sheet_sha256=sha256_hex(sheet.read_bytes()),
        contact_sheet_path="reviews/frames/contact-sheet.png",
        frames=tuple(records),
        created_at=dt.datetime.now(dt.UTC),
    )
    (sheet.parent / "batch.json").write_text(batch.model_dump_json(indent=1))
    (run_dir / "story").mkdir()
    (run_dir / "story" / "plan.json").write_text(
        json.dumps(
            {
                "visual_subject": "one small iceberg of blue-white ice on dark green water",
                "hook_text": "iceberg",
                "beats": [{"display_text": f"View {i + 1}."} for i in range(frames)],
            }
        )
    )
    return run_dir, deliverable


def gateway_for(judgement: dict[str, Any]) -> tuple[ModelGateway, list[dict[str, Any]]]:
    """The real gateway with a canned completion, and the calls it made.

    A real :class:`ModelGateway` rather than a stand-in object, through the ``completion_fn`` seam
    the gateway carries for exactly this. It costs nothing and buys the whole path: routing
    actually picks the vision alias (so `required_models` is under test rather than asserted),
    the schema is enforced the way Ollama enforces it, and the budget is reserved and settled.
    """
    calls: list[dict[str, Any]] = []

    def fake_completion(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {
            "choices": [{"message": {"content": json.dumps(judgement)}}],
            "usage": {"prompt_tokens": 2910, "completion_tokens": 1168},
        }

    return (
        ModelGateway(
            catalog=default_catalog(),
            endpoints=default_endpoints(),
            ledger=CostLedger(),
            # The card this runs on. The vision tier declares 19 GiB and `hardware_fit` compares
            # against total VRAM, so a smaller profile would refuse it before the prompt is built.
            inventory=mock_inventory("rtx3090"),
            completion_fn=fake_completion,
        ),
        calls,
    )


def answer(**overrides: Any) -> dict[str, Any]:
    base = {
        "frames": [
            {
                "frame_id": "frame:0000",
                "shows": "a blue-white iceberg on dark water",
                "matches_intent": True,
                "issues": [],
                "severity": "fine",
            },
            {
                "frame_id": "frame:0001",
                "shows": "a much smaller iceberg from a higher angle",
                "matches_intent": False,
                "issues": ["a different camera angle and scale from the others"],
                "severity": "wrong",
            },
        ],
        "set": {
            "same_world": False,
            "what_changes": ["the camera rises and the iceberg shrinks"],
            "drifting_frames": ["frame:0001"],
            "summary": "frame:0001 is from a different setup",
        },
    }
    return {**base, **overrides}


def test_the_whole_set_goes_in_one_call_with_the_story_and_each_frames_intent(
    tmp_path: Path,
) -> None:
    run_dir, deliverable = gated_run(tmp_path)
    gateway, calls = gateway_for(answer())

    vlm_review.review_batch(run_dir, deliverable, gateway=gateway, check_gpu=False)

    assert len(calls) == 1  # one call: "do these belong together" needs all of them
    # The system message the gateway prepends comes first; the pictures ride the user turn.
    content = next(m for m in calls[0]["messages"] if m["role"] == "user")["content"]
    images = [part for part in content if part["type"] == "image_url"]
    assert len(images) == 3
    prompt = content[0]["text"]
    # The story the frames are for, read off the run's own plan rather than rebuilt.
    assert "one small iceberg of blue-white ice" in prompt
    # Every frame named, so an answer can be matched to a picture.
    for index in range(3):
        assert f"frame:{index:04d}" in prompt
    # And the instruction that keeps it an opinion.
    assert "not accepting or rejecting" in prompt


def test_every_frame_is_downscaled_before_it_is_sent(tmp_path: Path) -> None:
    """Six frames at their native 2560x1440 came to 22,494 tokens and were refused outright."""
    run_dir, deliverable = gated_run(tmp_path, frames=1)
    gateway, calls = gateway_for(
        answer(frames=[answer()["frames"][0]], set={**answer()["set"], "drifting_frames": []})
    )

    vlm_review.review_batch(run_dir, deliverable, gateway=gateway, check_gpu=False)

    content = next(m for m in calls[0]["messages"] if m["role"] == "user")["content"]
    part = next(p for p in content if p["type"] == "image_url")
    raw = base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as sent:
        assert max(sent.size) == vlm_review.REVIEW_LONG_EDGE
        assert sent.format == "JPEG"


def test_the_vision_model_is_required_rather_than_chosen(tmp_path: Path) -> None:
    """A text-only model handed an image list answers confidently about pictures it never got."""
    run_dir, deliverable = gated_run(tmp_path)
    gateway, calls = gateway_for(answer())
    review = vlm_review.review_batch(run_dir, deliverable, gateway=gateway, check_gpu=False)

    # Routing actually resolved to the vision tier — asserted through the weights the call named,
    # not through the manifest's own declaration. This is the only catalogue entry with a clip
    # projector, and a text-only model here would answer confidently about nothing.
    assert "Qwen3.6-27B-Heretic" in calls[0]["model"]
    assert review.model_alias == vlm_review.VISION_ALIAS
    # No egress: these are unpublished frames, several of them the ones that came out wrong.
    assert vlm_review._SKILL.permissions.network_egress is False


def test_an_opinion_about_a_frame_that_was_not_shown_is_dropped(tmp_path: Path) -> None:
    run_dir, deliverable = gated_run(tmp_path)
    gateway, _calls = gateway_for(
        answer(
            frames=[
                *answer()["frames"],
                {
                    "frame_id": "frame:0099",
                    "shows": "a frame that is not in this set",
                    "matches_intent": True,
                    "issues": [],
                    "severity": "fine",
                },
            ],
            set={**answer()["set"], "drifting_frames": ["frame:0001", "frame:0099"]},
        )
    )

    review = vlm_review.review_batch(run_dir, deliverable, gateway=gateway, check_gpu=False)

    assert [f.frame_id for f in review.frames] == ["frame:0000", "frame:0001"]
    # And a drifting id nothing can find is a mark that would go nowhere on the reviewer's screen.
    assert review.set.drifting_frames == ("frame:0001",)


def test_an_answer_about_none_of_the_frames_is_an_error_not_an_empty_review(
    tmp_path: Path,
) -> None:
    run_dir, deliverable = gated_run(tmp_path)
    gateway, _calls = gateway_for(
        answer(
            frames=[
                {
                    "frame_id": "frame:0099",
                    "shows": "something else entirely",
                    "matches_intent": True,
                    "issues": [],
                    "severity": "fine",
                }
            ],
            set={**answer()["set"], "drifting_frames": []},
        )
    )

    with pytest.raises(ValueError, match="not in this set"):
        vlm_review.review_batch(run_dir, deliverable, gateway=gateway, check_gpu=False)
    # Nothing stored: a model that did not do the task must not leave a review behind.
    assert vlm_review.load_review(deliverable) is None


def test_the_review_records_no_verdict_and_leaves_the_gate_alone(tmp_path: Path) -> None:
    run_dir, deliverable = gated_run(tmp_path)
    gateway, _calls = gateway_for(answer())

    review = vlm_review.review_batch(run_dir, deliverable, gateway=gateway, check_gpu=False)

    # It called one frame unusable. Nothing was decided about any of them.
    assert review.flagged == ("frame:0001",)
    assert not frame_reviews.verdict_path(deliverable).exists()
    batch = frame_reviews.current_batch(deliverable)
    assert batch is not None
    assert batch.reviewer is None
    assert len(batch.unreviewed) == 3


def test_a_redrawn_frame_makes_the_opinion_stale(tmp_path: Path) -> None:
    run_dir, deliverable = gated_run(tmp_path)
    vlm_review.review_batch(run_dir, deliverable, gateway=gateway_for(answer())[0], check_gpu=False)

    stored, current = vlm_review.current_review(run_dir, deliverable)
    assert stored is not None and current is True

    # The picture is redrawn. The opinion is about a frame that is gone.
    (deliverable / "sequence" / "frames" / "0000.png").write_bytes(_png(255))
    batch_path = frame_reviews.batch_path(deliverable)
    batch = json.loads(batch_path.read_text())
    batch["frames"][0]["png_sha256"] = sha256_hex(
        (deliverable / "sequence" / "frames" / "0000.png").read_bytes()
    )
    batch_path.write_text(json.dumps(batch))

    stale, current = vlm_review.current_review(run_dir, deliverable)
    # Kept and labelled, not deleted: it says what was wrong last time.
    assert stale is not None and current is False
    assert stale.set.summary


def test_a_run_with_no_batch_and_a_batch_with_no_files_both_refuse_by_name(
    tmp_path: Path,
) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(VlmReviewUnavailableError, match="no frame-review batch"):
        vlm_review.review_batch(tmp_path / "empty", tmp_path / "empty", check_gpu=False)

    run_dir, deliverable = gated_run(tmp_path)
    for png in (deliverable / "sequence" / "frames").glob("*.png"):
        png.unlink()
    with pytest.raises(VlmReviewUnavailableError, match="nothing to look at"):
        vlm_review.review_batch(run_dir, deliverable, check_gpu=False)


def test_the_review_says_which_weights_answered_and_what_they_were_told(tmp_path: Path) -> None:
    """A judgement is worth what the judge was told, and what answered it."""
    run_dir, deliverable = gated_run(tmp_path)
    review = vlm_review.review_batch(
        run_dir, deliverable, gateway=gateway_for(answer())[0], check_gpu=False
    )

    assert review.model_alias == vlm_review.VISION_ALIAS
    # The weight as the provider spells it, not the alias: "the local vision model" is three
    # different models over a year.
    assert "Qwen3.6-27B" in review.model_id
    # The brief it saw, so "does not match intent" can be told from "the brief never arrived".
    assert "one small iceberg" in review.intent
    # Bound to every frame it was *shown*, not only the ones it described: the third had no
    # opinion, and redrawing it still makes this review a judgement about a set that no longer
    # exists. The conservative direction is the only safe one here.
    assert set(review.digests) == {"frame:0000", "frame:0001", "frame:0002"}


def test_the_card_is_not_taken_from_a_running_lane(tmp_path: Path, monkeypatch) -> None:
    """17.8 GB does not share a 24 GB card with HiDream. Refuse, naming the run; never evict."""
    run_dir, deliverable = gated_run(tmp_path)

    class Running:
        workflow = "image-set"

    monkeypatch.setattr(
        "content_factory.runners.registry.active_runs", lambda: [Running()], raising=False
    )
    with pytest.raises(VlmReviewUnavailableError, match="image-set"):
        vlm_review.review_batch(run_dir, deliverable, gateway=gateway_for(answer())[0])
