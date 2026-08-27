"""Runs against the compose Temporal dev server (marked integration). Captures a workflow history
into fixtures/temporal/ so the offline replay test can run in core CI without any server."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from temporalio.client import Client, WorkflowHistory
from temporalio.worker import Replayer, Worker

from content_factory.workflows.spike import (
    TASK_QUEUE,
    SpikeProductionRun,
    SpikeRunInput,
    run_stage,
)

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "temporal"
    / "spike_production_run.history.json"
)

pytestmark = pytest.mark.integration


async def _connect() -> Client:
    from content_factory.config import get_settings

    s = get_settings()
    return await Client.connect(s.temporal.address, namespace=s.temporal.namespace)


async def test_workflow_parks_on_approval_then_completes_and_history_replays() -> None:
    try:
        client = await _connect()
    except Exception as exc:
        pytest.skip(f"temporal dev server not reachable: {exc}")
    wf_id = f"spike-{uuid.uuid4().hex[:8]}"
    async with Worker(
        client, task_queue=TASK_QUEUE, workflows=[SpikeProductionRun], activities=[run_stage]
    ):
        handle = await client.start_workflow(
            SpikeProductionRun.run,
            SpikeRunInput(
                workspace_id="ws_demo0000001", project_id="prj_demo00000001", brief="hello"
            ),
            id=wf_id,
            task_queue=TASK_QUEUE,
        )
        for _ in range(100):
            if await handle.query(SpikeProductionRun.state) == "WAITING_FOR_APPROVAL":
                break
            await asyncio.sleep(0.05)
        assert await handle.query(SpikeProductionRun.state) == "WAITING_FOR_APPROVAL"
        await handle.signal(SpikeProductionRun.approve, "operator")
        result = await handle.result()

    assert result.approved_by == "operator"
    assert [s.idempotency_key for s in result.stages] == sorted(
        {s.idempotency_key for s in result.stages},
        key=[s.idempotency_key for s in result.stages].index,
    )
    assert result.state_history[-1] == "COMPLETE"
    assert "WAITING_FOR_APPROVAL" in result.state_history

    # Persist the history for the offline replay test and replay it right away.
    history = await handle.fetch_history()
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n")
    replayer = Replayer(workflows=[SpikeProductionRun])
    await replayer.replay_workflow(WorkflowHistory.from_json(wf_id, FIXTURE.read_text()))
