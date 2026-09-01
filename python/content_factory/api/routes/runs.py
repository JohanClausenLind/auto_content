"""/v1/runs and /v1/action-items: start, inspect, approve production runs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.api.deps import Principal, get_db, require_role, require_step_up
from content_factory.db.models import Role
from content_factory.services import audit
from content_factory.services import runs as svc

router = APIRouter(prefix="/v1", tags=["runs"])
EDITOR = require_role(Role.editor)
VIEWER = require_role(Role.viewer)


class StartRunBody(BaseModel):
    quality: str = Field(default="demo", pattern=r"^(smoke|demo)$")
    campaign: str = Field(
        default="fixture", pattern=r"^fixture$"
    )  # real campaign compiler in phase 8 create flow


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
async def start_run(
    body: StartRunBody, p: Principal = Depends(EDITOR), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    from content_factory.schemas.fixtures import sample_campaign

    campaign = sample_campaign().model_copy(update={"workspace_id": p.workspace_id})
    campaign = campaign.model_copy(
        update={"brief": campaign.brief.model_copy(update={"workspace_id": p.workspace_id})}
    )
    try:
        run_id = await svc.start_run(campaign, quality=body.quality)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"workflow engine unavailable: {exc}"
        ) from exc
    await audit.record(
        db,
        "run.start",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="run",
        target_id=run_id,
    )
    return {"run_id": run_id}


@router.get("/runs")
async def list_runs(
    p: Principal = Depends(VIEWER), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    return await svc.list_runs(db, p.workspace_id)


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str, p: Principal = Depends(VIEWER), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    view = await svc.run_view(db, p.workspace_id, run_id)
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return view


class ApproveBody(BaseModel):
    revision_hash: str = Field(min_length=8, max_length=64)
    decision: str = Field(default="approve", pattern=r"^(approve|reject)$")
    reason: str = Field(default="", max_length=1000)


@router.post("/runs/{run_id}/approval")
async def approve(
    run_id: str,
    body: ApproveBody,
    p: Principal = Depends(require_step_up),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    view = await svc.run_view(db, p.workspace_id, run_id)
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    if p.role not in {Role.owner, Role.editor} and not p.account.is_owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "requires editor or owner")
    try:
        await svc.approve_run(
            run_id,
            actor=p.account.username,
            revision_hash=body.revision_hash,
            decision=body.decision,
            reason=body.reason,
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"workflow engine unavailable: {exc}"
        ) from exc
    await audit.record(
        db,
        f"run.{body.decision}",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="run",
        target_id=run_id,
        detail={"revision_hash": body.revision_hash},
    )
    return {"run_id": run_id, "decision": body.decision}


@router.get("/action-items")
async def action_items(
    status_filter: str = "open", p: Principal = Depends(VIEWER), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    if status_filter not in {"open", "resolved"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "status must be open or resolved")
    return await svc.list_action_items(db, p.workspace_id, status=status_filter)
