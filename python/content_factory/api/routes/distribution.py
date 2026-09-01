"""/v1/distribution: profiles (immutable authorized revisions) and the kill switch. Both are
step-up-gated sensitive actions; models can never call these (no MCP tool exists for them)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.api.deps import Principal, get_db, require_step_up
from content_factory.db.base import new_id, utcnow
from content_factory.db.models import DistributionProfile, DistributionState
from content_factory.services import audit

router = APIRouter(prefix="/v1/distribution", tags=["distribution"])


class ProfileBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    config: dict[str, Any] = Field(default_factory=dict)


@router.get("/profiles")
async def list_profiles(
    p: Principal = Depends(require_step_up), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(DistributionProfile)
            .where(DistributionProfile.workspace_id == p.workspace_id)
            .order_by(DistributionProfile.name, DistributionProfile.revision)
        )
    ).scalars()
    return [
        {
            "id": r.id,
            "name": r.name,
            "revision": r.revision,
            "authorized": r.authorized_at is not None,
            "disabled": r.disabled_at is not None,
            "config": r.config,
        }
        for r in rows
    ]


@router.post("/profiles", status_code=status.HTTP_201_CREATED)
async def create_profile_revision(
    body: ProfileBody, p: Principal = Depends(require_step_up), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Any change creates a NEW unauthorized revision; authorization is a separate step-up act."""
    latest = (
        await db.execute(
            select(DistributionProfile)
            .where(
                DistributionProfile.workspace_id == p.workspace_id,
                DistributionProfile.name == body.name,
            )
            .order_by(DistributionProfile.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    revision = (latest.revision + 1) if latest else 1
    row = DistributionProfile(
        id=new_id("dpf"),
        workspace_id=p.workspace_id,
        name=body.name,
        revision=revision,
        config=body.config,
    )
    db.add(row)
    await audit.record(
        db,
        "distribution.profile.revise",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="distribution_profile",
        target_id=row.id,
        detail={"revision": revision},
    )
    return {"id": row.id, "name": row.name, "revision": revision, "authorized": False}


@router.post("/profiles/{profile_id}/authorize")
async def authorize_profile(
    profile_id: str, p: Principal = Depends(require_step_up), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    row = (
        await db.execute(
            select(DistributionProfile).where(
                DistributionProfile.id == profile_id,
                DistributionProfile.workspace_id == p.workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    if row.disabled_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "this revision is disabled; create a new one")
    row.authorized_by = p.account.username
    row.authorized_at = utcnow()
    await audit.record(
        db,
        "distribution.profile.authorize",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="distribution_profile",
        target_id=row.id,
        detail={"revision": row.revision},
    )
    return {"id": row.id, "revision": row.revision, "authorized": True}


class KillSwitchBody(BaseModel):
    on: bool
    reason: str = Field(default="", max_length=500)


@router.get("/kill-switch")
async def get_kill_switch(
    p: Principal = Depends(require_step_up), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    row = await db.get(DistributionState, p.workspace_id)
    return {"kill_switch": row.kill_switch if row else True}


@router.post("/kill-switch")
async def set_kill_switch(
    body: KillSwitchBody,
    p: Principal = Depends(require_step_up),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    row = await db.get(DistributionState, p.workspace_id)
    if row is None:
        row = DistributionState(workspace_id=p.workspace_id, kill_switch=body.on)
        db.add(row)
    else:
        row.kill_switch = body.on
    row.updated_by = p.account.username
    row.updated_at = utcnow()
    await audit.record(
        db,
        "distribution.kill_switch",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        detail={"on": body.on, "reason": body.reason},
    )
    return {"kill_switch": body.on}
