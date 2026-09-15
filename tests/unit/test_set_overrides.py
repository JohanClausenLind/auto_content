"""`--set` has to be held to the same standard a lane definition is."""

from __future__ import annotations

import pytest
import typer

from content_factory.cli.workflows_cmd import _parse_overrides
from content_factory.schemas.dag import Stage
from content_factory.workflows.catalog import node_catalog

STEPS = [
    ("spokes", Stage.generate_keyframes, {}),
    ("anchor", Stage.generate_anchor, {}),
]


def test_the_drift_knobs_are_declared_on_the_node_that_reads_them() -> None:
    declared = node_catalog()["generate_keyframes"]["widgets"]
    assert {"drift_profile", "locked_min", "style_delta_max"} <= set(declared)
    # And the profile names the stage actually branches on.
    options = node_catalog()["generate_keyframes"]["widget_options"]
    assert options["drift_profile"] == ["configured", "uncalibrated"]


def test_a_real_widget_key_lands_on_the_node() -> None:
    node_params, by_stage = _parse_overrides(["spokes.drift_profile=uncalibrated"], STEPS)
    assert node_params == {"spokes": {"drift_profile": "uncalibrated"}}
    assert by_stage == {}


def test_a_typoed_widget_key_is_refused_rather_than_swallowed(capsys) -> None:
    with pytest.raises(typer.Exit) as exc:
        _parse_overrides(["spokes.drift_profil=uncalibrated"], STEPS)
    assert exc.value.exit_code == 2
    err = capsys.readouterr().err
    assert "names a widget generate_keyframes does not declare" in err
    # The message names what it *would* have accepted, so the typo is fixable from the error.
    assert "drift_profile" in err


def test_a_stage_scoped_override_is_left_alone() -> None:
    """A stage is not a node: it applies to every node running it."""
    node_params, by_stage = _parse_overrides(["generate_anchor.style=charcoal"], STEPS)
    assert node_params == {}
    assert by_stage == {Stage.generate_anchor: {"style": "charcoal"}}


def test_zero_leaves_the_drift_profile_alone_rather_than_flooring_it_at_zero(tmp_path) -> None:
    """The widget defaults to 0 because the real thresholds are per-style."""
    from content_factory.runners.local import make_context
    from content_factory.workflows.stages import _param_float

    ctx = make_context(project_dir=tmp_path / "prj")
    profile_value = 0.92

    unset = _param_float(ctx, "locked_min", 0.0) or profile_value
    assert unset == profile_value

    measured = make_context(project_dir=tmp_path / "prj2")
    measured.params["locked_min"] = "0.85"
    chosen = _param_float(measured, "locked_min", 0.0) or profile_value
    assert chosen == 0.85
