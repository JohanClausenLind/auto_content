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

from content_factory.api.deps import Principal, get_db, require_role
from content_factory.db.models import Role
from content_factory.qc.verdict import VerdictRefusedError
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


def _payload(run_id: str, run_dir: Path, record: history.RunRecord | None) -> dict[str, Any]:
    reviews = frame_reviews.run_reviews(run_dir)
    workflow = record.workflow if record is not None else None
    project_dir = record.project_dir if record is not None else str(run_dir)
    return {
        "run_id": run_id,
        "workflow": workflow,
        "project_dir": project_dir,
        # A verdict unblocks the gate; it does not restart the stages after it. Saying so, with
        # the command that does, is the difference between a reviewer waiting for a film and a
        # reviewer making one.
        "resume_command": frame_reviews.resume_command(workflow, project_dir),
        "reviews": [review.as_dict() for review in reviews],
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
    candidates = frame_reviews.deliverable_dirs(run_dir)
    if not candidates:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this run has no frame-review batch")
    if body.deliverable is not None:
        chosen = [d for d in candidates if d.name == body.deliverable]
        if not chosen:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such deliverable in this run")
        deliverable_dir = chosen[0]
    elif len(candidates) > 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"this run has {len(candidates)} deliverables with a gate; name one",
        )
    else:
        deliverable_dir = candidates[0]

    try:
        decided = frame_reviews.record_verdict(
            run_dir,
            deliverable_dir,
            reviewer=body.reviewer,
            accept=body.accept,
            reject=body.reject,
            accept_rest=body.accept_rest,
            reason=body.reason,
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
