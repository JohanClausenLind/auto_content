"""Node parameters: what an operator types into a canvas widget reaches the executor.

Before this, a workspace graph carried only its shape — the widgets were decoration and every
stage read the process-wide settings, so two graphs on one machine could not differ. The
parameters ride on the DAG node, enter the node's input hash, and are read by the stages that
know them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.runners.local import make_context
from content_factory.schemas.fixtures import sample_campaign
from content_factory.schemas.shots import BonePose, ShotPlan
from content_factory.schemas.workspace_graph import WorkspaceGraph, WorkspaceLink, WorkspaceNode
from content_factory.sequences.engine import MockReferenceEditBackend
from content_factory.workflows.stages import (
    _anchor_lock,
    _param,
    _param_size,
    stage_plan_shots,
    stage_plan_story,
)
from content_factory.workspace import compile_graph

REPO_ROOT = Path(__file__).resolve().parents[2]
LOVE_SHOTS = "fixtures/shots/love_story_topdown.json"
LOVE_STORY = "fixtures/story/love_story.json"


def _graph(*nodes: WorkspaceNode, links: tuple[WorkspaceLink, ...] = ()) -> WorkspaceGraph:
    return WorkspaceGraph(graph_id="g_params", name="Params", nodes=nodes, links=links)


def test_widget_values_are_frozen_onto_the_dag_node() -> None:
    g = _graph(
        WorkspaceNode(id="brief", type="input.brief", x=0, y=0, values={"topic": "two hands"}),
        WorkspaceNode(id="story", type="plan_story", x=0, y=0, values={"story": LOVE_STORY}),
        WorkspaceNode(
            id="shots",
            type="plan_shots",
            x=0,
            y=0,
            values={"planner": "fixture", "fixture_path": LOVE_SHOTS, "fps": 24},
        ),
        links=(
            WorkspaceLink(
                id="l1", from_node="story", from_slot="story", to_node="shots", to_slot="story"
            ),
        ),
    )
    compilation = compile_graph(g, sample_campaign())
    assert compilation.ok, compilation.problems
    assert compilation.dag is not None
    by_stage = {n.stage.value: n for n in compilation.dag.nodes}
    assert by_stage["plan_story"].params == {"story": LOVE_STORY}
    # Numbers become strings: the widgets are form controls, the stages parse what they know.
    assert by_stage["plan_shots"].params["fps"] == "24"
    assert by_stage["plan_shots"].params["planner"] == "fixture"
    # The brief is not a stage; its values become the campaign instead.
    assert compilation.campaign is not None
    assert compilation.campaign.brief.topic == "two hands"


def test_parameters_survive_the_dag_json_round_trip() -> None:
    g = _graph(
        WorkspaceNode(id="brief", type="input.brief", x=0, y=0, values={"topic": "t"}),
        WorkspaceNode(id="shots", type="plan_shots", x=0, y=0, values={"planner": "fixture"}),
    )
    dag = compile_graph(g, sample_campaign()).dag
    assert dag is not None
    from content_factory.schemas.dag import DeliverableDAG

    reloaded = DeliverableDAG.model_validate_json(dag.model_dump_json())
    assert {n.stage.value: n.params for n in reloaded.nodes}["plan_shots"] == {"planner": "fixture"}


def test_accessors_fall_back_to_the_configured_default(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path)
    assert _param(ctx, "missing", "default") == "default"
    assert _param_size(ctx, "size", (1024, 576)) == (1024, 576)
    sized = make_context(project_dir=tmp_path)
    object.__setattr__(sized, "params", {"size": "896x512", "empty": ""})
    assert _param_size(sized, "size", (1024, 576)) == (896, 512)
    assert _param(sized, "empty", "fallback") == "fallback"


def test_plan_story_loads_a_written_script(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path)
    object.__setattr__(ctx, "params", {"story": LOVE_STORY})
    out = stage_plan_story(ctx)
    assert out.facts["beats"] == 6
    from content_factory.schemas.scenes import StoryPlan

    plan = StoryPlan.model_validate_json((tmp_path / "story" / "plan.json").read_text())
    assert plan.beats[3].display_text == "Hey. You're so beautiful."


def test_plan_shots_loads_the_love_story_fixture(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path)
    object.__setattr__(ctx, "params", {"planner": "fixture", "fixture_path": LOVE_SHOTS})
    out = stage_plan_shots(ctx)
    assert out.facts["planner"] == "fixture"
    assert out.facts["shots"] == 30
    plan = ShotPlan.model_validate_json((ctx.ddir() / "shots" / "plan.json").read_text())
    first, last = plan.shots[0], plan.shots[-1]
    # Two people, each posed bone by bone: this is what keeps the walk the same walk.
    assert [c.id for c in first.characters] == ["man", "woman"]
    poses = [c.pose for c in first.characters]
    assert all(isinstance(p, BonePose) for p in poses)
    assert isinstance(poses[0], BonePose) and "upperleg01.L" in poses[0].bones

    # They start apart and end within reach; the camera looks straight down the whole way.
    def gap(shot):
        return abs(
            shot.characters[1].transform.position[0] - shot.characters[0].transform.position[0]
        )

    # They start apart and end within reach. The opening gap is bounded above by the framing floor
    # (a person must stay big enough for the skeleton to be read), so this checks the shape of the
    # story, not a magic number: see test_story_fixtures for the floor itself.
    # The opening gap is bounded above by what an overhead camera can frame with readable bodies,
    # and the closing gap below by how far two arms actually reach; both come from geometry rather
    # than taste, so this checks the shape of the story between them.
    assert gap(first) > 2.0
    assert gap(last) < 1.5
    assert gap(first) > gap(last) * 1.8
    assert all(s.camera.keyframes[0].position[2] > 1.5 for s in plan.shots)
    # One drawing per shot, and Blender only renders the frame that gets drawn.
    assert all(s.anchor_frames == (0,) and s.render.frames == (0,) for s in plan.shots)


def test_anchor_lock_takes_its_style_and_seed_from_the_node(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path)
    backend = MockReferenceEditBackend()
    default = _anchor_lock(width=1024, height=576, backend=backend, ctx=ctx)
    object.__setattr__(ctx, "params", {"style": "ink and watercolour, paper grain", "seed": "42"})
    styled = _anchor_lock(width=1024, height=576, backend=backend, ctx=ctx)
    assert styled.style_prompt == "ink and watercolour, paper grain"
    assert styled.seed == 42
    assert styled.style_prompt != default.style_prompt
    # A different style is a different lock: every frame downstream regenerates.
    assert styled.content_hash() != default.content_hash()


@pytest.mark.parametrize("fixture", [LOVE_SHOTS, LOVE_STORY])
def test_shipped_fixtures_are_valid_contracts(fixture: str) -> None:
    """The template points the operator at these paths; a broken fixture must fail here, not
    twenty minutes into a run."""
    text = (REPO_ROOT / fixture).read_text()
    if fixture == LOVE_SHOTS:
        assert len(ShotPlan.model_validate_json(text).shots) == 30
    else:
        from content_factory.schemas.scenes import StoryPlan

        assert len(StoryPlan.model_validate_json(text).beats) == 6


def test_the_anchor_reference_set_is_a_parameter_and_changes_the_cache_key(tmp_path: Path) -> None:
    """Which control passes reach the image model is the single biggest lever on what it draws,
    so it is a node parameter, and changing it has to invalidate the anchors it produced."""
    from content_factory.controls.bundle import ControlBundle
    from content_factory.workflows.stages import _conditioning_for_frame

    bundle_dir = tmp_path / "shot"
    for kind in ("pose_skeleton", "rough_rgb", "layout_boxes"):
        frames = bundle_dir / kind / "frames"
        frames.mkdir(parents=True)
        (frames / "0000.png").write_bytes(_PNG_1PX)

    from content_factory.schemas.fixtures import sample_control_bundle

    bundle = sample_control_bundle()
    assert isinstance(bundle, ControlBundle)
    # A pass the bundle does not carry is skipped rather than faked, so the fixture (skeleton +
    # layout boxes) yields one reference whichever way rough_rgb is asked for.
    available = {tr.kind.value for tr in bundle.tracks}
    assert "pose_skeleton" in available and "rough_rgb" not in available

    skeleton_only, skeleton_slots = _conditioning_for_frame(
        bundle, bundle_dir, 0, None, ("pose_skeleton",)
    )
    with_layout, layout_slots = _conditioning_for_frame(
        bundle, bundle_dir, 0, None, ("pose_skeleton", "layout_boxes")
    )
    nothing, no_slots = _conditioning_for_frame(bundle, bundle_dir, 0, None, ("rough_rgb",))
    assert len(skeleton_only.reference_pngs) == 1
    assert len(with_layout.reference_pngs) == 2
    assert len(nothing.reference_pngs) == 0
    digests = {skeleton_only.sha256(), with_layout.sha256(), nothing.sha256()}
    assert len(digests) == 3, "a different reference set must be a different cache key"
    # Which slot carried what, in order. Upstream branches on the reference COUNT, so a run whose
    # identity sheet was missing silently became a one-reference run on another scheduler; the
    # slots are recorded so that is visible in the marker rather than inferred from a number.
    assert [s["role"] for s in skeleton_slots] == ["pose_skeleton"]
    assert [s["role"] for s in layout_slots] == ["pose_skeleton", "layout_boxes"]
    assert [s["slot"] for s in layout_slots] == [0, 1]
    assert all(s["subject_id"] is None and s["box"] is None for s in layout_slots)
    assert no_slots == []


_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082"
)


def test_style_presets_resolve_and_a_typo_is_refused() -> None:
    """The style is the one string that must be identical for every frame of a film, so it is a
    named preset. A mistyped name would otherwise be sent to the model as a two-word prompt and
    quietly produce something unrelated for three GPU-hours."""
    from content_factory.sequences.styles import STYLE_PRESETS, resolve_style

    assert "riso" in STYLE_PRESETS and "ink_wash" in STYLE_PRESETS
    assert resolve_style("riso") == STYLE_PRESETS["riso"]
    # A prompt written out in full passes through untouched.
    written_out = "chalk pastel on black sugar paper, smudged, no outlines"
    assert resolve_style(written_out) == written_out
    with pytest.raises(ValueError, match="unknown style preset"):
        resolve_style("watercolor")  # near-miss spelling of a real preset


def test_changing_the_style_preset_redraws_the_film(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path)
    backend = MockReferenceEditBackend()
    locks = {}
    for preset in ("ink_wash", "riso", "charcoal"):
        object.__setattr__(ctx, "params", {"style": preset})
        locks[preset] = _anchor_lock(width=1024, height=576, backend=backend, ctx=ctx)
    assert len({lock.content_hash() for lock in locks.values()}) == 3
    assert "risograph" in locks["riso"].style_prompt


def test_the_style_leads_the_anchor_prompt(tmp_path: Path) -> None:
    """Position, not cosmetics. With the style clause last — behind the subject and the staging —
    the image model ignored it and returned its default idiom for every art direction; the same
    words moved to the front produced the style that was asked for."""
    from content_factory.schemas.shots import ShotPlan
    from content_factory.workflows.stages import _anchor_prompt

    ctx = make_context(project_dir=tmp_path)
    object.__setattr__(ctx, "params", {"style": "woodblock", "prompt": "Two people on a plaza."})
    lock = _anchor_lock(width=1024, height=576, backend=MockReferenceEditBackend(), ctx=ctx)
    shot = ShotPlan.model_validate_json((REPO_ROOT / LOVE_SHOTS).read_text()).shots[-1]
    prompt = _anchor_prompt(ctx, shot, lock)

    assert prompt.startswith("Japanese woodblock print")
    assert prompt.index("woodblock") < prompt.index("Two people on a plaza")
    assert shot.description is not None
    assert prompt.index("Two people on a plaza") < prompt.index(shot.description[:20])
    # Nothing may be appended after the staging that would dilute the style clause further.
    assert prompt.rstrip().endswith(".")
    # The progression is the clip's prompt, not the still's. A frame cannot carry out "rise slowly
    # towards each other": asking a still to is how one arrives with motion blur and extra hands.
    assert shot.motion_prompt not in prompt
    assert "rise slowly" not in prompt


def test_the_model_widget_picks_the_anchor_backend_and_a_host_setting_still_wins(
    tmp_path: Path, monkeypatch
) -> None:
    """`model` was declared on the canvas and read by nothing, so three lanes said
    `model: hidream-o1` and every one of them drew mock rectangles.

    The precedence is the design. A machine that has configured `image_sequences.backend` has said
    something about itself — "no GPU here" — and must be obeyed, or that setting is unenforceable
    on any lane that pins a model. `model_fields_set` is what separates a *configured* mock from a
    *defaulted* one; they are the same string and they mean different things.
    """
    import pytest

    from content_factory.config import get_settings
    from content_factory.workflows.stages import _anchor_backend_name

    ctx = make_context(project_dir=tmp_path)
    # This test's whole subject is configured-versus-defaulted, so it sets its own premise rather
    # than inheriting one: `tests/conftest.py` configures the mock backends for the suite (a lane
    # definition may now pin a real model, and an offline test must not run it), and "nothing
    # configured" has to mean nothing configured.
    monkeypatch.delenv("CF__IMAGE_SEQUENCES__BACKEND", raising=False)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        # Nothing configured: the lane's model decides.
        object.__setattr__(ctx, "params", {"model": "hidream-o1"})
        assert _anchor_backend_name(ctx) == "hidream"
        object.__setattr__(ctx, "params", {"model": "flux2-dev"})
        assert _anchor_backend_name(ctx) == "flux2"
        object.__setattr__(ctx, "params", {})
        assert _anchor_backend_name(ctx) == "mock"  # the setting's own default

        # Options the widget offers that nothing can execute are refused by name, before any GPU
        # tenant is started, rather than silently falling back to the mock.
        for model in ("krea2-turbo", "comfy-fixture"):
            object.__setattr__(ctx, "params", {"model": model})
            with pytest.raises(RuntimeError, match="cannot run"):
                _anchor_backend_name(ctx)
        object.__setattr__(ctx, "params", {"model": "sdxl"})
        with pytest.raises(RuntimeError, match="unknown generate_anchor model"):
            _anchor_backend_name(ctx)

        # A configured host setting outranks the lane's pin.
        monkeypatch.setenv("CF__IMAGE_SEQUENCES__BACKEND", "mock")
        get_settings.cache_clear()  # type: ignore[attr-defined]
        object.__setattr__(ctx, "params", {"model": "hidream-o1"})
        assert _anchor_backend_name(ctx) == "mock"
        # And the per-run injection outranks everything, which is how the offline tests work.
        object.__setattr__(ctx, "params", {"model": "hidream-o1", "backend": "flux2"})
        assert _anchor_backend_name(ctx) == "flux2"
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]
