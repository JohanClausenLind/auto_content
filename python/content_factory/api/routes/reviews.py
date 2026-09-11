"""/v1/run-history/{run_id}/review: answering the frame-review gate from the page that made it.

``review_frames`` stops a run and asks a person to look at the drawings. Until now the only way to
answer was the CLI, with the run's path on disk — so the pictures that most needed somebody were
the ones hardest to reach, and 27 runs sat overnight finished and unlooked-at.

The rules are not re-stated here. Who may accept a batch unopened, what a verdict has to name and
what the contract will read back all live in :mod:`content_factory.qc.verdict`, shared with
``content-factory frames review``: this route decodes a run id, hands the decision over, and turns
a refusal into a 422 that says which frames are outstanding.

Reading needs ``viewer``; recording a verdict needs ``reviewer`` — the role that exists for exactly
this and nothing else. Every verdict is audited with what it decided.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from content_factory.api.deps import Principal, get_db, require_role
from content_factory.db.models import Role
from content_factory.qc.verdict import VerdictRefusedError
from content_factory.qc.vlm_review import VlmReviewUnavailableError, current_review, review_batch
from content_factory.services import audit, frame_reviews
from content_factory.services import run_history as history

router = APIRouter(prefix="/v1/run-history", tags=["reviews"])
VIEWER = require_role(Role.viewer)
REVIEWER = require_role(Role.reviewer)


def _run(run_id: str) -> tuple[Path, history.RunRecord | None]:
    """The run directory an id names, with its report. 404 for anything outside the output root."""
    run_dir = history.decode_run_id(run_id, history.history_root())
    if run_dir is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return run_dir, history.get_run(run_id)


def _deliverable(run_dir: Path, named: str | None) -> Path:
    """The deliverable whose gate is being acted on, or a 404/422 saying why not.

    Shared by every route below rather than repeated: "this run has three gates, name one" is a
    refusal the operator must get the same way whether they are recording a verdict or asking for
    a second opinion about the same pictures.
    """
    candidates = frame_reviews.deliverable_dirs(run_dir)
    if not candidates:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this run has no frame-review batch")
    if named is not None:
        chosen = [d for d in candidates if d.name == named]
        if not chosen:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such deliverable in this run")
        return chosen[0]
    if len(candidates) > 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"this run has {len(candidates)} deliverables with a gate; name one",
        )
    return candidates[0]


def _ai_review_body(run_dir: Path, deliverable_dir: Path) -> dict[str, Any] | None:
    """The stored vision-model opinion for one gate, labelled with whether it is still current.

    ``current`` is the load-bearing field. An opinion formed about a frame that has since been
    redrawn is worth showing — it says what was wrong last time — and worth never showing as if
    it were about the picture on screen, which is the same rule
    :func:`content_factory.qc.verdict.merge_verdict` applies to a human verdict.
    """
    review, current = current_review(run_dir, deliverable_dir)
    if review is None:
        return None
    return {
        **review.model_dump(mode="json"),
        "current": current,
        "flagged": list(review.flagged),
    }


def _payload(run_id: str, run_dir: Path, record: history.RunRecord | None) -> dict[str, Any]:
    reviews = frame_reviews.run_reviews(run_dir)
    workflow = record.workflow if record is not None else None
    project_dir = record.project_dir if record is not None else str(run_dir)
    by_name = {d.name: d for d in frame_reviews.deliverable_dirs(run_dir)}
    return {
        "run_id": run_id,
        "workflow": workflow,
        "project_dir": project_dir,
        # A verdict unblocks the gate; it does not restart the stages after it. Saying so, with
        # the command that does, is the difference between a reviewer waiting for a film and a
        # reviewer making one.
        "resume_command": frame_reviews.resume_command(workflow, project_dir),
        "reviews": [review.as_dict() for review in reviews],
        # Served with the gate, not behind a second request: the panel has to know whether a
        # second opinion already exists before it can decide whether to offer to ask for one.
        "ai_reviews": {
            name: body
            for name, deliverable in by_name.items()
            if (body := _ai_review_body(run_dir, deliverable)) is not None
        },
    }


@router.get("/{run_id}/review")
async def get_review(run_id: str, p: Principal = Depends(VIEWER)) -> dict[str, Any]:
    """The gate's question: every frame, its measurements, and the file to look at."""
    run_dir, record = _run(run_id)
    return _payload(run_id, run_dir, record)


class VerdictBody(BaseModel):
    """What a reviewer decided. Unknown fields are an error, like every other contract here."""

    model_config = ConfigDict(extra="forbid")

    deliverable: str | None = None
    """Which deliverable's gate. Optional while a run has exactly one, which is the usual case."""
    accept: list[str] = Field(default_factory=list)
    reject: list[str] = Field(default_factory=list)
    accept_rest: bool = False
    """Accept every frame not named as rejected — a person's yes to a contact sheet. Refused for
    an agent, which reads the images one at a time and must name each one."""
    reason: str = Field(default="", max_length=400)
    """What is wrong with the rejected frames. For the record and the operator."""
    redirect: str = Field(default="", max_length=400)
    """What they should show instead, stated positively — this is the part that reaches the model
    when the frame is redrawn. See ``FrameRecord.redirect``."""
    note: str = Field(default="", max_length=2000)
    reviewer: Literal["operator", "agent", "vlm"] = "operator"


@router.post("/{run_id}/review")
async def record_review(
    run_id: str,
    body: VerdictBody,
    request: Request,
    p: Principal = Depends(REVIEWER),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Record a verdict. Nothing is written unless the whole batch satisfies its contract."""
    run_dir, record = _run(run_id)
    deliverable_dir = _deliverable(run_dir, body.deliverable)

    try:
        decided = frame_reviews.record_verdict(
            run_dir,
            deliverable_dir,
            reviewer=body.reviewer,
            accept=body.accept,
            reject=body.reject,
            accept_rest=body.accept_rest,
            reason=body.reason,
            redirect=body.redirect,
            note=body.note,
        )
    except VerdictRefusedError as exc:
        # The frames are part of the refusal, not just the sentence: a reviewer who left three of
        # thirty undecided should be shown which three, not asked to diff two lists by eye.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            {
                "problem": exc.reason,
                "kind": exc.kind,
                "frames": list(exc.frames),
                "details": list(exc.details),
            },
        ) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    await audit.record(
        db,
        "review.frames",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="run_directory",
        target_id=run_id,
        detail={
            "deliverable": deliverable_dir.name,
            "reviewer": body.reviewer,
            "accepted": decided.accepted,
            "rejected": decided.rejected,
            "unreviewed": decided.unreviewed,
            "passed": decided.passed,
        },
        ip=request.client.host if request.client else None,
    )
    return _payload(run_id, run_dir, record)


class AiReviewBody(BaseModel):
    """Which gate to ask about. Nothing else: the review has no knobs on purpose.

    A reviewer choosing a temperature or a model is a reviewer producing an opinion that cannot be
    compared with yesterday's, and the point of storing these is that they can be.
    """

    model_config = ConfigDict(extra="forbid")

    deliverable: str | None = None


@router.post("/{run_id}/ai-review")
async def request_ai_review(
    run_id: str,
    body: AiReviewBody,
    request: Request,
    p: Principal = Depends(REVIEWER),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Ask the local vision model to look at this gate's pictures, and store what it says.

    Needs ``reviewer`` rather than ``viewer``, and not because it changes a verdict — it cannot.
    It spends about forty seconds of the GPU and writes a file into the run, and both of those are
    the reviewer's to spend.

    The two failure modes answer differently and must not be collapsed. A review that *cannot be
    asked for* — no batch, no frame files, a run holding the card — is a 409 with a sentence the
    operator can act on. A review that was asked for and *failed* — no model server, weights not
    pulled, an answer that would not validate — is a 502: the pictures and the gate are fine and
    the thing to fix is the model stack.
    """
    run_dir, record = _run(run_id)
    deliverable_dir = _deliverable(run_dir, body.deliverable)
    try:
        review = await run_in_threadpool(review_batch, run_dir, deliverable_dir)
    except VlmReviewUnavailableError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"the vision reviewer did not answer: {exc}"
        ) from exc
    await audit.record(
        db,
        "review.frames.ai",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="run_directory",
        target_id=run_id,
        detail={
            "deliverable": deliverable_dir.name,
            "model": review.model_alias,
            "frames": len(review.frames),
            # What it thought, in the audit trail, because an opinion that moved a verdict should
            # be findable afterwards without opening the run directory.
            "same_world": review.set.same_world,
            "flagged": list(review.flagged),
            "elapsed_s": review.elapsed_s,
        },
        ip=request.client.host if request.client else None,
    )
    return _payload(run_id, run_dir, record)
