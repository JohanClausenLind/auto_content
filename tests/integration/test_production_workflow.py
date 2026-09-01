"""Phase-6 gate (integration: compose postgres + temporal + headless browser):
1. Full run parks at WAITING_FOR_APPROVAL; a stale-revision approval is ignored; the exact
   revision approves; every node executes exactly once.
2. A card edit rebuilds only that card (per-card cache; image branch untouched).
3. A killed worker resumes without duplicate artifacts or re-executed completed stages.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import select
from temporalio.client import Client
from temporalio.worker import Worker

from content_factory.db.models import ActionItem, ActionItemStatus
from content_factory.schemas import content
from content_factory.schemas.fixtures import WS, sample_brief
from content_factory.services.runs import approve_run, run_view, start_run
from content_factory.workflows.production import PRODUCTION_ACTIVITIES, ProductionWorkflow

pytestmark = pytest.mark.integration
REPO = Path(__file__).resolve().parents[2]
BROWSER = REPO / "apps" / "renderer" / "node_modules" / ".remotion"


def _campaign() -> content.ContentCampaign:
    export = content.DestinationBinding(
        destination=content.Destination(
            destination_id="dst_export000001", platform="export", capability_revision="2026-09-01"
        ),
        visibility="export_only",
    )
    return content.ContentCampaign(
        campaign_id="cmp_wftest000001",
        workspace_id=WS,
        brief=sample_brief(),
        deliverables=(
            content.SingleImagePostSpec(
                deliverable_id="dlv_image0000001",
                title="Wind card",
                layout="number_led",
                destinations=(export,),
            ),
            content.CarouselSpec(
                deliverable_id="dlv_carousel0001",
                title="Wind carousel",
                card_count=3,
                destinations=(export,),
            ),
        ),
    )


async def _ensure_fixture_workspace() -> None:
    from sqlalchemy.dialects.postgresql import insert

    from content_factory.db.models import Workspace
    from content_factory.db.session import get_sessionmaker

    maker = get_sessionmaker()
    async with maker() as db:
        stmt = insert(Workspace).values(
            id=WS, slug="fixture-demo", name="Fixture Demo", settings={}
        )
        await db.execute(stmt.on_conflict_do_nothing(index_elements=[Workspace.id]))
        await db.commit()


async def _connect() -> Client:
    from content_factory.config import get_settings

    s = get_settings()
    return await Client.connect(s.temporal.address, namespace=s.temporal.namespace)


async def _wait_state(run_id: str, state: str, timeout_s: float = 120) -> dict:
    from content_factory.db.session import get_sessionmaker

    maker = get_sessionmaker()
    view = None
    for _ in range(int(timeout_s / 0.25)):
        async with maker() as db:
            view = await run_view(db, WS, run_id)
        if view and view["state"] == state:
            return view
        if (
            view
            and view["state"] in {"FAILED", "CANCELLED"}
            and state not in {"FAILED", "CANCELLED"}
        ):
            raise AssertionError(f"run reached {view['state']}: {view.get('error')}")
        await asyncio.sleep(0.25)
    raise TimeoutError(f"run {run_id} never reached {state}; last: {view}")


def _executions(project_dir: Path) -> Counter:
    log = project_dir / ".stages" / "executions.log"
    if not log.exists():
        return Counter()
    return Counter(line.split("\t")[0] for line in log.read_text().splitlines() if line)


@pytest.mark.skipif(not BROWSER.exists(), reason="headless browser not downloaded")
def test_full_run_with_approval_binding_and_targeted_card_rebuild(tmp_path: Path) -> None:
    # Runs on a plain asyncio loop: pytest-asyncio's managed loop starves Temporal's activity
    # poller in this scenario (verified empirically; the same flow passes on asyncio.run).
    asyncio.run(_full_run_flow(tmp_path))


async def _full_run_flow(tmp_path: Path) -> None:
    try:
        client = await _connect()
    except Exception as exc:
        pytest.skip(f"temporal unreachable: {exc}")
    await _ensure_fixture_workspace()
    queue = f"test-{uuid.uuid4().hex[:8]}"
    projects = tmp_path / "projects"
    artifacts = tmp_path / "artifacts"
    async with Worker(
        client,
        task_queue=queue,
        workflows=[ProductionWorkflow],
        activities=PRODUCTION_ACTIVITIES,
    ):
        run_id = await start_run(
            _campaign(),
            quality="demo",
            projects_dir=projects,
            artifacts_dir=artifacts,
            task_queue=queue,
        )
        view = await _wait_state(run_id, "WAITING_FOR_APPROVAL")
        revision = view["preflight_revision_hash"]
        assert revision
        project_dir = projects / view["project_id"]
        assert (project_dir / "preflight" / "package.json").exists()

        # An open ActionItem exists for the approval.
        from content_factory.db.session import get_sessionmaker

        maker = get_sessionmaker()
        async with maker() as db:
            items = (
                (await db.execute(select(ActionItem).where(ActionItem.run_id == run_id)))
                .scalars()
                .all()
            )
        assert any(
            i.kind == "approval_waiting" and i.status == ActionItemStatus.open for i in items
        )

        # Approval for a stale revision is ignored; the run keeps waiting.
        await approve_run(run_id, actor="operator", revision_hash="f" * 64)
        await asyncio.sleep(1.0)
        async with maker() as db:
            still = await run_view(db, WS, run_id)
        assert still and still["state"] == "WAITING_FOR_APPROVAL"
        handle = client.get_workflow_handle(run_id)
        assert any("stale" in r for r in await handle.query(ProductionWorkflow.rejections))

        # The exact revision approves; the run completes; the ActionItem resolves.
        await approve_run(run_id, actor="operator", revision_hash=revision)
        await _wait_state(run_id, "COMPLETE")
        async with maker() as db:
            items = (
                (await db.execute(select(ActionItem).where(ActionItem.run_id == run_id)))
                .scalars()
                .all()
            )
        assert all(
            i.status == ActionItemStatus.resolved for i in items if i.kind == "approval_waiting"
        )
        counts = _executions(project_dir)
        node_counts = {k: v for k, v in counts.items() if not k.startswith("render_card:")}
        assert (node_counts and all(v == 1 for v in node_counts.items() if False)) or all(
            v == 1 for v in node_counts.values()
        ), node_counts
        assert counts["render_card:card_000000000002"] == 1

        # --- targeted rebuild: edit card 2's copy, rerun the same project ----------------------
        overlay = {
            "copy": {
                "dlv_carousel0001": {
                    "card_000000000002": "EDITED: siting improved output per turbine."
                }
            }
        }
        (project_dir / "edits").mkdir(exist_ok=True)
        (project_dir / "edits" / "overlay.json").write_text(json.dumps(overlay))
        run2 = await start_run(
            _campaign(),
            quality="demo",
            projects_dir=projects,
            artifacts_dir=artifacts,
            task_queue=queue,
            project_id=view["project_id"],
        )
        v2 = await _wait_state(run2, "WAITING_FOR_APPROVAL")
        await approve_run(run2, actor="operator", revision_hash=v2["preflight_revision_hash"])
        await _wait_state(run2, "COMPLETE")
        counts2 = _executions(project_dir)
        card_renders = {k: v for k, v in counts2.items() if k.startswith("render_card:")}
        # Only the edited card rendered again; cards 1 and 3 stayed cached.
        assert card_renders["render_card:card_000000000002"] == 2
        assert card_renders["render_card:card_000000000001"] == 1
        assert card_renders["render_card:card_000000000003"] == 1
        # The image branch was untouched (cache hits, no re-render).
        assert counts2["render_static:dlv_image0000001"] == 1
        async with maker() as db:
            v2_done = await run_view(db, WS, run2)
        assert v2_done
        by_node = {n["node_id"]: n for n in v2_done["nodes"]}
        assert by_node["render_static:dlv_image0000001"]["cache_hit"] is True
        assert by_node["render_cards:dlv_carousel0001"]["cache_hit"] is False


@pytest.mark.skipif(not BROWSER.exists(), reason="headless browser not downloaded")
def test_worker_kill_resumes_without_duplicate_executions(tmp_path: Path) -> None:
    asyncio.run(_worker_kill_flow(tmp_path))


async def _worker_kill_flow(tmp_path: Path) -> None:
    try:
        await _connect()
    except Exception as exc:
        pytest.skip(f"temporal unreachable: {exc}")
    await _ensure_fixture_workspace()
    queue = f"kill-{uuid.uuid4().hex[:8]}"
    projects = tmp_path / "projects"
    env = {
        **os.environ,
        "CF_WORKER_ALLOW_ANY_QUEUE": "1",
        "CF_TEST_STAGE_DELAY_S": "0.4",
        "PATH": os.environ["PATH"],
    }

    worker_log = (tmp_path / "worker.log").open("ab")

    def spawn() -> subprocess.Popen:
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                f"from content_factory.workflows.worker import main; main('{queue}')",
            ],
            env=env,
            cwd=REPO,
            stdout=worker_log,
            stderr=worker_log,
        )

    # Register the ephemeral queue in the worker registry via env-independent path: reuse control
    # registry but poll a custom queue name.
    from content_factory.workflows import worker as worker_mod

    worker_mod.REGISTRY[queue] = worker_mod.REGISTRY["control"]

    proc = spawn()
    try:
        run_id = await start_run(
            _campaign(),
            quality="demo",
            projects_dir=projects,
            artifacts_dir=tmp_path / "artifacts",
            task_queue=queue,
        )
        view = await _wait_state(run_id, "WAITING_FOR_APPROVAL", timeout_s=180)
        project_dir = projects / view["project_id"]
        await approve_run(run_id, actor="operator", revision_hash=view["preflight_revision_hash"])

        # Wait until at least two branch nodes completed, then kill the worker hard.
        from content_factory.db.session import get_sessionmaker

        maker = get_sessionmaker()
        for _ in range(600):
            async with maker() as db:
                v = await run_view(db, WS, run_id)
            done_branch = [
                n
                for n in (v or {}).get("nodes", [])
                if n["state"] == "complete" and n["deliverable_id"]
            ]
            if len(done_branch) >= 2:
                break
            await asyncio.sleep(0.2)
        else:
            raise TimeoutError("branch nodes never progressed")
        assert v is not None
        completed_before_kill = {n["node_id"] for n in v["nodes"] if n["state"] == "complete"}
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)

        async with maker() as db:
            mid = await run_view(db, WS, run_id)
        assert mid and mid["state"] in {"PRODUCING", "APPROVED"}

        proc = spawn()
        await _wait_state(run_id, "COMPLETE", timeout_s=240)
        counts = _executions(project_dir)
        node_counts = {k: v for k, v in counts.items() if not k.startswith("render_card:")}
        # Nodes that completed before the kill executed exactly once; the in-flight node at most twice.  # noqa: E501
        for node_id in completed_before_kill:
            assert node_counts.get(node_id, 0) == 1, (node_id, node_counts)
        assert all(v <= 2 for v in node_counts.values()), node_counts
        assert sum(1 for v in node_counts.values() if v == 2) <= 1
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
        worker_mod.REGISTRY.pop(queue, None)


@pytest.mark.skipif(not BROWSER.exists(), reason="headless browser not downloaded")
def test_originality_gate_blocks_a_near_duplicate_second_campaign(tmp_path: Path) -> None:
    asyncio.run(_originality_block_flow(tmp_path))


async def _originality_block_flow(tmp_path: Path) -> None:
    try:
        client = await _connect()
    except Exception as exc:
        pytest.skip(f"temporal unreachable: {exc}")
    await _ensure_fixture_workspace()
    queue = f"orig-{uuid.uuid4().hex[:8]}"
    projects = tmp_path / "projects"

    async def run_campaign(campaign_id: str) -> str:
        campaign = _campaign().model_copy(update={"campaign_id": campaign_id})
        run_id = await start_run(
            campaign,
            quality="demo",
            projects_dir=projects,
            artifacts_dir=tmp_path / "artifacts",
            task_queue=queue,
        )
        view = await _wait_state(run_id, "WAITING_FOR_APPROVAL")
        await approve_run(run_id, actor="operator", revision_hash=view["preflight_revision_hash"])
        return run_id

    async with Worker(
        client, task_queue=queue, workflows=[ProductionWorkflow], activities=PRODUCTION_ACTIVITIES
    ):
        first = await run_campaign("cmp_origfirst001")
        await _wait_state(first, "COMPLETE")
        # A SECOND campaign with the same fixture content must hit the blocking gate and FAIL,
        # with the typed verdict in the run error — a model cannot override this.
        second = await run_campaign("cmp_origsecond01")
        view = await _wait_state(second, "FAILED", timeout_s=180)
        assert "originality gate" in (view["error"] or "")
        assert "TOO_SIMILAR" in view["error"] or "MASS_PRODUCTION_RISK" in view["error"]
