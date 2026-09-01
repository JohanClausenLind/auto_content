"""/v1/revisions: the Revision Box loop — feedback → typed outcome; confirm → apply + rebuild."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.api.deps import Principal, get_db, require_role
from content_factory.db.models import Role
from content_factory.editor.apply import ApplyError, apply_fix_plan, undo_last
from content_factory.editor.critique import map_feedback
from content_factory.editor.project_context import ProjectContextError, load_context
from content_factory.schemas.editing import FixPlan
from content_factory.services import audit
from content_factory.services.runs import projects_root, start_run

router = APIRouter(prefix="/v1/revisions", tags=["revisions"])
EDITOR = require_role(Role.editor)


class FeedbackBody(BaseModel):
    project_id: str = Field(pattern=r"^prj_[A-Za-z0-9_-]{4,40}$")
    unit_id: str | None = None
    feedback: str = Field(min_length=1, max_length=4000)


def _project_dir(project_id: str) -> Path:
    path = projects_root() / project_id
    if not (path / "manifest.json").exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return path


@router.post("")
async def submit_feedback(body: FeedbackBody, p: Principal = Depends(EDITOR)) -> dict[str, Any]:
    try:
        ctx = load_context(_project_dir(body.project_id))
    except ProjectContextError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    outcome = map_feedback(body.feedback, ctx)
    return {"outcome": outcome.model_dump(mode="json")}


@router.post("/apply", status_code=status.HTTP_202_ACCEPTED)
async def apply_and_rebuild(
    body: FeedbackBody, p: Principal = Depends(EDITOR), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    project_dir = _project_dir(body.project_id)
    try:
        ctx = load_context(project_dir)
    except ProjectContextError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    outcome = map_feedback(body.feedback, ctx)
    if not isinstance(outcome, FixPlan):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"feedback did not map to a fix plan: {outcome.model_dump(mode='json')}",
        )
    try:
        applied = apply_fix_plan(project_dir, outcome)
    except ApplyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    import json as _json

    manifest = _json.loads((project_dir / "manifest.json").read_text())
    from content_factory.schemas.fixtures import sample_campaign

    campaign = sample_campaign()  # phase 8 create-flow stores the real campaign per project
    campaign_path = project_dir / "campaign.json"
    if campaign_path.exists():
        from content_factory.schemas.content import ContentCampaign

        campaign = ContentCampaign.model_validate_json(campaign_path.read_text())
    try:
        run_id = await start_run(
            campaign, quality=manifest.get("quality", "demo"), project_id=body.project_id
        )
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"workflow engine unavailable: {exc}"
        ) from exc
    await audit.record(
        db,
        "revision.apply",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="project",
        target_id=body.project_id,
        detail={"revision": applied.revision, "run_id": run_id},
    )
    return {
        "run_id": run_id,
        "revision": applied.revision,
        "affected_unit_ids": list(applied.affected_unit_ids),
    }


class UndoBody(BaseModel):
    project_id: str = Field(pattern=r"^prj_[A-Za-z0-9_-]{4,40}$")


@router.post("/undo")
async def undo(
    body: UndoBody, p: Principal = Depends(EDITOR), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    try:
        applied = undo_last(_project_dir(body.project_id))
    except ApplyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await audit.record(
        db,
        "revision.undo",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="project",
        target_id=body.project_id,
    )
    return {"revision": applied.revision, "overlay_sha256": applied.overlay_sha256}
