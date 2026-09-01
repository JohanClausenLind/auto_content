"""Phase-9 scheduling gate: a Temporal Schedule ticks a program on cadence with overlap SKIP and
a minimal catch-up window; pausing/unpausing never bursts overdue ticks."""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from temporalio.client import Client, ScheduleOverlapPolicy
from temporalio.worker import Worker

from content_factory.programs.scheduler import (
    ProgramTickWorkflow,
    create_program_schedule,
    delete_schedule,
    set_schedule_paused,
)
from content_factory.workflows.production import PRODUCTION_ACTIVITIES, ProductionWorkflow

pytestmark = pytest.mark.integration


def test_schedule_ticks_and_pause_prevents_burst(tmp_path) -> None:
    asyncio.run(_flow(tmp_path))


async def _flow(tmp_path) -> None:
    from content_factory.config import get_settings
    from tests.integration.test_production_workflow import _ensure_fixture_workspace

    s = get_settings()
    try:
        client = await Client.connect(s.temporal.address, namespace=s.temporal.namespace)
    except Exception as exc:
        pytest.skip(f"temporal unreachable: {exc}")
    await _ensure_fixture_workspace()
    queue = f"sched-{uuid.uuid4().hex[:8]}"
    program_id = f"prog{uuid.uuid4().hex[:8]}"
    schedule_id = None
    started: list[str] = []

    from temporalio import activity

    @activity.defn(name="start_program_run")
    async def fake_start(inp) -> dict:
        started.append(activity.info().workflow_id)
        return {"run_id": f"fake-{len(started)}", "tick": activity.info().workflow_id}

    async with Worker(
        client,
        task_queue=queue,
        workflows=[ProgramTickWorkflow, ProductionWorkflow],
        activities=[fake_start, *PRODUCTION_ACTIVITIES],
    ):
        schedule_id = await create_program_schedule(
            client,
            program_id=program_id,
            workspace_id="ws_demo00000001",
            every=timedelta(seconds=2),
            task_queue=queue,
            catch_up="skip",
            paused=False,
        )
        try:
            desc = await client.get_schedule_handle(schedule_id).describe()
            assert desc.schedule.policy.overlap == ScheduleOverlapPolicy.SKIP
            assert desc.schedule.policy.pause_on_failure is True
            for _ in range(120):
                if len(started) >= 2:
                    break
                await asyncio.sleep(0.25)
            assert len(started) >= 2, "schedule never ticked twice"
            # Pause for several intervals: overdue ticks are SKIPPED, not burst, on unpause.
            await set_schedule_paused(client, schedule_id, paused=True, reason="test pause")
            await asyncio.sleep(6)  # ~3 missed intervals
            count_at_unpause = len(started)
            await set_schedule_paused(client, schedule_id, paused=False, reason="test unpause")
            await asyncio.sleep(3.0)
            burst = len(started) - count_at_unpause
            assert burst <= 2, (
                f"unpause burst {burst} ticks (catch-up window must skip overdue ticks)"
            )
        finally:
            await delete_schedule(client, schedule_id)
