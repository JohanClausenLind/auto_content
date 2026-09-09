"""The workflow catalogue: every definition holds together, and the generated files match it.

These tests exist because a workflow used to be defined in two hand-written places that drifted.
The point of the refactor is that there is now one file per workflow and everything else is
generated from it, so what has to be guarded is the generation and the honesty of each file, not
the agreement between two copies.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.schemas.dag import Stage
from content_factory.schemas.workflow_template import (
    NON_RUNNABLE_TYPES,
    NON_STAGE_TYPES,
    WorkflowNode,
    WorkflowTemplate,
    WorkflowWire,
)
from content_factory.workflows.catalog import (
    DEFINITIONS_DIR,
    WorkflowDefinitionError,
    load_definition,
    load_definition_file,
    load_definitions,
    node_catalog,
    runnable_missing_executor,
    to_workspace_graph,
)
from content_factory.workflows.stages import STAGE_EXECUTORS

DEFINITION_FILES = sorted(DEFINITIONS_DIR.glob("*.yaml"))
IDS = [p.stem for p in DEFINITION_FILES]


def test_there_are_definitions_at_all() -> None:
    assert len(DEFINITION_FILES) >= 10, "the catalogue should cover the kinds of content we make"


@pytest.mark.parametrize("path", DEFINITION_FILES, ids=IDS)
def test_every_definition_validates(path: Path) -> None:
    template = load_definition_file(path)
    assert template.id == path.stem


@pytest.mark.parametrize("path", DEFINITION_FILES, ids=IDS)
def test_every_stage_has_an_executor_or_the_file_says_why_not(path: Path) -> None:
    template = load_definition_file(path)
    blocked = runnable_missing_executor(template)
    if blocked:
        assert template.caveat, (
            f"{path.name} uses {list(blocked)} with no executor and declares no caveat;"
            " the catalogue may describe an unfinished lane but not pretend one works"
        )
        for stage in blocked:
            assert stage in template.caveat, f"{path.name}: caveat should name {stage}"


@pytest.mark.parametrize("path", DEFINITION_FILES, ids=IDS)
def test_every_definition_compiles_to_a_canvas_graph(path: Path) -> None:
    """`to_workspace_graph` returns the contract now, so constructing it *is* the assertion.

    `WorkspaceGraph`'s validators refuse a duplicate node id, a link to a node that is not in the
    graph, two links into one input slot, and a node linked to itself — every one of which the old
    untyped dict could emit and the canvas would then refuse to load. What is left to assert here
    is the thing the contract cannot know: that this graph is *this* definition.
    """
    template = load_definition_file(path)
    graph = to_workspace_graph(
        template
    )  # raises ValidationError on a graph the canvas would reject
    assert graph.graph_id == template.id
    assert len(graph.nodes) == len(template.nodes)
    assert len(graph.links) == len(template.wires)
    # Positions are definite, so the canvas never has to invent one.
    assert all(isinstance(n.x, float) and isinstance(n.y, float) for n in graph.nodes)
    # And the ids are derived from the node keys, so the same definition always yields the same
    # graph — a test can compare bytes, and a saved graph survives a redeploy.
    assert to_workspace_graph(template).model_dump_json() == graph.model_dump_json()


@pytest.mark.parametrize("path", DEFINITION_FILES, ids=IDS)
def test_every_required_input_is_wired(path: Path) -> None:
    """No lane opens on a required input that nothing feeds — and a caveat can no longer excuse
    one.

    Four lanes used to. Each of them worked on material the operator supplies, and there was no
    node type for that material to arrive through, so each declared its first stage's input
    unwired and apologised in prose. ``input.audio`` / ``input.image`` / ``input.video`` are lane
    node types now, so the apology has been replaced by a wire and the loader refuses the rest.
    Asserted here as well as in the loader so a failure names the file.
    """
    template = load_definition_file(path)
    catalog = node_catalog()
    fed = {(w.to_key, w.to_slot) for w in template.wires}
    unfed = [
        f"{node.key}.{slot}"
        for node in template.nodes
        if node.runnable and (spec := catalog.get(node.type)) is not None
        for slot in spec["required_inputs"]
        if (node.key, slot) not in fed
    ]
    assert unfed == [], f"{path.name}: unwired required inputs {unfed}"


@pytest.mark.parametrize("path", DEFINITION_FILES, ids=IDS)
def test_every_output_is_consumed_by_something(path: Path) -> None:
    """A node whose output nothing reads is the canvas's own warning, and in a committed lane it
    means one of two things: a step whose result is thrown away, or a terminal nobody wired. Both
    are wiring mistakes, and both were in the catalogue until this test existed."""
    template = load_definition_file(path)
    catalog = node_catalog()
    used = {w.from_key for w in template.wires}
    dangling = [
        node.key
        for node in template.nodes
        if (spec := catalog.get(node.type)) is not None and spec["outputs"] and node.key not in used
    ]
    assert dangling == [], f"{path.name}: nothing consumes the output of {dangling}"


@pytest.mark.parametrize("path", DEFINITION_FILES, ids=IDS)
def test_a_lane_that_needs_material_declares_a_node_for_it(path: Path) -> None:
    """The prerequisite and the graph have to agree, in both directions.

    There are exactly two ways an operator's material reaches a run, and a lane has to use one of
    them and say which: a file input node, staged by ``--input`` or by a drop on the canvas; or
    ``voice_over`` in ``takes`` mode, which reads one recording per beat out of a directory. A
    lane whose prose promises material and whose graph has neither is the shape that made four
    lanes unrunnable.
    """
    from content_factory.runners.local import workflow_inputs

    template = load_definition_file(path)
    kinds = workflow_inputs(template.id)
    prose = template.prerequisite.lower()
    if kinds:
        assert "--input" in prose, f"{path.name}: takes {kinds} and does not say how to pass it"
    if "--input" in prose:
        assert kinds, f"{path.name}: promises --input and declares no file input node"
    if "takes/" in prose:
        takes = [
            n
            for n in template.nodes
            if n.type == "voice_over" and str(n.values.get("source", "takes")) == "takes"
        ]
        assert takes, f"{path.name}: prerequisite names takes/ and no node reads a takes directory"


@pytest.mark.parametrize("path", DEFINITION_FILES, ids=IDS)
def test_folded_groups_hold_together_and_reach_the_canvas(path: Path) -> None:
    """Groups are how a lane is shown, so what has to hold is that they name real nodes, fold more
    than one, and survive the trip into the canvas document."""
    template = load_definition_file(path)
    keys = {n.key for n in template.nodes}
    for group in template.groups:
        assert set(group.members) <= keys, f"{path.name}: {group.key} names a node that is not here"
        assert len(group.members) > 1, f"{path.name}: {group.key} folds a single node"
    graph = to_workspace_graph(template)
    assert len(graph.groups) == len(template.groups)
    node_ids = {n.id for n in graph.nodes}
    for group in graph.groups:
        assert set(group.members) <= node_ids
        assert group.collapsed is True
    # Every lane that ends in the delivery pair folds it: that tail is identical in all of them,
    # and a catalogue where half the lanes fold it and half do not teaches nothing.
    if any(n.type == "compile_destination_packages" for n in template.nodes):
        assert template.groups, f"{path.name}: the delivery tail is not folded"


@pytest.mark.parametrize("path", DEFINITION_FILES, ids=IDS)
def test_names_are_general_not_about_one_subject(path: Path) -> None:
    """A workflow is named for what it does to the material. The lane that made a love story makes
    a fable with a different script, so 'love' does not belong in its name."""
    template = load_definition_file(path)
    banned = ("love", "romance", "wind", "documentary about", "my ")
    lowered = f"{template.id} {template.name}".lower()
    for word in banned:
        assert word not in lowered, f"{path.name}: {word!r} names a subject, not a workflow"


@pytest.mark.parametrize("path", DEFINITION_FILES, ids=IDS)
def test_values_are_declared_widgets_and_wires_typecheck(path: Path) -> None:
    """Covered by the loader, asserted here so a failure names the file."""
    load_definition_file(path)  # raises WorkflowDefinitionError with the file name in it


def test_ids_are_unique_and_files_are_named_after_them() -> None:
    definitions = load_definitions()
    assert sorted(definitions) == IDS


def test_no_two_workflows_run_the_same_stages_in_the_same_order() -> None:
    """The dedupe guard. Two lanes differing only by a widget value should be one lane with a
    parameter, which is what happened to the stills variant and the two single-image templates."""
    seen: dict[tuple[str, ...], str] = {}
    for wid, template in load_definitions().items():
        key = tuple(s.value for s in template.stages())
        if key in seen:
            pytest.fail(
                f"{wid} runs exactly the same stages in the same order as {seen[key]};"
                " make it one workflow with a parameter instead"
            )
        seen[key] = wid


def test_the_catalogue_covers_the_kinds_of_content_we_make() -> None:
    categories = {t.category for t in load_definitions().values()}
    assert {"image", "video", "audio", "utility"} <= categories
    stage_sets = [set(t.stages()) for t in load_definitions().values()]
    # A lane with a voice, a lane with no voice, a lane with no picture, and a finishing lane.
    assert any(Stage.voice_over in s or Stage.synthesize_narration in s for s in stage_sets)
    assert any(
        Stage.select_music in s
        and Stage.voice_over not in s
        and Stage.synthesize_narration not in s
        for s in stage_sets
    )
    assert any(Stage.compose_video not in s and Stage.mix_audio in s for s in stage_sets)
    assert any(Stage.upscale_video in s for s in stage_sets)


def test_generated_files_match_the_definitions() -> None:
    """`just schemas` regenerates these; this is the drift gate the git-ignored TS cannot be."""
    import subprocess
    import sys

    repo = DEFINITIONS_DIR.parent
    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / "export_workflows.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=repo,
    )
    assert result.returncode == 0, (
        "generated workflow data is stale; run `uv run python scripts/export_workflows.py`."
        f" {result.stdout}{result.stderr}"
    )


def test_the_tracked_json_describes_every_workflow() -> None:
    doc = json.loads(
        (DEFINITIONS_DIR.parent / "fixtures/schema/workflow_templates.json").read_text()
    )
    assert doc["schema"] == "cf.workflow_templates.v1"
    assert sorted(t["id"] for t in doc["templates"]) == IDS


def test_node_catalog_covers_every_stage() -> None:
    catalog = node_catalog()
    assert {s.value for s in Stage} <= set(catalog)
    assert NON_STAGE_TYPES <= set(catalog)


# --- the contract's own guards -----------------------------------------------------------------


def _template(**over) -> dict:
    base = {
        "id": "test-lane",
        "name": "Test lane",
        "description": "A lane used only by the tests.",
        "category": "utility",
        "nodes": [
            {"key": "a", "type": "plan_shots"},
            {"key": "b", "type": "route_shots"},
        ],
        "wires": [{"from_key": "a", "from_slot": "shots", "to_key": "b", "to_slot": "shots"}],
        "order": ["a", "b"],
    }
    base.update(over)
    return base


def test_order_must_be_a_topological_order_of_the_wires() -> None:
    with pytest.raises(ValueError, match="topological"):
        WorkflowTemplate.model_validate(_template(order=["b", "a"]))


def test_order_must_cover_every_runnable_node() -> None:
    with pytest.raises(ValueError, match="every runnable node"):
        WorkflowTemplate.model_validate(_template(order=["a"]))


def test_duplicate_node_keys_are_refused() -> None:
    nodes = [
        {"key": "a", "type": "plan_shots"},
        {"key": "a", "type": "route_shots"},
    ]
    with pytest.raises(ValueError, match="duplicate node keys"):
        WorkflowTemplate.model_validate(_template(nodes=nodes, wires=[], order=["a"]))


def test_an_unknown_node_type_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown type"):
        WorkflowNode.model_validate({"key": "a", "type": "not_a_stage"})


def test_a_wire_to_an_unknown_node_is_refused() -> None:
    wires = [{"from_key": "a", "from_slot": "shots", "to_key": "ghost", "to_slot": "shots"}]
    with pytest.raises(ValueError, match="unknown node"):
        WorkflowTemplate.model_validate(_template(wires=wires))


def test_a_node_wired_to_itself_is_refused() -> None:
    wires = [{"from_key": "a", "from_slot": "shots", "to_key": "a", "to_slot": "story"}]
    with pytest.raises(ValueError, match="wired to itself"):
        WorkflowTemplate.model_validate(_template(wires=wires))


def test_one_input_cannot_be_wired_twice() -> None:
    nodes = [
        {"key": "a", "type": "plan_shots"},
        {"key": "b", "type": "plan_shots"},
        {"key": "c", "type": "route_shots"},
    ]
    wires = [
        {"from_key": "a", "from_slot": "shots", "to_key": "c", "to_slot": "shots"},
        {"from_key": "b", "from_slot": "shots", "to_key": "c", "to_slot": "shots"},
    ]
    with pytest.raises(ValueError, match="wired twice"):
        WorkflowTemplate.model_validate(_template(nodes=nodes, wires=wires, order=["a", "b", "c"]))


def test_a_note_is_not_runnable_and_stays_out_of_the_order() -> None:
    nodes = [
        {"key": "a", "type": "plan_shots"},
        {"key": "b", "type": "route_shots"},
        {"key": "sticky", "type": "utility.note", "note": "a reminder"},
    ]
    template = WorkflowTemplate.model_validate(_template(nodes=nodes))
    assert "utility.note" in NON_RUNNABLE_TYPES
    assert [k for k, _s, _v in template.stage_order()] == ["a", "b"]


def test_a_value_outside_a_combo_widgets_options_is_refused(tmp_path: Path) -> None:
    """The check that caught six wrong values the moment it existed, including 'animate' where the
    widget says 'ltx'. A plausible wrong value is worse than a misspelled key: it validates and
    then silently takes the stage's fallback."""
    import yaml

    doc = _template(
        id="bad-option",
        nodes=[{"key": "a", "type": "interpolate", "values": {"factor": "3x"}}],
        wires=[],
        order=["a"],
        caveat="Only exists to test the option check, so its unwired input is expected.",
    )
    path = tmp_path / "bad-option.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(WorkflowDefinitionError, match="not one of"):
        load_definition_file(path)


def test_a_widget_with_no_options_accepts_any_value(tmp_path: Path) -> None:
    import yaml

    doc = _template(
        id="free-text",
        nodes=[{"key": "a", "type": "generate_anchor", "values": {"prompt": "anything at all"}}],
        wires=[],
        order=["a"],
        caveat="Only exists to test the option check, so its unwired input is expected.",
    )
    path = tmp_path / "free-text.yaml"
    path.write_text(yaml.safe_dump(doc))
    assert load_definition_file(path).id == "free-text"


def test_every_declared_widget_is_one_the_stage_actually_reads() -> None:
    """A widget that looks bound and does nothing is the bug this guards, and it is a class rather
    than a one-off: it has cost three renders to find (upscale_video's resolution, and
    generate_keyframes' two, which the frame count and the generation lock already decided).

    This used to be two assertions naming those nodes, which cannot catch the fourth. It is now a
    static read of stages.py for all 55 node types at once: every declared widget must reach a
    ``_param``-family call or a ``ctx.params.get`` somewhere in its executor.
    """
    from content_factory.workflows.widget_audit import (
        WIDGET_EXEMPTIONS,
        stale_exemptions,
        unread_widgets,
        widget_reads,
    )

    dead = unread_widgets()
    assert dead == {}, (
        "these widgets are on the canvas and no stage reads them — bind them, delete them from"
        f" apps/web/src/workspace/catalog.ts, or add an exemption with a reason: {dead}"
    )
    # The exemption table cannot rot into a list of stale excuses.
    assert stale_exemptions() == {}
    # plan_story's `beats` left this table when transcript mode bound it: a lane that plans a film
    # out of a recording decides how many drawings it gets, and that number is the beat count.
    assert set(WIDGET_EXEMPTIONS) == {"research", "compile_artboards"}
    for stage, exempt in WIDGET_EXEMPTIONS.items():
        for widget, reason in exempt.items():
            assert len(reason) > 40, f"{stage}.{widget} needs a real reason, not a shrug"

    catalog = node_catalog()
    assert catalog["generate_keyframes"]["widgets"] == []
    # The three the audit found and this check now covers by construction.
    reads = widget_reads()
    assert "resolution" in reads["upscale_video"]
    assert {"default_route", "generate_kinds"} <= reads["route_shots"]
    assert {"noise_seed", "guide_strength", "unet_name"} <= reads["generate_video"]


def test_a_widget_the_node_does_not_declare_is_refused(tmp_path: Path) -> None:
    import yaml

    doc = _template(nodes=[{"key": "a", "type": "plan_shots", "values": {"nonsense": 1}}])
    doc["wires"] = []
    doc["order"] = ["a"]
    doc["id"] = "bad-lane"
    path = tmp_path / "bad-lane.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(WorkflowDefinitionError, match="nonsense"):
        load_definition_file(path)


def test_an_incompatible_wire_type_is_refused(tmp_path: Path) -> None:
    import yaml

    # plan_shots outputs SHOTS; qc_deliverable's deliverable input takes a VIDEO or a package.
    doc = _template(
        id="type-clash",
        nodes=[{"key": "a", "type": "plan_shots"}, {"key": "b", "type": "qc_deliverable"}],
        wires=[{"from_key": "a", "from_slot": "shots", "to_key": "b", "to_slot": "deliverable"}],
        order=["a", "b"],
    )
    path = tmp_path / "type-clash.yaml"
    path.write_text(yaml.safe_dump(doc))
    with pytest.raises(WorkflowDefinitionError, match="accepts"):
        load_definition_file(path)


def test_a_file_whose_id_does_not_match_its_name_is_refused(tmp_path: Path) -> None:
    import yaml

    path = tmp_path / "wrong-name.yaml"
    path.write_text(yaml.safe_dump(_template()))
    with pytest.raises(WorkflowDefinitionError, match=r"should be test-lane\.yaml"):
        load_definition_file(path)


def test_stage_order_carries_values_by_node_not_by_stage() -> None:
    """Two nodes running one stage keep their own values. The old table collapsed them."""
    nodes = [
        {"key": "first", "type": "generate_anchor", "values": {"seed": 1}},
        {"key": "second", "type": "generate_anchor", "values": {"seed": 2}},
    ]
    template = WorkflowTemplate.model_validate(
        _template(nodes=nodes, wires=[], order=["first", "second"])
    )
    assert [(k, v["seed"]) for k, _s, v in template.stage_order()] == [("first", 1), ("second", 2)]


def test_every_definition_that_claims_to_be_ready_really_is() -> None:
    for wid, template in load_definitions().items():
        if template.caveat:
            continue
        for stage in template.stages():
            assert stage in STAGE_EXECUTORS, f"{wid} claims no caveat but {stage.value} cannot run"


def test_wire_helper_round_trips() -> None:
    wire = WorkflowWire(from_key="a", from_slot="shots", to_key="b", to_slot="shots")
    assert wire.model_dump()["from_slot"] == "shots"


def test_load_definition_names_the_known_ids_when_asked_for_a_missing_one() -> None:
    with pytest.raises(WorkflowDefinitionError, match="known:"):
        load_definition("no-such-lane")


def test_a_truncated_stage_line_cannot_swallow_the_framing_warning() -> None:
    """The runner truncates a stage's facts to keep a run cheap to read, so a warning arriving as
    the tail of a cut fact blob is a warning nobody sees. It gets its own line instead, emitted
    where the untruncated facts are so every front end gets it."""
    from content_factory.runners.local import stage_warnings

    facts = {
        "shots": 6,
        "planner": "reference",
        "frames": 966,
        "staged_from": ["cmu_18_19_01", "cmu_20_21_02"],
        "underframed": [
            {"shot_id": "shot_aaaa", "body_fraction": 0.2},
            {"shot_id": "shot_bbbb", "body_fraction": 0.31},
        ],
    }
    assert len(json.dumps(facts)) > 160, "the truncation this guards against has to bite"
    (warning,) = stage_warnings(facts)
    assert "2 shot(s)" in warning
    assert "0.20" in warning, "the worst number, not the first"
    assert "shot_aaaa" in warning and "shot_bbbb" in warning
    # And it says what to do, because the planner deliberately does not choose for the operator.
    assert "Widen the frame" in warning


def test_nothing_to_report_prints_nothing() -> None:
    from content_factory.runners.local import stage_warnings

    assert stage_warnings({"shots": 6, "underframed": []}) == []
    assert stage_warnings({"shots": 6}) == []
    assert stage_warnings({"underframed": [{"no": "fields"}]}) == []
    assert stage_warnings({"underframed": "not a list"}) == []
