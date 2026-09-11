"""Run orchestration service: start/inspect/approve production runs (shared by API and CLI)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.client import Client

from content_factory.config import get_settings
from content_factory.db.models import ActionItem, NodeState, ProductionRun, RunNode
from content_factory.schemas.content import ContentCampaign
from content_factory.services import durations
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


# Node states that will not move on their own. `complete`, `failed` and `skipped` are over and
# carry a real `duration_ms`, so estimating them would replace a measurement with a guess.
# `blocked` belongs here too and the reason is different: the stage *has* run — it generated,
# checked its own output and handed the decision to a person — so its remaining cost is a human
# looking at it, not seconds, and "~42 s" is the one thing that is certainly wrong.
_SETTLED = {NodeState.complete, NodeState.failed, NodeState.skipped, NodeState.blocked}


_NO_ETA: dict[str, Any] = {"eta_seconds": None, "eta_samples": 0}

_warming: set[asyncio.Task] = set()
"""Live background warms. Held because asyncio only weakly references a running task, and a
fire-and-forget task with no reference can be collected mid-flight."""


def _read_durations_in_background(build) -> None:
    """Read the duration history off the request path. `build` is `warm` or `rebuild`."""
    try:
        task = asyncio.create_task(asyncio.to_thread(build))
    except RuntimeError:
        return  # no running loop (a sync caller): the next async read will start it
    _warming.add(task)
    task.add_done_callback(_warming.discard)


def _elapsed_seconds(node: RunNode, now: datetime) -> float:
    """How long a running node has been running, from the transition that set it running.

    `updated_at` is the row's last write and a running node's last write is the transition into
    `running`, so this is the start of the stage. Naive timestamps come back from SQLite, which
    stores what it was given without a zone; they are UTC by construction here.
    """
    stamp = node.updated_at
    if stamp is None:
        return 0.0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return max((now - stamp).total_seconds(), 0.0)


def _run_forecast(
    nodes: Sequence[RunNode], workflow: str | None, now: datetime
) -> dict[str, Any] | None:
    """The run-level "done at", from the stages that have not finished.

    Built here rather than in the browser because the history lives on this machine, and computed
    from node *state* rather than from position in the list: a DAG runs several branches at once,
    so "everything after the running one" is not a thing the list order knows.

    None once every node is over — a zero the UI has to translate back into "done" is a worse
    contract than an absent field.
    """
    if all(n.state in _SETTLED for n in nodes):
        return None
    queued = [n.stage for n in nodes if n.state == NodeState.queued]
    # Several stages can be in flight at once (a DAG's branches overlap), and those run
    # concurrently, so the run waits on the *slowest* of them rather than on their sum. Whichever
    # has the most left is the one to charge; a node with no history has no claim to be the
    # longest, so it cannot displace one that does.
    in_flight: tuple[str, float] | None = None
    most_left = -1.0
    for node in nodes:
        if node.state != NodeState.running:
            continue
        elapsed = _elapsed_seconds(node, now)
        est = durations.estimate_stage(node.stage, workflow)
        left = max(est.seconds - elapsed, 0.0) if est is not None else -1.0
        if left > most_left:
            if in_flight is not None:
                queued.append(in_flight[0])  # demoted: it is not the one being waited on
            in_flight, most_left = (node.stage, elapsed), left
        else:
            queued.append(node.stage)
    fc = durations.forecast(queued, workflow, running=in_flight)
    return {
        "remaining_seconds": fc.remaining_seconds,
        "finish_at": fc.finish_at(now).isoformat(),
        "samples": fc.samples,
        "confident": fc.confident,
        "overdue": fc.overdue,
        "unknown_stages": list(fc.unknown),
    }


def _node_eta(node: RunNode, workflow: str | None, now: datetime) -> dict[str, Any]:
    """Per-node estimate: seconds still to come, and how much evidence is behind that."""
    if node.state in _SETTLED:
        return {"eta_seconds": None, "eta_samples": 0}
    est = durations.estimate_stage(node.stage, workflow)
    if est is None:
        return {"eta_seconds": None, "eta_samples": 0}
    left = est.seconds
    if node.state == NodeState.running:
        left = max(est.seconds - _elapsed_seconds(node, now), 0.0)
    return {"eta_seconds": round(left, 1), "eta_samples": est.samples}


def _run_workflow_name(run: ProductionRun) -> str | None:
    """The lane this run is, when the report names one.

    `generate_anchor` is 32 s on `image-set` and ten minutes on `audio-picture-story`; without a
    lane name the estimate falls back to the median across every lane, which is the right answer
    to a question that has no better one.
    """
    report = run.report
    name = report.get("workflow") if isinstance(report, dict) else None
    return name if isinstance(name, str) and name else None


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
    now = datetime.now(UTC)
    workflow = _run_workflow_name(run)
    # The estimate is skipped entirely until the history has been read in the background, and
    # never built on this thread. Doing it here — even inside `to_thread` — cost 410 ms of
    # GIL-holding YAML parsing on a view the UI polls every two seconds, and in a process that
    # also hosts a Temporal worker that starved the worker's activity heartbeats: it failed
    # `test_run_stop::..._cancels_the_node_in_flight` reproducibly with "Heartbeat timeout" and
    # passed as soon as this work left the request path. One poll without an ETA is nothing; a
    # cancelled run is not.
    warm = durations.is_warm()
    if not warm:
        _read_durations_in_background(durations.warm)
    elif durations.is_stale():
        # Every finished run changes these medians, and this process may run for days. The
        # rebuild swaps in whole, so the current answer keeps serving while it happens.
        _read_durations_in_background(durations.rebuild)
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
        # None once there is nothing left to wait for (see `_run_forecast`), and until the
        # history behind it has been read.
        "eta": _run_forecast(nodes, workflow, now) if warm else None,
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
                **(_node_eta(n, workflow, now) if warm else _NO_ETA),
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
