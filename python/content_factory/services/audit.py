"""Append-only audit events for every connection, authorization, approval, publish, kill-switch,
import, and settings change (section 25)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.db.base import new_id, utcnow
from content_factory.db.models import AuditEvent


async def record(
    db: AsyncSession,
    action: str,
    *,
    actor_account_id: str | None,
    workspace_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: dict[str, Any] | None = None,
    ip: str | None = None,
) -> AuditEvent:
    ev = AuditEvent(
        id=new_id("aud"),
        workspace_id=workspace_id,
        actor_account_id=actor_account_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail or {},
        ip=ip,
        created_at=utcnow(),
    )
    db.add(ev)
    return ev
