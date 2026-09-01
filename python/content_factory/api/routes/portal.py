"""External request portal (phase 12). Editors mint expiring, revocable submit links; the public
endpoint accepts ONLY a valid token + a valid brief and creates a PortalBrief + ActionItem — never
a run, never any editor capability. Tokens are stored as hashes; plaintext is shown once."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.api.deps import Principal, get_db, require_role
from content_factory.config import get_settings
from content_factory.db.base import new_id, utcnow
from content_factory.db.models import ActionItem, PortalBrief, PortalBriefStatus, PortalLink, Role
from content_factory.portal.requests import (
    PortalError,
    make_portal_token,
    validate_submission,
    verify_portal_token,
)
from content_factory.services import audit

router = APIRouter(tags=["portal"])
VIEWER = require_role(Role.viewer)
EDITOR = require_role(Role.editor)


def _secret() -> str:
    secret = os.environ.get(get_settings().portal.secret_env, "")
    if len(secret) < 16:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "portal signing secret is not configured (set CF_PORTAL_SECRET, >=16 chars)",
        )
    return secret


def _link_dict(link: PortalLink) -> dict[str, Any]:
    return {
        "id": link.id,
        "label": link.label,
        "expires_at": link.expires_at.isoformat(),
        "revoked": link.revoked_at is not None,
    }


class PortalLinkBody(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    ttl_days: int | None = Field(default=None, ge=1, le=365)


@router.post("/v1/portal-links", status_code=status.HTTP_201_CREATED)
async def create_portal_link(
    body: PortalLinkBody,
    p: Principal = Depends(EDITOR),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    settings = get_settings()
    ttl_days = body.ttl_days or settings.portal.default_ttl_days
    token = make_portal_token(
        workspace_id=p.workspace_id, secret=_secret(), ttl_seconds=ttl_days * 86400
    )
    link = PortalLink(
        id=new_id("plink"),
        workspace_id=p.workspace_id,
        label=body.label,
        token_sha256=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=utcnow() + timedelta(days=ttl_days),
    )
    db.add(link)
    await audit.record(
        db,
        "portal_link.create",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="portal_link",
        target_id=link.id,
    )
    # The token appears exactly once, in this response; only its hash is stored.
    return {
        **_link_dict(link),
        "token": token,
        "submit_url": f"{settings.public_base_url}/portal?token={token}",
    }


@router.get("/v1/portal-links")
async def list_portal_links(
    p: Principal = Depends(VIEWER), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(PortalLink)
            .where(PortalLink.workspace_id == p.workspace_id)
            .order_by(PortalLink.created_at.desc())
        )
    ).scalars()
    return [_link_dict(r) for r in rows]


@router.delete("/v1/portal-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_portal_link(
    link_id: str, p: Principal = Depends(EDITOR), db: AsyncSession = Depends(get_db)
) -> None:
    link = (
        await db.execute(
            select(PortalLink).where(
                PortalLink.id == link_id, PortalLink.workspace_id == p.workspace_id
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    if link.revoked_at is None:
        link.revoked_at = utcnow()
        await audit.record(
            db,
            "portal_link.revoke",
            actor_account_id=p.account.id,
            workspace_id=p.workspace_id,
            target_type="portal_link",
            target_id=link.id,
        )


class PortalSubmitBody(BaseModel):
    token: str = Field(min_length=16, max_length=2000)
    topic: str = Field(max_length=500)
    objective: str = Field(max_length=1000)
    contact: str = Field(max_length=300)
    deadline: str | None = Field(default=None, max_length=100)


@router.post("/portal/briefs", status_code=status.HTTP_201_CREATED)
async def submit_brief(body: PortalSubmitBody, db: AsyncSession = Depends(get_db)) -> dict:
    """PUBLIC: token-authorized brief submission. The token grants this and nothing else."""
    if not get_settings().portal.enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    try:
        workspace_id = verify_portal_token(body.token, secret=_secret())
        submission = validate_submission(body.model_dump())
    except PortalError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    link = (
        await db.execute(
            select(PortalLink).where(
                PortalLink.token_sha256 == hashlib.sha256(body.token.encode()).hexdigest(),
                PortalLink.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if link is None or link.revoked_at is not None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "this portal link is no longer active")
    brief = PortalBrief(
        id=new_id("brief"),
        workspace_id=workspace_id,
        link_id=link.id,
        topic=submission.topic,
        objective=submission.objective,
        deadline=submission.deadline,
        contact=submission.contact,
    )
    db.add(brief)
    db.add(
        ActionItem(
            id=new_id("ai"),
            workspace_id=workspace_id,
            kind="portal_brief",
            severity="normal",
            title=f"New brief: {submission.topic[:120]}",
            body=f"From {submission.contact}: {submission.objective[:400]}",
            dedupe_key=f"portal_brief:{brief.id}",
            deep_link="/portal/briefs",
        )
    )
    await audit.record(
        db,
        "portal_brief.submit",
        actor_account_id=None,  # external submitter — no account, link is the actor
        workspace_id=workspace_id,
        target_type="portal_brief",
        target_id=brief.id,
        detail={"link_id": link.id},
    )
    return {"id": brief.id, "status": "received"}


def _brief_dict(b: PortalBrief) -> dict[str, Any]:
    return {
        "id": b.id,
        "topic": b.topic,
        "objective": b.objective,
        "deadline": b.deadline,
        "contact": b.contact,
        "status": b.status.value,
        "created_at": b.created_at.isoformat() if isinstance(b.created_at, datetime) else None,
    }


@router.get("/v1/portal-briefs")
async def list_briefs(
    p: Principal = Depends(VIEWER), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(PortalBrief)
            .where(PortalBrief.workspace_id == p.workspace_id)
            .order_by(PortalBrief.created_at.desc())
        )
    ).scalars()
    return [_brief_dict(b) for b in rows]


class DecisionBody(BaseModel):
    decision: PortalBriefStatus


@router.post("/v1/portal-briefs/{brief_id}/decision")
async def decide_brief(
    brief_id: str,
    body: DecisionBody,
    p: Principal = Depends(EDITOR),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if body.decision == PortalBriefStatus.new:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "decision must not be 'new'")
    brief = (
        await db.execute(
            select(PortalBrief).where(
                PortalBrief.id == brief_id, PortalBrief.workspace_id == p.workspace_id
            )
        )
    ).scalar_one_or_none()
    if brief is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    brief.status = body.decision
    brief.decided_by = p.account.id
    brief.decided_at = utcnow()
    await audit.record(
        db,
        f"portal_brief.{body.decision.value}",
        actor_account_id=p.account.id,
        workspace_id=p.workspace_id,
        target_type="portal_brief",
        target_id=brief.id,
    )
    return _brief_dict(brief)
