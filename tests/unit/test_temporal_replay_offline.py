"""Offline replay: the recorded history must replay against the current workflow code with no
non-determinism errors. Runs in core CI without a Temporal server."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from temporalio import workflow
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from content_factory.workflows.spike import SpikeProductionRun, SpikeRunInput, StageInput, run_stage

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "temporal"
    / "spike_production_run.history.json"
)


@workflow.defn(name="SpikeProductionRun", sandboxed=False)
class DriftedSpikeProductionRun:
    """Same workflow type name, signals and queries, but a different command sequence."""

    def __init__(self) -> None:
        self._approved_by: str | None = None

    @workflow.signal
    def approve(self, actor: str) -> None:
        self._approved_by = actor

    @workflow.query
    def state(self) -> str:
        return "DRIFTED"

    @workflow.run
    async def run(self, inp: SpikeRunInput) -> None:
        await workflow.execute_activity(
            run_stage,
            StageInput(inp.workspace_id, inp.project_id, "somethingelse", "r9", inp.brief),
            start_to_close_timeout=timedelta(seconds=1),
        )
        await workflow.sleep(1)


@pytest.mark.skipif(not FIXTURE.exists(), reason="history fixture not captured yet")
async def test_recorded_history_replays_deterministically() -> None:
    history = WorkflowHistory.from_json("spike-replay", FIXTURE.read_text())
    await Replayer(workflows=[SpikeProductionRun]).replay_workflow(history)


@pytest.mark.skipif(not FIXTURE.exists(), reason="history fixture not captured yet")
async def test_changed_workflow_code_is_detected_as_nondeterministic() -> None:
    history = WorkflowHistory.from_json("spike-replay", FIXTURE.read_text())
    with pytest.raises(Exception, match=r"(?i)nondeterminism|non-determinism|mismatch"):
        await Replayer(workflows=[DriftedSpikeProductionRun]).replay_workflow(history)
