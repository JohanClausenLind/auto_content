"""Stopping a durable run (integration: compose postgres + temporal).

Two ways a run is stopped, and both have to leave the same truthful state behind:
1. parked waiting for approval — the signal alone closes it, no cancellation needed;
2. mid-production with a node in flight — the signal cannot land until the node lets go, so the
   workflow is cancelled and the row is corrected from the client, because a cancelled workflow
   cannot run one more activity to correct it itself.

What is asserted in both: the run says CANCELLED with who stopped it, nothing is left asking a
person for something, and no node is left claiming to be running.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from temporalio.client import WorkflowExecutionStatus
from temporalio.worker import Worker

from content_factory.db.models import (
    ActionItem,
    ActionItemStatus,
    NodeState,
    ProductionRun,
    RunNode,
    RunState,
)
from content_factory.schemas.fixtures import WS
from content_factory.services.runs import (
    approve_run,
    open_run_ids,
    record_cancelled,
    run_view,
    start_run,
    stop_run,
)
from content_factory.workflows.production import PRODUCTION_ACTIVITIES, ProductionWorkflow
from tests.integration.test_production_workflow import (
    BROWSER,
    _campaign,
    _connect,
    _ensure_fixture_workspace,
    _wait_state,
)

pytestmark = pytest.mark.integration


async def _items(run_id: str) -> list[ActionItem]:
    from content_factory.db.session import get_sessionmaker

    async with get_sessionmaker()() as db:
        result = await db.execute(select(ActionItem).where(ActionItem.run_id == run_id))
        return list(result.scalars().all())


@pytest.mark.skipif(not BROWSER.exists(), reason="headless browser not downloaded")
def test_stopping_a_waiting_run_closes_it_and_clears_what_it_was_asking_for(tmp_path: Path) -> None:
    asyncio.run(_stop_while_waiting(tmp_path))


async def _stop_while_waiting(tmp_path: Path) -> None:
    try:
        client = await _connect()
    except Exception as exc:
        pytest.skip(f"temporal unreachable: {exc}")
    await _ensure_fixture_workspace()
    queue = f"stop-{uuid.uuid4().hex[:8]}"
    async with Worker(
        client,
        task_queue=queue,
        workflows=[ProductionWorkflow],
        activities=PRODUCTION_ACTIVITIES,
    ):
        run_id = await start_run(
            _campaign(),
            quality="demo",
            projects_dir=tmp_path / "projects",
            artifacts_dir=tmp_path / "artifacts",
            task_queue=queue,
        )
        await _wait_state(run_id, "WAITING_FOR_APPROVAL")
        assert run_id in await open_run_ids()
        assert any(
            i.kind == "approval_waiting" and i.status == ActionItemStatus.open
            for i in await _items(run_id)
        )

        result = await stop_run(run_id, actor="operator", reason="just stop")
        assert result["outcome"] == "stopped"  # the signal alone; nothing had to be cancelled

        view = await _wait_state(run_id, "CANCELLED")
        assert "stopped by operator" in (view["error"] or "")
        assert all(i.status == ActionItemStatus.resolved for i in await _items(run_id))
        assert all(n["state"] != "running" for n in view["nodes"])
        description = await client.get_workflow_handle(run_id).describe()
        assert description.status == WorkflowExecutionStatus.COMPLETED
        assert run_id not in await open_run_ids()

        # Stopping a run that has already stopped says so instead of failing.
        again = await stop_run(run_id, actor="operator")
        assert again["outcome"] == "not running"


@pytest.mark.skipif(not BROWSER.exists(), reason="headless browser not downloaded")
def test_stopping_a_producing_run_cancels_the_node_in_flight(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CF_TEST_STAGE_DELAY_S", "8")
    asyncio.run(_stop_while_producing(tmp_path))


async def _stop_while_producing(tmp_path: Path) -> None:
    try:
        client = await _connect()
    except Exception as exc:
        pytest.skip(f"temporal unreachable: {exc}")
    await _ensure_fixture_workspace()
    queue = f"stop-{uuid.uuid4().hex[:8]}"
    async with Worker(
        client,
        task_queue=queue,
        workflows=[ProductionWorkflow],
        activities=PRODUCTION_ACTIVITIES,
    ):
        run_id = await start_run(
            _campaign(),
            quality="demo",
            projects_dir=tmp_path / "projects",
            artifacts_dir=tmp_path / "artifacts",
            task_queue=queue,
        )
        view = await _wait_state(run_id, "WAITING_FOR_APPROVAL", timeout_s=180)
        await approve_run(run_id, actor="operator", revision_hash=view["preflight_revision_hash"])
        await _wait_state(run_id, "PRODUCING", timeout_s=180)
        # Wait until a node is actually executing, so the stop has something to cut.
        for _ in range(240):
            current = await run_view_now(run_id)
            if any(n["state"] == "running" for n in current["nodes"]):
                break
            await asyncio.sleep(0.25)
        else:
            pytest.fail("no node ever started")

        result = await stop_run(run_id, actor="operator", reason="just stop", wait_s=2.0)
        assert result["outcome"] == "cancelled"

        final = await run_view_now(run_id)
        assert final["state"] == "CANCELLED"
        assert "stopped by operator" in (final["error"] or "")
        # Nothing may still claim to be running: the activity died with the cancellation.
        assert all(n["state"] != "running" for n in final["nodes"])
        assert all(i.status == ActionItemStatus.resolved for i in await _items(run_id))
        description = await client.get_workflow_handle(run_id).describe()
        assert description.status in {
            WorkflowExecutionStatus.CANCELED,
            WorkflowExecutionStatus.COMPLETED,
        }


async def run_view_now(run_id: str) -> dict:
    from content_factory.db.session import get_sessionmaker

    async with get_sessionmaker()() as db:
        view = await run_view(db, WS, run_id)
    assert view is not None
    return view


def test_a_run_whose_workflow_was_cut_off_has_its_row_corrected() -> None:
    """The backstop for a hard cancel: Temporal has stopped executing, so nothing inside the run
    is left to write down what happened to it. Idempotent, because a stop can be pressed twice."""
    asyncio.run(_reconcile_flow())


async def _reconcile_flow() -> None:
    from content_factory.db.base import new_id
    from content_factory.db.session import get_sessionmaker, session_scope

    try:
        await _connect()
    except Exception as exc:
        pytest.skip(f"temporal unreachable: {exc}")
    await _ensure_fixture_workspace()
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    async with session_scope() as db:
        db.add(
            ProductionRun(
                id=run_id,
                workspace_id=WS,
                campaign_id="cmp_wftest000001",
                project_id="prj_stop00000001",
                state=RunState.producing,
                quality="demo",
            )
        )
        db.add(
            RunNode(
                id=new_id("rn"),
                workspace_id=WS,
                run_id=run_id,
                node_id="render_static:dlv_image0000001",
                stage="render_static",
                deliverable_id="dlv_image0000001",
                state=NodeState.running,
            )
        )
        db.add(
            ActionItem(
                id=new_id("ai"),
                workspace_id=WS,
                kind="approval_waiting",
                severity="normal",
                title="Preflight approval needed",
                body="",
                dedupe_key=f"approval:{run_id}",
                run_id=run_id,
            )
        )

    corrected = await record_cancelled(run_id, actor="operator", reason="just stop")
    assert corrected == {
        "runs_corrected": 1,
        "nodes_corrected": 1,
        "action_items_resolved": 1,
    }
    async with get_sessionmaker()() as db:
        view = await run_view(db, WS, run_id)
    assert view is not None
    assert view["state"] == "CANCELLED" and view["error"] == "stopped by operator: just stop"
    # Nothing may still claim to be running: the activity died with the cancellation.
    assert [n["state"] for n in view["nodes"]] == ["failed"]
    assert all(i.status == ActionItemStatus.resolved for i in await _items(run_id))

    # Pressing stop again corrects nothing and breaks nothing.
    assert (await record_cancelled(run_id, actor="operator"))["runs_corrected"] == 0
