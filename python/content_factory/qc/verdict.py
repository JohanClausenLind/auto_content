"""Turning "these are fine, that one is wrong" into a verdict the gate will read back.

Two surfaces now record a verdict on a batch of drawings — ``content-factory frames review`` and
the workspace's review panel — and the rules they have to enforce are identical and load-bearing.
A second copy of "may an agent accept a batch it never opened" is how the two answers drift, and
the one that drifts is the one that lets six unopened pictures into a film.

Every refusal below is something that actually happened on this machine:

* **``--as overnight-review``** — a reviewer kind the contract does not have. The command printed
  ``{"passed": true}``, wrote the file, and the next ``review_frames`` refused the very verdict it
  had just been handed.
* **a 401-character rejection reason** — ``model_copy(update=...)`` does not re-validate in
  Pydantic v2, so it went to disk and the gate died three stages later reading it.
* **an agent accepting a batch in one word** — see :mod:`content_factory.qc.reviewer`; an agent
  reads the images one at a time, so a verdict that does not name a frame is a frame it did not
  open.

So the decided batch goes through its own contract *before* anything is written, and a violated
constraint becomes a sentence for the reviewer instead of a crash in a later stage.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from typing import Literal

from pydantic import ValidationError

from content_factory.qc.reviewer import missing_decisions
from content_factory.schemas.review import FrameReviewBatch, ReviewerKind

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
    """A verdict that must not be written, and what the reviewer has to do about it.

    Carries the frames it is about, so a caller can point at them rather than making the reviewer
    diff two lists by eye.
    """

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
    note: str = "",
    now: dt.datetime | None = None,
) -> FrameReviewBatch:
    """The batch as decided, or :class:`VerdictRefusedError`. Writes nothing.

    ``accept_rest`` is the blanket yes: a person looking at one contact sheet of every frame may
    reasonably say "all fine", and an agent reading them one at a time may not.
    """
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

    # `accept_rest` accepts what was not rejected; otherwise a frame is accepted only when it was
    # named. Both paths leave `rejected` deciding, so a frame in both lists cannot slip through as
    # accepted — that combination is refused above.
    def _verdict(frame_id: str) -> str:
        if frame_id in rejected:
            return "reject"
        if accept_rest or frame_id in accepted:
            return "accept"
        return "unreviewed"

    updates: dict[str, object] = {
        "reviewer": reviewer,
        "reviewed_at": (now or dt.datetime.now(dt.UTC)).isoformat(),
        "notes": note or batch.notes,
        "frames": [
            {
                **f.model_dump(mode="json"),
                "verdict": _verdict(f.frame_id),
                "reason": reason if f.frame_id in rejected else "",
            }
            for f in batch.frames
        ],
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
    """``batch`` carrying the decisions ``prior`` made about the *same* images.

    A verdict binds to the digests of the exact pictures reviewed, so a regenerated frame comes
    back unreviewed and has to be looked at again. That is the whole point of the gate, and it is
    why this is a digest comparison rather than a frame-id one.
    """
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
                    }
                )
                if record.frame_id in by_id
                and by_id[record.frame_id].png_sha256 == record.png_sha256
                else record
                for record in batch.frames
            ),
        }
    )
