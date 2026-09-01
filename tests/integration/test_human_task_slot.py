"""Phase-11 gate: a human write_copy slot parks the run (no worker consumed, survives waiting),
rejects an invalid submission with reasons, accepts a valid one, and the completeness gate holds —
nothing downstream ran before the slot was filled."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from content_factory.schemas import content
from content_factory.schemas.fixtures import WS, sample_brief
from content_factory.services.runs import approve_run
from content_factory.workflows.production import (
    PRODUCTION_ACTIVITIES,
    HumanTaskSubmission,
    ProductionInput,
    ProductionWorkflow,
)
from tests.integration.test_production_workflow import (
    _ensure_fixture_workspace,
    _executions,
    _wait_state,
)

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]
BROWSER = REPO / "apps" / "renderer" / "node_modules" / ".remotion"


@pytest.mark.skipif(not BROWSER.exists(), reason="headless browser not downloaded")
def test_human_slot_parks_validates_and_gates_downstream(tmp_path: Path) -> None:
    asyncio.run(_flow(tmp_path))


async def _flow(tmp_path: Path) -> None:
    from content_factory.config import get_settings

    s = get_settings()
    try:
        client = await Client.connect(s.temporal.address, namespace=s.temporal.namespace)
    except Exception as exc:
        pytest.skip(f"temporal unreachable: {exc}")
    await _ensure_fixture_workspace()
    queue = f"human-{uuid.uuid4().hex[:8]}"
    export = content.DestinationBinding(
        destination=content.Destination(
            destination_id="dst_export000001", platform="export", capability_revision="2026-09-01"
        ),
        visibility="export_only",
    )
    campaign = content.ContentCampaign(
        campaign_id="cmp_humantask001",
        workspace_id=WS,
        brief=sample_brief(),
        deliverables=(
            content.CarouselSpec(
                deliverable_id="dlv_carousel0001",
                title="Human-written carousel",
                card_count=3,
                destinations=(export,),
            ),
        ),
    )
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    async with Worker(
        client, task_queue=queue, workflows=[ProductionWorkflow], activities=PRODUCTION_ACTIVITIES
    ):
        handle = await client.start_workflow(
            ProductionWorkflow.run,
            ProductionInput(
                run_id=run_id,
                workspace_id=WS,
                campaign_json=campaign.model_dump_json(),
                quality="demo",
                projects_dir=str(tmp_path / "projects"),
                artifacts_dir=str(tmp_path / "artifacts"),
                human_stages_json=json.dumps({"dlv_carousel0001": ["write_copy"]}),
            ),
            id=run_id,
            task_queue=queue,
        )
        view = await _wait_state(run_id, "WAITING_FOR_APPROVAL")
        await approve_run(run_id, actor="operator", revision_hash=view["preflight_revision_hash"])

        # The run parks at the human node: no pending activities = no worker consumed.
        node_id = "write_copy:dlv_carousel0001"
        for _ in range(200):
            waiting = await handle.query(ProductionWorkflow.waiting_human_tasks)
            if node_id in waiting:
                break
            await asyncio.sleep(0.2)
        assert node_id in await handle.query(ProductionWorkflow.waiting_human_tasks)
        desc = await handle.describe()
        assert not desc.raw_description.pending_activities  # parked, consuming nothing
        project_dir = tmp_path / "projects" / view["project_id"]
        counts = _executions(project_dir)
        assert not any(
            k.startswith(("compile_cards", "render_cards")) for k in counts
        )  # completeness gate

        # An invalid submission is rejected with reasons; the slot stays parked.
        await handle.signal(
            ProductionWorkflow.submit_human_task,
            HumanTaskSubmission(
                node_id=node_id,
                payload_json=json.dumps(
                    {"cards": [{"card_id": "card_000000000001", "text": "  "}]}
                ),
            ),
        )
        await asyncio.sleep(1.5)
        assert node_id in await handle.query(ProductionWorkflow.waiting_human_tasks)

        # A valid submission unparks the slot; the pipeline finishes with the human's exact copy.
        cards = [
            {"card_id": f"card_{i:012d}", "text": t}
            for i, t in enumerate(
                ["Hand-written card one.", "Hand-written card two.", "Sources at the end."], start=1
            )
        ]
        await handle.signal(
            ProductionWorkflow.submit_human_task,
            HumanTaskSubmission(node_id=node_id, payload_json=json.dumps({"cards": cards})),
        )
        await _wait_state(run_id, "COMPLETE", timeout_s=180)
        copy = json.loads(
            (project_dir / "deliverables" / "dlv_carousel0001" / "copy.json").read_text()
        )
        assert copy["cards"][0]["text"] == "Hand-written card one."
        counts = _executions(project_dir)
        assert (
            counts["render_cards:dlv_carousel0001"] == 1
            and counts["render_card:card_000000000001"] == 1
        )
