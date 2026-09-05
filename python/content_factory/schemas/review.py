"""Frame review: no generated image reaches a cut until someone has looked at it.

Everything this pipeline got wrong in its first days was visible in the pictures and invisible to
the code: two characters standing back to back, four people where two were staged, arms reaching
away from the person they were reaching for, a skeleton's joint dots drawn as coloured baubles,
a monochrome instruction half-applied so the image was neither colour nor grey. Deterministic
checks catch some of that — tonal collapse and stray saturation are measurable — but "these two
people are the same two people as the last frame" and "this pose makes bodily sense" are not.

So a batch of frames produces a **contact sheet** and blocks on a verdict. The verdict binds to
the digests of the exact images reviewed, so a regenerated frame is unreviewed again; and a
reviewer can reject single frames rather than the whole batch, because one bad drawing out of
thirty should cost one drawing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import OpaqueId, SchemaModel, Sha256Hex, VersionedModel

FrameVerdict = Literal["accept", "reject", "unreviewed"]
ReviewerKind = Literal["operator", "agent", "vlm"]


class FrameFinding(SchemaModel):
    """One measured property of one frame. Advisory findings inform the reviewer; blockers stand
    on their own and fail the frame without anyone having to look."""

    check: str = Field(min_length=1, max_length=60)
    passed: bool
    severity: Literal["blocker", "advisory"]
    detail: str = Field(min_length=1, max_length=400)
    measured: float | None = None
    threshold: float | None = None


class FrameRecord(SchemaModel):
    frame_id: str = Field(min_length=1, max_length=80)
    """Usually the shot id; whatever names the image in its own manifest."""
    png_sha256: Sha256Hex
    findings: tuple[FrameFinding, ...] = ()
    verdict: FrameVerdict = "unreviewed"
    reason: str = Field(default="", max_length=400)

    @property
    def blocked(self) -> bool:
        return any(f.severity == "blocker" and not f.passed for f in self.findings)


class FrameReviewBatch(VersionedModel):
    """One review pass over the frames of one deliverable."""

    deliverable_id: OpaqueId
    contact_sheet_sha256: Sha256Hex
    contact_sheet_path: str = Field(min_length=1, max_length=400)
    """Where the reviewer looks. Relative to the deliverable directory."""
    frames: tuple[FrameRecord, ...] = Field(min_length=1)
    created_at: datetime
    reviewer: ReviewerKind | None = None
    reviewed_at: datetime | None = None
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _reviewer_and_time_together(self) -> FrameReviewBatch:
        if (self.reviewer is None) != (self.reviewed_at is None):
            msg = "reviewer and reviewed_at must be set together"
            raise ValueError(msg)
        return self

    @property
    def rejected(self) -> tuple[FrameRecord, ...]:
        return tuple(f for f in self.frames if f.verdict == "reject" or f.blocked)

    @property
    def unreviewed(self) -> tuple[FrameRecord, ...]:
        return tuple(f for f in self.frames if f.verdict == "unreviewed" and not f.blocked)

    @property
    def passed(self) -> bool:
        """Every frame accepted by a reviewer, and none blocked by a measurement."""
        return self.reviewer is not None and not self.rejected and not self.unreviewed
