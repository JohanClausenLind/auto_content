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
    """What is wrong with the picture. For the record, and for the operator reading it later."""
    redirect: str = Field(default="", max_length=400)
    """What the picture should show instead, stated **positively** — and it reaches the model.

    ``reason`` is a complaint and cannot be sent to an image model as one. The weights this repo
    drives are distilled to guidance 0, where a negation is a hint the model may or may not take:
    ``sequences.styles`` records the measurement ("no lettering" produced exactly as much gibberish
    signage as omitting it) and the conclusion — "what *does* work is a positive instruction about
    the scene".

    So a rejection carries two things and they do different jobs. "An open circular rim: a hollow
    vessel, not a solid lump" is the reason. "a solid opaque lump of resin with no opening" is the
    redirect, and that is what `_anchor_prompt` appends when the frame is redrawn. Empty is
    allowed and means the redraw only moves the seed, which is what happened before this existed.
    """

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


# --- what a vision model says about a set ------------------------------------------------------
#
# The deterministic checks and a person are the two reviewers this gate was built for, and the
# gap between them is real: measurements cannot see whether the subject is the same subject, and
# a person is not always sitting there — 27 runs sat overnight with their drawings finished.
# `ReviewerKind` has had "vlm" in it since the contract was written, and this is what it says.
#
# It is an **opinion, not a verdict**. Nothing below can accept or reject a frame: the review is
# stored beside the batch, shown next to the pictures, and the operator decides. That is the same
# rule the whole gate rests on — `qc/frame_review.py` says it plainly about its own numbers — and
# it is more load-bearing here than anywhere else, because a model that mistakes what it is
# looking at will do so fluently.

FrameOpinionSeverity = Literal["fine", "minor", "wrong"]


class FrameOpinion(SchemaModel):
    """What the reviewer says about one picture."""

    frame_id: str = Field(min_length=1, max_length=80)
    shows: str = Field(min_length=1, max_length=400)
    """What the reviewer says is *in* the picture, in its own words.

    The first field and the load-bearing one. It is how a reader tells whether the model looked:
    when this machine drew jars of honey for "honey-coloured", a reviewer describing jars of
    honey is instantly, visibly wrong about the brief — and one describing the intended subject
    is a reviewer that cannot be trusted about anything else it said."""
    matches_intent: bool
    """Whether what it sees is what this frame was asked for."""
    issues: tuple[str, ...] = ()
    """What is wrong in the picture, each as its own sentence. Empty when nothing is."""
    severity: FrameOpinionSeverity = "fine"


class SetOpinion(SchemaModel):
    """What the reviewer says about the frames *together*, which is the point of asking.

    A set can drift a little at each step and end somewhere else entirely with every consecutive
    pair looking fine — which is why `qc.frame_review.consistency_matrix` compares every pair, and
    why the question put to a vision model is about the whole set rather than about neighbours.
    """

    same_world: bool
    """Whether these frames read as one subject in one place and one idiom."""
    what_changes: tuple[str, ...] = ()
    """What actually differs across the set, named — "the mug's handle swaps sides", "the light
    moves from behind to in front". A consistency complaint that cannot say what moved is not
    actionable, and the reasons here are what a redraw and the prompt-guidance proposals read."""
    drifting_frames: tuple[str, ...] = ()
    """Frame ids that left the others behind. Empty when the set has no odd one out — including
    when it is uniformly inconsistent, which is a different fault with a different fix."""
    summary: str = Field(min_length=1, max_length=1200)


class SetReview(VersionedModel):
    """One vision-model review of one frame-review batch.

    Bound to image digests, like every verdict here: a regenerated frame is not covered by an
    opinion formed about the picture it replaced, and the panel says so rather than showing a
    stale judgement next to a new drawing (see :func:`content_factory.qc.verdict.merge_verdict`,
    which enforces the same rule for a human verdict).
    """

    deliverable_id: OpaqueId
    reviewed_at: datetime
    model_alias: str = Field(min_length=1, max_length=80)
    model_id: str = Field(default="", max_length=200)
    """The weight that answered, spelled as the provider names it. Recorded because "the local
    vision model" is three different models over a year, and an opinion is worth what the model
    that gave it is worth."""
    intent: str = Field(default="", max_length=4000)
    """The story and per-frame context the reviewer was given, kept verbatim.

    A judgement is only as good as what the judge was told, and this is the only way a reader can
    tell whether a "does not match intent" means the picture is wrong or the brief never reached
    the reviewer."""
    frames: tuple[FrameOpinion, ...] = ()
    set: SetOpinion
    digests: dict[str, Sha256Hex] = Field(default_factory=dict)
    """frame_id -> the PNG digest this opinion was formed about."""
    elapsed_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def flagged(self) -> tuple[str, ...]:
        """Frames the reviewer would not pass, in frame order.

        Offered as *what to look at first* and never applied. The panel pre-marks them for the
        operator, who is the only reviewer that can reject anything."""
        return tuple(
            f.frame_id for f in self.frames if f.severity == "wrong" or not f.matches_intent
        )

    def covers(self, digests: dict[str, str]) -> bool:
        """Whether this review is about exactly the pictures now on disk."""
        return bool(self.digests) and self.digests == digests
