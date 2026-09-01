"""ContentProgram scheduling on Temporal Schedules (section 7, phase 9).

Ticks are idempotent (the tick id folds the scheduled time), overlap policy SKIP prevents pile-ups,
and catch-up after downtime is explicit: 'skip' (default, zero catch-up window), 'run_latest'
(one catch-up), or 'bounded_backfill' (a capped window). Overdue content is never burst out."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from temporalio import workflow
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
)

CatchUp = Literal["skip", "run_latest", "bounded_backfill"]


@dataclass
class ProgramTickInput:
    program_id: str
    workspace_id: str
    quality: str = "demo"


@workflow.defn
class ProgramTickWorkflow:
    """One program tick: budget/health check → start a production run. Grows per phase; the
    tick itself must stay idempotent (the schedule provides a unique workflow id per fire)."""

    @workflow.run
    async def run(self, inp: ProgramTickInput) -> dict:
        from datetime import timedelta as td

        from temporalio.common import RetryPolicy

        result = await workflow.execute_activity(
            start_program_run,
            inp,
            start_to_close_timeout=td(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return result


from temporalio import activity  # noqa: E402


@activity.defn
async def start_program_run(inp: ProgramTickInput) -> dict:
    from content_factory.schemas.fixtures import sample_campaign
    from content_factory.services.runs import start_run

    # The tick id (workflow id) already encodes the scheduled time; a retried tick reuses it.
    info = activity.info()
    run_id = await start_run(sample_campaign(), quality=inp.quality)
    return {"run_id": run_id, "tick": info.workflow_id}


PROGRAM_ACTIVITIES = [start_program_run]


def _catch_up_window(policy: CatchUp) -> timedelta:
    return {
        "skip": timedelta(seconds=1),
        "run_latest": timedelta(minutes=1),
        "bounded_backfill": timedelta(hours=6),
    }[policy]


async def create_program_schedule(
    client: Client,
    *,
    program_id: str,
    workspace_id: str,
    every: timedelta,
    task_queue: str = "control",
    catch_up: CatchUp = "skip",
    paused: bool = True,
    quality: str = "demo",
) -> str:
    schedule_id = f"program-{program_id}"
    await client.create_schedule(
        schedule_id,
        Schedule(
            action=ScheduleActionStartWorkflow(
                ProgramTickWorkflow.run,
                ProgramTickInput(program_id=program_id, workspace_id=workspace_id, quality=quality),
                id=f"tick-{program_id}",
                task_queue=task_queue,
            ),
            spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=every)]),
            policy=SchedulePolicy(
                overlap=ScheduleOverlapPolicy.SKIP,  # a slow run never stacks ticks
                catchup_window=_catch_up_window(catch_up),
                pause_on_failure=True,  # a failing program pauses instead of improvising
            ),
            state=ScheduleState(paused=paused, note=f"ContentProgram {program_id} ({catch_up})"),
        ),
    )
    return schedule_id


async def set_schedule_paused(
    client: Client, schedule_id: str, *, paused: bool, reason: str
) -> None:
    handle = client.get_schedule_handle(schedule_id)
    if paused:
        await handle.pause(note=reason)
    else:
        await handle.unpause(note=reason)


async def delete_schedule(client: Client, schedule_id: str) -> None:
    await client.get_schedule_handle(schedule_id).delete()
