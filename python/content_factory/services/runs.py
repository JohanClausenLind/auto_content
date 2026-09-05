"""Run orchestration service: start/inspect/approve production runs (shared by API and CLI)."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from content_factory.config import get_settings
from content_factory.db.models import ActionItem, ProductionRun, RunNode
from content_factory.schemas.content import ContentCampaign
from content_factory.workflows.production import (
    ApprovalSignal,
    ProductionInput,
    ProductionWorkflow,
    StopSignal,
)

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
    dag_json: str = "",
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
            dag_json=dag_json,
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


async def open_run_ids(*, task_queue: str | None = None) -> list[str]:
    """Every ProductionWorkflow Temporal still has running, newest first.

    Temporal is asked rather than the runs table because Temporal is what is still executing:
    a row can say PRODUCING because the process that would have corrected it was killed.
    """
    client = await temporal_client()
    query = 'WorkflowType = "ProductionWorkflow" AND ExecutionStatus = "Running"'
    if task_queue:
        query += f' AND TaskQueue = "{task_queue}"'
    found: list[tuple[Any, str]] = []
    async for execution in client.list_workflows(query):
        found.append((execution.start_time, execution.id))
    return [run_id for _started, run_id in sorted(found, reverse=True)]


async def stop_run(
    run_id: str,
    *,
    actor: str,
    reason: str = "",
    graceful: bool = False,
    wait_s: float = 15.0,
) -> dict[str, Any]:
    """Stop one durable run: ask it to stop, and cut it off if it does not.

    Three rungs, because each one can fail to land and "stop" has to mean stop:

    1. **Signal.** The clean path: the workflow closes itself at the next node boundary, resolves
       what it was asking a person for, and records CANCELLED itself.
    2. **Cancel**, when a node already inside a GPU stage will not reach that boundary for
       minutes. This kills the activity, and the workflow usually still closes itself CANCELLED.
    3. **Terminate**, when even that does not land. Cancellation is a *request a worker has to
       process*, and by then there may be no worker — ``just stop`` kills it in the same breath,
       and a dev Temporal accumulates runs whose worker is long gone. Terminate is server-side
       and needs nobody's cooperation.

    The row is corrected from here after 2 and 3, because a workflow that was cut off cannot be
    relied on to run one more activity to write its own state down.
    """
    from temporalio.service import RPCError, RPCStatusCode

    client = await temporal_client()
    handle = client.get_workflow_handle_for(ProductionWorkflow.run, run_id)
    try:
        await handle.signal(ProductionWorkflow.request_stop, StopSignal(actor=actor, reason=reason))
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise
        return {"run_id": run_id, "outcome": "not running", "detail": "no open workflow"}
    if graceful:
        return {"run_id": run_id, "outcome": "stop requested"}
    if await _closed_within(handle, wait_s):
        return {"run_id": run_id, "outcome": "stopped"}
    detail = f"stopped by {actor}" + (f": {reason}" if reason else "")
    await handle.cancel()
    if await _closed_within(handle, 10.0):
        outcome = "cancelled"
    else:
        await handle.terminate(reason=detail)
        await _closed_within(handle, 10.0)
        outcome = "terminated"
    try:
        corrected = await record_cancelled(run_id, actor=actor, reason=reason)
    except Exception as exc:  # the engine stopped either way; say what could not be written down
        return {"run_id": run_id, "outcome": outcome, "detail": f"row not corrected: {exc}"}
    return {"run_id": run_id, "outcome": outcome, **corrected}


async def _closed_within(handle: Any, seconds: float) -> bool:
    """Poll until the workflow is no longer running. Polling (rather than awaiting the result)
    keeps a run that closes normally and one that is cancelled on the same code path."""
    from temporalio.client import WorkflowExecutionStatus

    deadline = asyncio.get_running_loop().time() + max(seconds, 0.0)
    while True:
        description = await handle.describe()
        if description.status != WorkflowExecutionStatus.RUNNING:
            return True
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.5)


async def record_cancelled(run_id: str, *, actor: str, reason: str = "") -> dict[str, int]:
    """Write CANCELLED over a run whose workflow was cut off, and close what it left open.

    Only ever called after the workflow is gone. A node left at ``running`` is recorded failed
    with the reason, because nothing is running: the alternative is a canvas that spins for ever
    on a node whose activity died with the worker.
    """
    from sqlalchemy import update as sa_update

    from content_factory.db.base import utcnow
    from content_factory.db.models import ActionItemStatus, NodeState, RunState
    from content_factory.db.session import session_scope

    detail = f"stopped by {actor}" + (f": {reason}" if reason else "")
    async with session_scope() as db:
        run = await db.execute(
            sa_update(ProductionRun)
            .where(
                ProductionRun.id == run_id,
                ProductionRun.state.notin_(
                    [RunState.complete, RunState.failed, RunState.cancelled]
                ),
            )
            .values(state=RunState.cancelled, error=detail)
        )
        nodes = await db.execute(
            sa_update(RunNode)
            .where(RunNode.run_id == run_id, RunNode.state == NodeState.running)
            .values(state=NodeState.failed, error=detail)
        )
        items = await db.execute(
            sa_update(ActionItem)
            .where(ActionItem.run_id == run_id, ActionItem.status == ActionItemStatus.open)
            .values(status=ActionItemStatus.resolved, resolved_at=utcnow())
        )
    return {
        "runs_corrected": getattr(run, "rowcount", 0) or 0,
        "nodes_corrected": getattr(nodes, "rowcount", 0) or 0,
        "action_items_resolved": getattr(items, "rowcount", 0) or 0,
    }


# The run views poll every couple of seconds; dag.json is written once at compile time, so a
# parse cached by (path, mtime_ns) turns each poll into a single stat instead of a full
# read+parse of the DAG on the event loop's thread pool.
_dag_edges_cache: dict[str, tuple[int, list[dict[str, str]]]] = {}


def dag_edges(project_dir: Path) -> list[dict[str, str]] | None:
    """The run's real dependency edges from the compiled dag.json, or None when unavailable.
    The file is the workflow's own artifact, so the canvas can draw truth instead of guessing
    chains from node order (hand-drawn workspace graphs are not chains)."""
    path = project_dir / "dag.json"
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    cached = _dag_edges_cache.get(str(path))
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]
    try:
        import json

        dag = json.loads(path.read_text())
        edges = [
            {"source": dep, "target": node["node_id"]}
            for node in dag.get("nodes", [])
            for dep in node.get("depends_on", [])
        ]
    except (OSError, ValueError, KeyError, TypeError):
        _dag_edges_cache.pop(str(path), None)
        return None
    _dag_edges_cache[str(path)] = (mtime_ns, edges)
    return edges


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
        # File I/O off the event loop: the run views poll this every couple of seconds.
        "edges": await asyncio.to_thread(dag_edges, projects_root() / run.project_id),
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
