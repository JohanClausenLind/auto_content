"""`--set` has to be held to the same standard a lane definition is.

`workflows/catalog.py` rejects a definition that sets a widget the node does not declare, and its
docstring gives the reason: "a typo'd key is silently swallowed by the stage's `_param` default and
the workflow quietly does something else". `--set` was the same door with no lock on it — it
checked the node or stage name and never the key.

The drift knobs are the case that made it visible. `stage_generate_keyframes` has always read
`drift_profile`, `locked_min` and `style_delta_max`, and its own comment tells the operator to
reach for `--set spokes.drift_profile=uncalibrated`, but none of the three was declared on the
node: settable from the command line, invisible on the canvas, and rejected by the definition
validator if a lane tried to freeze one.
"""

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
    """A stage is not a node: it applies to every node running it, so there is no single
    declaration to check against."""
    node_params, by_stage = _parse_overrides(["generate_anchor.style=charcoal"], STEPS)
    assert node_params == {}
    assert by_stage == {Stage.generate_anchor: {"style": "charcoal"}}


def test_zero_leaves_the_drift_profile_alone_rather_than_flooring_it_at_zero(tmp_path) -> None:
    """The widget defaults to 0 because the real thresholds are per-style and resolved at run
    time. If 0 were taken literally, every node that never touched the widget would silently
    override the profile with a floor of zero and pass every frame."""
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
