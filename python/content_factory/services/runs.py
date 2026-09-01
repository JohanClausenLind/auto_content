"""Run orchestration service: start/inspect/approve production runs (shared by API and CLI)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from content_factory.config import get_settings
from content_factory.db.models import ActionItem, ProductionRun, RunNode
from content_factory.schemas.content import ContentCampaign
from content_factory.workflows.production import ApprovalSignal, ProductionInput, ProductionWorkflow

REPO_ROOT = Path(__file__).resolve().parents[3]


def projects_root() -> Path:
    import os

    return Path(os.environ.get("CF_PROJECTS_DIR", str(REPO_ROOT / "projects")))


async def temporal_client() -> Client:
    s = get_settings()
    return await Client.connect(s.temporal.address, namespace=s.temporal.namespace)


async def start_run(
    campaign: ContentCampaign,
    *,
    quality: str = "demo",
    projects_dir: Path | None = None,
    artifacts_dir: Path | None = None,
    task_queue: str = "control",
    project_id: str | None = None,
) -> str:
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    client = await temporal_client()
    await client.start_workflow(
        ProductionWorkflow.run,
        ProductionInput(
            run_id=run_id,
            workspace_id=campaign.workspace_id,
            campaign_json=campaign.model_dump_json(),
            quality=quality,
            projects_dir=str(projects_dir or REPO_ROOT / "projects"),
            artifacts_dir=str(artifacts_dir or REPO_ROOT / "data" / "artifacts"),
            project_id=project_id,
        ),
        id=run_id,
        task_queue=task_queue,
    )
    return run_id


async def approve_run(
    run_id: str, *, actor: str, revision_hash: str, decision: str = "approve", reason: str = ""
) -> None:
    client = await temporal_client()
    handle = client.get_workflow_handle_for(ProductionWorkflow.run, run_id)
    await handle.signal(
        ProductionWorkflow.submit_approval,
        ApprovalSignal(actor=actor, revision_hash=revision_hash, decision=decision, reason=reason),
    )


async def run_view(db: AsyncSession, workspace_id: str, run_id: str) -> dict[str, Any] | None:
    run = (
        await db.execute(
            select(ProductionRun).where(
                ProductionRun.id == run_id, ProductionRun.workspace_id == workspace_id
            )
        )
    ).scalar_one_or_none()
    if run is None:
        return None
    nodes = (
        (
            await db.execute(
                select(RunNode).where(RunNode.run_id == run_id).order_by(RunNode.created_at)
            )
        )
        .scalars()
        .all()
    )
    return {
        "run_id": run.id,
        "state": run.state.value,
        "campaign_id": run.campaign_id,
        "project_id": run.project_id,
        "quality": run.quality,
        "preflight_revision_hash": run.preflight_revision_hash,
        "approved_by": run.approved_by,
        "error": run.error,
        "report": run.report,
        "nodes": [
            {
                "node_id": n.node_id,
                "stage": n.stage,
                "deliverable_id": n.deliverable_id,
                "state": n.state.value,
                "attempts": n.attempts,
                "cache_hit": n.cache_hit,
                "duration_ms": n.duration_ms,
                "error": n.error,
            }
            for n in nodes
        ],
    }


async def list_runs(
    db: AsyncSession, workspace_id: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(ProductionRun)
            .where(ProductionRun.workspace_id == workspace_id)
            .order_by(ProductionRun.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [
        {
            "run_id": r.id,
            "state": r.state.value,
            "campaign_id": r.campaign_id,
            "project_id": r.project_id,
            "quality": r.quality,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


async def list_action_items(
    db: AsyncSession, workspace_id: str, *, status: str = "open"
) -> list[dict[str, Any]]:
    from content_factory.db.models import ActionItemStatus

    rows = (
        await db.execute(
            select(ActionItem)
            .where(
                ActionItem.workspace_id == workspace_id,
                ActionItem.status == ActionItemStatus(status),
            )
            .order_by(ActionItem.created_at.desc())
        )
    ).scalars()
    return [
        {
            "id": a.id,
            "kind": a.kind,
            "severity": a.severity,
            "title": a.title,
            "body": a.body,
            "run_id": a.run_id,
            "deep_link": a.deep_link,
            "created_at": a.created_at.isoformat(),
        }
        for a in rows
    ]
