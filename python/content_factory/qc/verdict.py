"""Turning "these are fine, that one is wrong" into a verdict the gate will read back."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import ValidationError

from content_factory.qc.reviewer import missing_decisions
from content_factory.schemas.review import FrameRecord, FrameReviewBatch, ReviewerKind

RefusalKind = Literal[
    "unknown_frames",
    "conflicting",
    "agent_blanket",
    "undecided",
    "nothing_decided",
    "contract",
]
"""Why a verdict was refused. The caller turns this into an exit code or a status code; the
sentence in ``reason`` is the same on both surfaces, because it is the same rule."""


class VerdictRefusedError(Exception):
    """A verdict that must not be written, and what the reviewer has to do about it."""

    def __init__(
        self,
        kind: RefusalKind,
        reason: str,
        *,
        frames: Sequence[str] = (),
        details: Sequence[str] = (),
    ) -> None:
        super().__init__(reason)
        self.kind: RefusalKind = kind
        self.reason = reason
        self.frames = tuple(frames)
        self.details = tuple(details)


def decide(
    batch: FrameReviewBatch,
    *,
    reviewer: ReviewerKind,
    accept: Iterable[str] = (),
    reject: Iterable[str] = (),
    accept_rest: bool = False,
    reason: str = "",
    redirect: str = "",
    note: str = "",
    now: dt.datetime | None = None,
) -> FrameReviewBatch:
    """The batch as decided, or :class:`VerdictRefusedError`."""
    accepted = {f.strip() for f in accept if f.strip()}
    rejected = {f.strip() for f in reject if f.strip()}
    known = {f.frame_id for f in batch.frames}

    unknown = (accepted | rejected) - known
    if unknown:
        raise VerdictRefusedError(
            "unknown_frames",
            f"unknown frame id(s): {sorted(unknown)}",
            frames=sorted(unknown),
        )
    both = accepted & rejected
    if both:
        raise VerdictRefusedError(
            "conflicting",
            f"frame(s) both accepted and rejected: {sorted(both)}",
            frames=sorted(both),
        )

    if reviewer == "agent":
        if accept_rest:
            raise VerdictRefusedError(
                "agent_blanket",
                "accepting the rest is a yes to a batch nobody opened, which an agent cannot"
                " give. Name every frame as accepted or rejected.",
            )
        undecided = missing_decisions(batch.frames, accepted, rejected)
        if undecided:
            raise VerdictRefusedError(
                "undecided",
                f"{len(undecided)} frame(s) not decided: {', '.join(undecided)}",
                frames=undecided,
            )
    elif not accept_rest and not accepted and not rejected:
        raise VerdictRefusedError(
            "nothing_decided",
            f"nothing was decided about any of the {len(batch.frames)} frames:"
            " accept the batch, or name the frames that are wrong.",
        )

    # `rejected` decides first on both paths. An unmentioned frame keeps the decision it carries
    # (a prior accept of a byte-identical picture), so one rejected drawing costs one redraw.
    def _decided(frame: FrameRecord) -> tuple[str, str, str]:
        if frame.frame_id in rejected:
            return "reject", reason, redirect
        if accept_rest or frame.frame_id in accepted:
            return "accept", "", ""
        return frame.verdict, frame.reason, frame.redirect

    frames_out = []
    for f in batch.frames:
        verdict, why, instead = _decided(f)
        frames_out.append(
            {**f.model_dump(mode="json"), "verdict": verdict, "reason": why, "redirect": instead}
        )

    updates: dict[str, object] = {
        "reviewer": reviewer,
        "reviewed_at": (now or dt.datetime.now(dt.UTC)).isoformat(),
        "notes": note or batch.notes,
        "frames": frames_out,
    }
    try:
        # Validated, not copied: `model_copy(update=...)` does not re-validate, and that is how a
        # verdict the contract could not read back reached disk twice.
        return FrameReviewBatch.model_validate({**batch.model_dump(mode="json"), **updates})
    except ValidationError as exc:
        details = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        ]
        raise VerdictRefusedError(
            "contract",
            "this verdict does not satisfy the review contract; nothing was written.",
            details=details,
        ) from exc


def merge_verdict(batch: FrameReviewBatch, prior: FrameReviewBatch) -> FrameReviewBatch:
    """``batch`` carrying the decisions ``prior`` made about the *same* images."""
    by_id = {f.frame_id: f for f in prior.frames}
    return batch.model_copy(
        update={
            "reviewer": prior.reviewer,
            "reviewed_at": prior.reviewed_at,
            "notes": prior.notes,
            "frames": tuple(
                record.model_copy(
                    update={
                        "verdict": by_id[record.frame_id].verdict,
                        "reason": by_id[record.frame_id].reason,
                        "redirect": by_id[record.frame_id].redirect,
                    }
                )
                if record.frame_id in by_id
                and by_id[record.frame_id].png_sha256 == record.png_sha256
                else record
                for record in batch.frames
            ),
        }
    )
