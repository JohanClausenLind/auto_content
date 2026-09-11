"""Workspace-graph runs execute end-to-end on the real engine (compose postgres + temporal):
compile a hand-drawn graph, start the durable workflow with the precompiled DAG, park at the
approval gate, approve, and watch the new stages (select_music, generate_video,
render_animation) produce verified artifacts exactly once."""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import uuid
from collections import Counter
from pathlib import Path

import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from content_factory.schemas.fixtures import WS, sample_campaign
from content_factory.schemas.workspace_graph import WorkspaceGraph, WorkspaceLink, WorkspaceNode
from content_factory.services.runs import approve_run, run_view, start_run
from content_factory.workflows.production import PRODUCTION_ACTIVITIES, ProductionWorkflow
from content_factory.workspace import compile_graph

pytestmark = pytest.mark.integration


def _node(node_id: str, node_type: str, **kw) -> WorkspaceNode:
    return WorkspaceNode(id=node_id, type=node_type, x=0, y=0, **kw)


def _graph() -> WorkspaceGraph:
    nodes = (
        _node("brief", "input.brief", values={"topic": "An islander outruns a storm."}),
        _node("research", "research"),
        _node("story", "plan_story"),
        _node("copy", "write_copy"),
        _node("lock", "lock_script"),
        _node("voice", "synthesize_narration"),
        _node("align", "align_words"),
        _node("caps", "compile_captions"),
        _node("music", "select_music"),
        _node("mix", "mix_audio"),
        _node("anim", "render_animation"),
        _node("gen", "generate_video"),
    )
    chain = ["brief", "research", "story", "copy", "lock", "voice", "align", "caps", "music", "mix"]
    links = [
        WorkspaceLink(id=f"l{i}", from_node=a, from_slot="o", to_node=b, to_slot="i")
        for i, (a, b) in enumerate(itertools.pairwise(chain))
    ]
    links.append(
        WorkspaceLink(id="lg", from_node="story", from_slot="o2", to_node="gen", to_slot="i")
    )
    links.append(
        WorkspaceLink(id="la", from_node="story", from_slot="o3", to_node="anim", to_slot="i")
    )
    return WorkspaceGraph(
        graph_id="graph_e2e_test_1", name="Graph e2e", nodes=nodes, links=tuple(links)
    )


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
        if view and view["state"] in {"FAILED", "CANCELLED"}:
            failed = [
                (n.get("node_id"), n.get("state"), n.get("error"))
                for n in view.get("nodes", [])
                if n.get("state") != "complete"
            ]
            raise AssertionError(
                f"run reached {view['state']}: {view.get('error')}; nodes: {failed}"
            )
        await asyncio.sleep(0.25)
    raise TimeoutError(f"run {run_id} never reached {state}; last: {view}")


def test_workspace_graph_runs_end_to_end(tmp_path: Path, monkeypatch) -> None:
    # Pin the fixture music library. The configured default is `assets/music`, which is
    # host-specific git-ignored media; asserting a track id from it would make this test pass on
    # this machine and fail on a fresh checkout.
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("CF__MEDIA_LIBRARY__MUSIC_DIR", str(repo_root / "fixtures" / "music"))
    from content_factory.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    previous = os.environ.get("CF_PROJECTS_DIR")
    try:
        asyncio.run(_flow(tmp_path))
    finally:
        if previous is None:
            os.environ.pop("CF_PROJECTS_DIR", None)
        else:
            os.environ["CF_PROJECTS_DIR"] = previous
        get_settings.cache_clear()  # type: ignore[attr-defined]


async def _flow(tmp_path: Path) -> None:
    try:
        client = await _connect()
    except Exception as exc:
        pytest.skip(f"temporal unreachable: {exc}")

    from tests.integration.test_production_workflow import _ensure_fixture_workspace

    await _ensure_fixture_workspace()

    template = sample_campaign()
    compilation = compile_graph(_graph(), template)
    assert compilation.ok, compilation.problems
    assert compilation.dag is not None and compilation.campaign is not None
    assert {n.stage.value for n in compilation.dag.nodes} >= {
        "select_music",
        "generate_video",
        "render_animation",
    }

    queue = f"test-{uuid.uuid4().hex[:8]}"
    # run_view resolves the project dir via projects_root(); point it at this test's tree the
    # same way the API and worker share it in production.
    os.environ["CF_PROJECTS_DIR"] = str(tmp_path / "projects")
    async with Worker(
        client,
        task_queue=queue,
        workflows=[ProductionWorkflow],
        activities=PRODUCTION_ACTIVITIES,
    ):
        run_id = await start_run(
            compilation.campaign,
            quality="smoke",
            projects_dir=tmp_path / "projects",
            artifacts_dir=tmp_path / "artifacts",
            task_queue=queue,
            dag_json=compilation.dag.model_dump_json(),
        )
        waiting = await _wait_state(run_id, "WAITING_FOR_APPROVAL")
        await approve_run(
            run_id,
            actor="e2e",
            revision_hash=waiting["preflight_revision_hash"],
            decision="approve",
        )
        done = await _wait_state(run_id, "COMPLETE")
        assert done["edges"], "graph runs expose their real dependency edges"
        assert {"source": "plan_story", "target": "generate_video:gen"} in done["edges"]

    project_dir = tmp_path / "projects" / done["project_id"]
    executions = Counter(
        line.split("\t")[0]
        for line in (project_dir / ".stages" / "executions.log").read_text().splitlines()
        if line
    )
    assert all(count == 1 for count in executions.values()), executions

    deliverable_id = compilation.campaign.deliverables[0].deliverable_id
    ddir = project_dir / "deliverables" / deliverable_id
    selection = json.loads((ddir / "audio" / "music-selection.json").read_text())
    assert selection["track_id"] == "calm_bed_a"  # the pinned fixture library, not assets/music
    assert (ddir / "audio" / "narration-with-music.wav").exists()
    assert (ddir / "audio" / "narration-mastered.wav").exists()
    assert (ddir / "exports" / "generated.mp4").stat().st_size > 0
    assert (ddir / "animation" / "preview.mp4").exists()
    assert len(list((ddir / "animation" / "frames").glob("*.png"))) > 0
