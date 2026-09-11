"""Pinned nodes: a frozen step is skipped, reused, and recorded as frozen.

The point of a pin is that a rerun does not pay for a node again. The point of *these* tests is
that it cannot lie about it while doing so: the stage never executes, the report says `pinned`
rather than pretending to have produced the output, and the 0.0 s it took never reaches the
duration medians that tell the next run how long a lane should take.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.runners import pins
from content_factory.runners.local import make_context, run_plan, run_stages
from content_factory.schemas.dag import Stage

STEPS = [
    ("plan_shots", Stage.plan_shots, {}),
    ("route_shots", Stage.route_shots, {}),
]


@pytest.fixture(autouse=True)
def _story_presets(monkeypatch) -> None:
    """Both stages here run offline off the preset planner."""
    monkeypatch.setenv("CF__SHOTS__PLANNER", "story_presets")
    from content_factory.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]


def _first_run(tmp_path: Path):
    ctx = make_context(project_dir=tmp_path / "prj")
    report = run_stages([Stage.plan_shots, Stage.route_shots], ctx, log=lambda _m: None)
    assert report["passed"]
    return ctx, report


def test_a_pinned_node_is_skipped_and_its_recorded_output_reused(tmp_path: Path) -> None:
    ctx, first = _first_run(tmp_path)
    was = {s["node"]: s["outputs_hash"] for s in first["stages"]}

    pins.pin_nodes(ctx.ddir(), first, ["plan_shots"])

    logs: list[str] = []
    second = run_plan(STEPS, ctx, workflow="test", log=logs.append)

    planned = next(s for s in second["stages"] if s["node"] == "plan_shots")
    routed = next(s for s in second["stages"] if s["node"] == "route_shots")

    assert planned["pinned"] is True
    assert planned["ok"] is True
    assert planned["seconds"] == 0.0
    # The reused hash is the one the first run produced, not a fresh one.
    assert planned["outputs_hash"] == was["plan_shots"]
    # A pinned node must be *visible* as not-run, not silently absent from the output.
    joined = "\n".join(logs)
    assert "--- 1 pinned node(s), not run: plan_shots" in joined
    assert f"    pinned {was['plan_shots'][:12]}, not run" in joined
    assert "==> plan_shots [plan_shots]" in joined

    # The unpinned node still ran, and could still read what the pinned one left on disk.
    assert "pinned" not in routed
    assert routed["ok"] and routed["outputs_hash"]

    assert second["pinned"] == ["plan_shots"]


def test_no_pins_runs_the_whole_lane_anyway(tmp_path: Path) -> None:
    ctx, first = _first_run(tmp_path)
    pins.pin_nodes(ctx.ddir(), first, ["plan_shots"])

    report = run_plan(STEPS, ctx, workflow="test", use_pins=False, log=lambda _m: None)

    assert all("pinned" not in s for s in report["stages"])
    assert "pinned" not in report
    # The pin file is left alone: --no-pins is "ignore them this once", not "release them".
    assert pins.load(ctx.ddir())


def test_pinning_a_node_that_never_ran_is_refused(tmp_path: Path) -> None:
    ctx, first = _first_run(tmp_path)
    with pytest.raises(pins.PinError) as exc:
        pins.pin_nodes(ctx.ddir(), first, ["generate_anchor"])
    assert "generate_anchor" in str(exc.value)
    assert "Run the node once, then pin it" in str(exc.value)
    assert not pins.load(ctx.ddir())


def test_a_pin_for_a_node_the_workflow_no_longer_has_refuses_before_spending_anything(
    tmp_path: Path,
) -> None:
    ctx, first = _first_run(tmp_path)
    pins.pin_nodes(ctx.ddir(), first, ["plan_shots"])
    # The lane changed under the pin: plan_shots is gone.
    shortened = [("route_shots", Stage.route_shots, {})]

    with pytest.raises(pins.PinError) as exc:
        run_plan(shortened, ctx, workflow="test", log=lambda _m: None)
    assert "plan_shots is pinned but this workflow has no such node" in str(exc.value)


def test_unpinning_releases_and_is_forgiving(tmp_path: Path) -> None:
    ctx, first = _first_run(tmp_path)
    pins.pin_nodes(ctx.ddir(), first, ["plan_shots", "route_shots"])
    assert set(pins.load(ctx.ddir())) == {"plan_shots", "route_shots"}

    pins.unpin_nodes(ctx.ddir(), ["plan_shots"])
    assert set(pins.load(ctx.ddir())) == {"route_shots"}

    # Unpinning something that is not pinned is not an error.
    pins.unpin_nodes(ctx.ddir(), ["plan_shots", "nothing_here"])
    assert set(pins.load(ctx.ddir())) == {"route_shots"}

    pins.unpin_nodes(ctx.ddir(), ["route_shots"])
    assert pins.load(ctx.ddir()) == {}
    # Nothing pinned means no file left behind.
    assert not pins.pins_path(ctx.ddir()).exists()


def test_a_pinned_stage_never_reaches_the_duration_medians(tmp_path: Path) -> None:
    """A skipped stage took 0.0 s. Letting that in would teach the estimator that a six-minute
    generation is instant."""
    from content_factory.services import durations

    root = tmp_path / "runs"
    (root / "deliverables" / "d1").mkdir(parents=True)
    (root / "deliverables" / "d1" / "run.json").write_text(
        json.dumps(
            {
                "workflow": "test-lane",
                "passed": True,
                "stages": [
                    {"stage": "generate_anchor", "node": "a", "ok": True, "seconds": 360.0},
                    {
                        "stage": "generate_anchor",
                        "node": "b",
                        "ok": True,
                        "pinned": True,
                        "seconds": 0.0,
                    },
                ],
            }
        )
    )

    samples = [
        (wf, stage, secs)
        for wf, stage, secs in durations._reports(root)
        if stage == "generate_anchor"
    ]
    assert samples == [("test-lane", "generate_anchor", 360.0)]


def test_a_pin_describes_itself_in_the_operators_terms(tmp_path: Path) -> None:
    ctx, first = _first_run(tmp_path)
    frozen = pins.pin_nodes(ctx.ddir(), first, ["plan_shots"])
    described = frozen["plan_shots"].describe()
    assert described.startswith("plan_shots (plan_shots, ")
    assert "pinned " in described and "ago)" in described


def test_an_unreadable_pin_file_reads_as_no_pins(tmp_path: Path) -> None:
    """Pins are an optimisation; a run must never fail to start because one is corrupt."""
    run_dir = tmp_path / "d"
    run_dir.mkdir()
    pins.pins_path(run_dir).write_text("{not json")
    assert pins.load(run_dir) == {}
