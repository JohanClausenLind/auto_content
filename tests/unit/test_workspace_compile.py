"""Workspace-graph → DAG compiler: dispositions, honesty about unexecutable stages, determinism."""

from __future__ import annotations

import pytest

from content_factory.schemas.fixtures import sample_campaign
from content_factory.schemas.workspace_graph import (
    WorkspaceGraph,
    WorkspaceGroup,
    WorkspaceLink,
    WorkspaceNode,
)
from content_factory.workspace import compile_graph


def node(node_id: str, node_type: str, **kw) -> WorkspaceNode:
    return WorkspaceNode(id=node_id, type=node_type, x=0, y=0, **kw)


def link(link_id: str, a: str, b: str, out: str = "o", inp: str = "i") -> WorkspaceLink:
    return WorkspaceLink(id=link_id, from_node=a, from_slot=out, to_node=b, to_slot=inp)


def graph(*nodes: WorkspaceNode, links: tuple[WorkspaceLink, ...] = ()) -> WorkspaceGraph:
    return WorkspaceGraph(graph_id="g1", name="Test graph", nodes=nodes, links=links)


TEMPLATE = sample_campaign()


def test_contract_refuses_cycles_and_double_inputs() -> None:
    with pytest.raises(ValueError, match="cycle"):
        graph(
            node("a", "plan_story"),
            node("b", "write_copy"),
            links=(link("l1", "a", "b"), link("l2", "b", "a")),
        )
    with pytest.raises(ValueError, match="connected twice"):
        graph(
            node("a", "plan_story"),
            node("b", "plan_story"),
            node("c", "write_copy"),
            links=(link("l1", "a", "c"), link("l2", "b", "c")),
        )


def test_video_graph_compiles_to_dag_with_defaults() -> None:
    g = graph(
        node("brief", "input.brief", values={"topic": "dragons", "quality": "demo"}),
        node("story", "plan_story"),
        node("tl", "compile_timeline"),
        node("rs", "render_scenes"),
        node("cv", "compose_video"),
        node("note", "utility.note", note="hello"),
        node("mute", "write_copy", mode="muted"),
        node("post", "publish.social"),
        links=(
            link("l1", "brief", "story"),
            link("l2", "story", "tl"),
            link("l3", "tl", "rs"),
            link("l4", "rs", "cv"),
            link("l5", "cv", "post"),
        ),
    )
    c = compile_graph(g, TEMPLATE)
    assert c.ok and c.dag is not None and c.campaign is not None
    assert c.deliverable_type == "short_video"
    by_kind = {d.node_id: d for d in c.dispositions}
    assert by_kind["brief"].kind == "skipped" and "campaign brief" in by_kind["brief"].reason
    assert by_kind["note"].kind == "skipped"
    assert by_kind["mute"].kind == "skipped" and "muted" in by_kind["mute"].reason
    assert by_kind["post"].kind == "skipped" and "distribution" in by_kind["post"].reason
    # shared stage keeps its bare id; per-deliverable stages carry the synthetic deliverable
    ids = {n.node_id: n for n in c.dag.nodes}
    assert "plan_story" in ids and ids["plan_story"].deliverable_id is None
    assert ids["compose_video:cv"].deliverable_id is not None
    assert ids["compose_video:cv"].depends_on == ("render_scenes:rs",)
    # campaign carries the brief topic
    assert c.campaign.brief.topic == "dragons"
    # deterministic: same graph compiles to the same campaign/DAG ids
    again = compile_graph(g, TEMPLATE)
    assert again.dag is not None and again.campaign is not None
    assert again.campaign.campaign_id == c.campaign.campaign_id
    assert [n.node_id for n in again.dag.nodes] == [n.node_id for n in c.dag.nodes]


def test_new_stages_have_defaults_and_compile() -> None:
    g = graph(
        node("brief", "input.brief", values={"topic": "storms"}),
        node("story", "plan_story"),
        node("anim", "render_animation"),
        node("gen", "generate_video"),
        node("music", "select_music"),
        links=(link("l1", "brief", "story"), link("l2", "story", "gen")),
    )
    c = compile_graph(g, TEMPLATE)
    assert c.ok, c.problems
    stages = {n.stage.value for n in c.dag.nodes}  # type: ignore[union-attr]
    assert {"render_animation", "generate_video", "select_music"} <= stages
    gen = next(n for n in c.dag.nodes if n.stage.value == "generate_video")  # type: ignore[union-attr]
    assert gen.resource_class == "inference-video" and gen.executor.value == "ai"


def test_preflight_is_injected_when_missing() -> None:
    g = graph(
        node("brief", "input.brief", values={"topic": "x"}),
        node("s", "plan_story"),
    )
    c = compile_graph(g, TEMPLATE)
    assert c.ok
    preflight = next(n for n in c.dag.nodes if n.stage.value == "preflight")  # type: ignore[union-attr]
    assert preflight.deliverable_id is None
    assert "plan_story" in preflight.depends_on
    assert any(d.node_id == "__preflight__" and "approval" in d.reason for d in c.dispositions)
    # a graph that already has one gets no duplicate
    g2 = graph(
        node("brief", "input.brief", values={"topic": "x"}),
        node("p", "preflight"),
    )
    c2 = compile_graph(g2, TEMPLATE)
    assert sum(1 for n in c2.dag.nodes if n.stage.value == "preflight") == 1  # type: ignore[union-attr]


def test_unexecutable_stage_blocks_with_reason() -> None:
    g = graph(
        node("brief", "input.brief", values={"topic": "x"}),
        # The article branch is what still has no executors; `ingest` had one added when the
        # research lane was finished, so it can no longer stand in for "unrunnable".
        node("a", "draft_article"),
    )
    c = compile_graph(g, TEMPLATE)
    assert not c.ok
    assert any("no executor" in problem for problem in c.problems)
    assert next(d for d in c.dispositions if d.node_id == "a").kind == "blocks"


def test_missing_or_empty_brief_blocks() -> None:
    c = compile_graph(graph(node("s", "plan_story")), TEMPLATE)
    assert not c.ok and any("Campaign Brief" in p for p in c.problems)
    c2 = compile_graph(
        graph(node("b", "input.brief", values={"topic": "  "}), node("s", "plan_story")), TEMPLATE
    )
    assert not c2.ok and any("topic is empty" in p for p in c2.problems)


def test_unknown_node_type_blocks() -> None:
    g = graph(node("b", "input.brief", values={"topic": "x"}), node("w", "comfy.mystery"))
    c = compile_graph(g, TEMPLATE)
    assert not c.ok and any("unknown node type" in p for p in c.problems)


def test_run_view_edges_come_from_the_compiled_dag(tmp_path) -> None:
    import json

    from content_factory.services.runs import dag_edges

    assert dag_edges(tmp_path) is None  # no dag.json -> legacy chain heuristic applies
    (tmp_path / "dag.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {"node_id": "plan_story", "depends_on": []},
                    {"node_id": "generate_video:g", "depends_on": ["plan_story"]},
                    {"node_id": "render_animation:a", "depends_on": ["plan_story"]},
                ]
            }
        )
    )
    assert dag_edges(tmp_path) == [
        {"source": "plan_story", "target": "generate_video:g"},
        {"source": "plan_story", "target": "render_animation:a"},
    ]
    (tmp_path / "dag.json").write_text("not json")
    assert dag_edges(tmp_path) is None  # corrupt file degrades to the heuristic, never a 500


def test_shot_planning_graph_compiles_with_render_cpu_controls() -> None:
    g = graph(
        node("brief", "input.brief", values={"topic": "storms"}),
        node("story", "plan_story"),
        node("shots", "plan_shots"),
        node("controls", "compile_controls"),
        links=(
            link("l1", "brief", "story"),
            link("l2", "story", "shots"),
            link("l3", "shots", "controls"),
        ),
    )
    c = compile_graph(g, TEMPLATE)
    assert c.ok, c.problems
    by_stage = {n.stage.value: n for n in c.dag.nodes}  # type: ignore[union-attr]
    assert by_stage["compile_controls"].resource_class == "render-cpu"
    assert by_stage["plan_shots"].resource_class == "control"
    assert all(d.kind == "executes" for d in c.dispositions if d.node_id in {"shots", "controls"})


# --- dropped files ----------------------------------------------------------------------------
#
# A source node carries an artifact key, and everything the run needs to know about the file is
# read out of that key rather than taken on trust from the graph document.

WS = TEMPLATE.workspace_id
KEY = f"{WS}/originals-audio/ab/" + "a" * 64 + ".wav"


def test_a_dropped_file_becomes_a_staged_upload_on_the_campaign() -> None:
    g = graph(
        node("brief", "input.brief", values={"topic": "a repaired recording", "quality": "demo"}),
        node(
            "clip", "input.audio", values={"asset": KEY, "filename": "take one.wav", "bytes": 4096}
        ),
        node("mix", "mix_audio"),
        links=(link("l1", "clip", "mix", out="audio", inp="audio"),),
    )
    result = compile_graph(g, TEMPLATE)

    assert result.ok, result.problems
    assert result.campaign is not None
    staged = result.campaign.staged_uploads
    assert len(staged) == 1
    assert staged[0].asset_id == KEY
    assert staged[0].filename == "take one.wav"
    assert staged[0].kind == "audio"  # from the key, not from the node type
    assert staged[0].sha256 == "a" * 64
    # The node itself is not a stage: the file is staged, the run's ingest stage reads it.
    disposition = next(d for d in result.dispositions if d.node_id == "clip")
    assert disposition.kind == "skipped"
    assert "uploads folder" in disposition.reason


def test_the_same_file_dropped_twice_is_staged_once() -> None:
    g = graph(
        node("brief", "input.brief", values={"topic": "two of the same", "quality": "demo"}),
        node("a", "input.audio", values={"asset": KEY, "filename": "take.wav", "bytes": 10}),
        node("b", "input.audio", values={"asset": KEY, "filename": "take.wav", "bytes": 10}),
        node("mix", "mix_audio"),
        links=(link("l1", "a", "mix", out="audio", inp="audio"),),
    )
    result = compile_graph(g, TEMPLATE)
    assert result.ok, result.problems
    assert result.campaign is not None
    assert len(result.campaign.staged_uploads) == 1


def test_a_reference_from_another_workspace_blocks_the_run() -> None:
    foreign = KEY.replace(WS, "ws_someoneelse01")
    g = graph(
        node("brief", "input.brief", values={"topic": "not mine", "quality": "demo"}),
        node("clip", "input.audio", values={"asset": foreign, "filename": "take.wav", "bytes": 10}),
        node("mix", "mix_audio"),
        links=(link("l1", "clip", "mix", out="audio", inp="audio"),),
    )
    result = compile_graph(g, TEMPLATE)
    assert not result.ok
    assert any("does not belong to this workspace" in p for p in result.problems)


def test_a_node_type_that_disagrees_with_the_key_blocks_the_run() -> None:
    """An `input.image` holding an audio key would stage a WAV as a still."""
    g = graph(
        node("brief", "input.brief", values={"topic": "mismatch", "quality": "demo"}),
        node("pic", "input.image", values={"asset": KEY, "filename": "take.wav", "bytes": 10}),
        node("story", "plan_story"),
        links=(),
    )
    result = compile_graph(g, TEMPLATE)
    assert not result.ok
    assert any("is a audio file, not image" in p for p in result.problems)


def test_an_empty_source_node_blocks_with_something_to_do() -> None:
    g = graph(
        node("brief", "input.brief", values={"topic": "nothing dropped", "quality": "demo"}),
        node("clip", "input.audio"),
        node("mix", "mix_audio"),
        links=(link("l1", "clip", "mix", out="audio", inp="audio"),),
    )
    result = compile_graph(g, TEMPLATE)
    assert not result.ok
    assert any("no file dropped on it yet" in p for p in result.problems)


def test_the_deliverables_terminal_is_a_marker_not_a_stage() -> None:
    g = graph(
        node("brief", "input.brief", values={"topic": "a film", "quality": "demo"}),
        node("story", "plan_story"),
        node("tl", "compile_timeline"),
        node("rs", "render_scenes"),
        node("cv", "compose_video"),
        node("out", "output.deliverables"),
        links=(link("l1", "cv", "out", out="video", inp="files"),),
    )
    result = compile_graph(g, TEMPLATE)
    assert result.ok, result.problems
    disposition = next(d for d in result.dispositions if d.node_id == "out")
    assert disposition.kind == "skipped"
    assert "already writes" in disposition.reason
    assert result.dag is not None
    assert all(n.stage.value != "output.deliverables" for n in result.dag.topological())


def test_an_inspector_with_nothing_wired_into_it_is_refused() -> None:
    """The screenshot case: QC Deliverable and Destination Packages joined only to each other.

    Dependencies come only from links, so an inspector with nothing upstream is not "last" — it
    is unordered, and would read an empty deliverable folder. The canvas calls that an error; so
    does the compiler, which is where the CLI and the MCP tool arrive.
    """
    g = graph(
        node("brief", "input.brief", values={"topic": "a film", "quality": "demo"}),
        node("story", "plan_story"),
        node("tl", "compile_timeline"),
        node("rs", "render_scenes"),
        node("cv", "compose_video"),
        node("qc", "qc_deliverable"),
        node("pack", "compile_destination_packages"),
        links=(link("l1", "qc", "pack", out="report", inp="qc"),),
    )
    result = compile_graph(g, TEMPLATE)

    assert not result.ok
    refusals = [p for p in result.problems if "nothing is wired into it" in p]
    # Exactly one: the packager is ordered *through* the QC node, so its own ordering is fine —
    # it is the unordered QC node that makes the graph wrong, and naming both would be noise.
    assert len(refusals) == 1, result.problems
    assert "qc_deliverable" in refusals[0] or "QC Deliverable" in refusals[0]
    assert "the cut, the frames, the image or the master" in refusals[0]


def test_a_packager_ordered_only_through_qc_is_accepted() -> None:
    """Ordering is what the wire is for, and it is transitive: a packager whose only link comes
    from a QC node that depends on the render still runs after the render. Its own deliverable
    input is a canvas-level requirement, not an ordering one — the stage reads the run folder."""
    g = graph(
        node("brief", "input.brief", values={"topic": "a film", "quality": "demo"}),
        node("story", "plan_story"),
        node("tl", "compile_timeline"),
        node("rs", "render_scenes"),
        node("cv", "compose_video"),
        node("qc", "qc_deliverable"),
        node("pack", "compile_destination_packages"),
        links=(
            link("l1", "cv", "qc", out="video", inp="deliverable"),
            link("l2", "qc", "pack", out="report", inp="qc"),
        ),
    )
    result = compile_graph(g, TEMPLATE)
    assert result.ok, result.problems


def test_the_same_lane_compiles_once_the_deliverable_is_wired() -> None:
    g = graph(
        node("brief", "input.brief", values={"topic": "a film", "quality": "demo"}),
        node("story", "plan_story"),
        node("tl", "compile_timeline"),
        node("rs", "render_scenes"),
        node("cv", "compose_video"),
        node("qc", "qc_deliverable"),
        node("pack", "compile_destination_packages"),
        links=(
            link("l1", "cv", "qc", out="video", inp="deliverable"),
            link("l2", "cv", "pack", out="video", inp="deliverable"),
            link("l3", "qc", "pack", out="report", inp="qc"),
        ),
    )
    result = compile_graph(g, TEMPLATE)
    assert result.ok, result.problems
    assert result.dag is not None
    by_id = {n.node_id: n for n in result.dag.topological()}
    qc_node = next(n for n in by_id.values() if n.stage.value == "qc_deliverable")
    # The wire is the ordering: QC now depends on the stage that made the cut.
    assert any("compose_video" in dep for dep in qc_node.depends_on)


# --- folded groups are a view, and the compiler must not be able to tell ------------------------


def _video_graph(groups: tuple[WorkspaceGroup, ...] = ()) -> WorkspaceGraph:
    return WorkspaceGraph(
        graph_id="g1",
        name="Test graph",
        nodes=(
            node("brief", "input.brief", values={"topic": "dragons", "quality": "demo"}),
            node("story", "plan_story"),
            node("tl", "compile_timeline"),
            node("rs", "render_scenes"),
            node("cv", "compose_video"),
            node("qc", "qc_deliverable"),
        ),
        links=(
            link("l1", "story", "tl"),
            link("l2", "tl", "rs"),
            link("l3", "rs", "cv"),
            link("l4", "cv", "qc"),
        ),
        groups=groups,
    )


def test_folding_a_graph_compiles_to_exactly_the_same_run() -> None:
    """The property the whole feature rests on. A group is how the canvas *draws* a lane; if it
    could change what runs, every folded lane would be a different film from the one an operator
    read, and the compiler would need to learn a second graph format."""
    flat = compile_graph(_video_graph(), TEMPLATE)
    folded = compile_graph(
        _video_graph(
            (
                WorkspaceGroup(
                    id="gr1", name="Render and check", members=("rs", "cv", "qc"), x=0, y=0
                ),
            )
        ),
        TEMPLATE,
    )
    assert flat.ok and folded.ok
    assert folded.dag is not None and flat.dag is not None
    assert folded.dag.model_dump_json() == flat.dag.model_dump_json()
    assert folded.campaign is not None and flat.campaign is not None
    assert folded.campaign.model_dump_json() == flat.campaign.model_dump_json()
    assert [d.kind for d in folded.dispositions] == [d.kind for d in flat.dispositions]


def test_the_publish_node_says_where_it_would_have_gone() -> None:
    """A compile preview is where an operator checks what a Run does. "Publishing is skipped" is
    half the answer; the other half is which destinations were lit up on the node."""
    g = graph(
        node("brief", "input.brief", values={"topic": "dragons", "quality": "demo"}),
        node("story", "plan_story"),
        node("tl", "compile_timeline"),
        node("rs", "render_scenes"),
        node("cv", "compose_video"),
        node("pub", "publish.social", values={"destinations": "bluesky,mastodon"}),
        links=(link("l1", "story", "tl"), link("l2", "tl", "rs"), link("l3", "rs", "cv")),
    )
    out = compile_graph(g, TEMPLATE)
    reason = next(d.reason for d in out.dispositions if d.node_id == "pub")
    assert "bluesky, mastodon" in reason
    empty = compile_graph(
        graph(
            node("brief", "input.brief", values={"topic": "dragons", "quality": "demo"}),
            node("story", "plan_story"),
            node("tl", "compile_timeline"),
            node("rs", "render_scenes"),
            node("cv", "compose_video"),
            node("pub", "publish.social", values={"destinations": ""}),
            links=(link("l1", "story", "tl"), link("l2", "tl", "rs"), link("l3", "rs", "cv")),
        ),
        TEMPLATE,
    )
    assert "no destination selected" in next(
        d.reason for d in empty.dispositions if d.node_id == "pub"
    )


def test_a_group_naming_a_node_that_is_not_there_is_refused_by_the_contract() -> None:
    with pytest.raises(ValueError, match="unknown node"):
        _video_graph((WorkspaceGroup(id="gr1", name="Ghost", members=("nope",), x=0, y=0),))


def test_one_node_cannot_be_in_two_groups() -> None:
    with pytest.raises(ValueError, match="in two groups"):
        _video_graph(
            (
                WorkspaceGroup(id="gr1", name="A", members=("rs",), x=0, y=0),
                WorkspaceGroup(id="gr2", name="B", members=("rs",), x=0, y=0),
            )
        )
