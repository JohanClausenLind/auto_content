"""/v1/operations (Owner-only): doctor summary, queue/worker visibility, audit tail."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from content_factory.api.deps import Principal, get_db, require_owner

router = APIRouter(prefix="/v1/operations", tags=["operations"])


@router.get("/health")
async def health(p: Principal = Depends(require_owner)) -> dict[str, Any]:
    from content_factory.doctor import run_doctor

    report = run_doctor()
    return {"ok": report.ok, "checks": [c.model_dump(mode="json") for c in report.checks]}


@router.get("/audit")
async def audit_tail(
    limit: int = 50, p: Principal = Depends(require_owner), db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    from content_factory.db.models import AuditEvent

    rows = (
        await db.execute(
            select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(min(limit, 200))
        )
    ).scalars()
    return [
        {
            "id": a.id,
            "action": a.action,
            "actor": a.actor_account_id,
            "workspace_id": a.workspace_id,
            "target": f"{a.target_type}:{a.target_id}" if a.target_type else None,
            "created_at": a.created_at.isoformat(),
        }
        for a in rows
    ]
