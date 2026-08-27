"""Phase-0 Temporal spike: a durable workflow with activities, a signal wait (the pattern human
task slots use — parked, consuming no worker), and replay-safe determinism.

Production workflows (phase 6) follow the same rules demonstrated here:
* activities carry typed IO and an idempotency key;
* no wall-clock, randomness, or I/O inside workflow code except via Temporal APIs;
* waiting states are ``workflow.wait_condition`` on signals — they hold no worker slot.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

TASK_QUEUE = "control"


@dataclass
class StageInput:
    workspace_id: str
    project_id: str
    stage: str
    revision: str
    payload: str

    @property
    def idempotency_key(self) -> str:
        raw = f"{self.workspace_id}|{self.project_id}|{self.stage}|{self.revision}|{self.payload}"
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class StageOutput:
    idempotency_key: str
    artifact_hash: str


@activity.defn
async def run_stage(inp: StageInput) -> StageOutput:
    # Deterministic "work": the artifact hash is a pure function of the input, so executing the
    # activity twice (retry, replay) yields identical output — safe if executed twice.
    artifact = hashlib.sha256(f"artifact:{inp.stage}:{inp.payload}".encode()).hexdigest()
    return StageOutput(idempotency_key=inp.idempotency_key, artifact_hash=artifact)


@dataclass
class SpikeRunInput:
    workspace_id: str
    project_id: str
    brief: str


@dataclass
class SpikeRunResult:
    stages: list[StageOutput]
    approved_by: str
    state_history: list[str]


@workflow.defn
class SpikeProductionRun:
    """CREATED -> PREFLIGHTING -> WAITING_FOR_APPROVAL -> APPROVED -> PRODUCING -> COMPLETE."""

    def __init__(self) -> None:
        self._approved_by: str | None = None
        self._states: list[str] = ["CREATED"]

    @workflow.signal
    def approve(self, actor: str) -> None:
        self._approved_by = actor

    @workflow.query
    def state(self) -> str:
        return self._states[-1]

    @workflow.run
    async def run(self, inp: SpikeRunInput) -> SpikeRunResult:
        retry = RetryPolicy(maximum_attempts=3)
        opts = {"start_to_close_timeout": timedelta(seconds=30), "retry_policy": retry}
        outputs: list[StageOutput] = []

        self._states.append("PREFLIGHTING")
        outputs.append(
            await workflow.execute_activity(
                run_stage,
                StageInput(inp.workspace_id, inp.project_id, "preflight", "r1", inp.brief),
                **opts,
            )
        )

        # Parked: no worker slot is consumed while waiting for the operator's signal.
        self._states.append("WAITING_FOR_APPROVAL")
        await workflow.wait_condition(lambda: self._approved_by is not None)
        self._states.append("APPROVED")

        self._states.append("PRODUCING")
        for stage in ("research", "script", "render"):
            outputs.append(
                await workflow.execute_activity(
                    run_stage,
                    StageInput(inp.workspace_id, inp.project_id, stage, "r1", inp.brief),
                    **opts,
                )
            )
        self._states.append("COMPLETE")
        assert self._approved_by is not None
        return SpikeRunResult(
            stages=outputs, approved_by=self._approved_by, state_history=self._states
        )
