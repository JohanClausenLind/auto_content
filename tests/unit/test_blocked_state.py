"""BLOCKED: a run that stopped on judgement rather than on a defect.

`RunState.BLOCKED` has been in `db/models.py` since the run state machine was written and nothing
ever set it (STATUS 2992). Everything a stage could not finish was a `RuntimeError`, so a run that
had generated a frame three times, checked it three times and been handed three unusable pictures
looked exactly like a run with a bug in it — same exception, same FAILED state, same invitation to
retry, which is three more GPU-minutes per frame for the same answer.

The other half is that the deterministic checks were running too late. `review_frames` measures
tonal collapse and a half-applied monochrome instruction, and it runs when a person is already
looking at the contact sheet — so a thirty-anchor run spent three GPU hours and then showed a
reviewer four frames whose style instruction the code could have caught the moment each arrived.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from content_factory.config import get_settings
from content_factory.runners.local import LocalRunError, make_context, run_stages
from content_factory.schemas.dag import Stage
from content_factory.workflows.blocked import (
    BLOCKED_EXIT_CODE,
    BlockedError,
    blocked_details,
)
from content_factory.workflows.stages import StageContext, stage_generate_anchor

MONOCHROME_STYLE = "ink wash, monochrome, no colour at all, brush on paper"
"""A style that asks for no colour. The mock backend paints a saturated red subject rectangle, so
every frame it draws fails `monochrome_honoured` — which is the one blocker-severity finding the
deterministic checks raise, and it fails for the real reason rather than a faked one."""


@pytest.fixture
def ctx(tmp_path: Path) -> StageContext:
    return make_context(project_dir=tmp_path / "project", brief={"topic": "a harbour at dawn"})


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("CF__IMAGE_SEQUENCES__BACKEND", "mock")
    monkeypatch.setenv("CF__CONTROLS__ASSETS_ROOT", str(tmp_path / "no-assets"))
    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_the_error_carries_what_a_person_needs_and_survives_wrapping() -> None:
    blocked = BlockedError(
        "anchor shot_x:0 failed monochrome_honoured on every attempt",
        stage="generate_anchor",
        attempts=3,
        candidates=[{"attempt": 1, "seed": 7, "path": "anchors/shot_x/0000.attempt1.png"}],
    )
    assert "3 attempt(s)" in str(blocked) and "attempt1.png" in str(blocked)
    assert blocked.as_dict()["attempts"] == 3
    assert blocked_details(blocked) == blocked.as_dict()
    # Wrapped, which is how it reaches the runner and the CLI.
    wrapper = RuntimeError("stage failed")
    wrapper.__cause__ = blocked
    assert blocked_details(wrapper) == blocked.as_dict()
    # An ordinary failure is not a block, and must not be mistaken for one.
    assert blocked_details(RuntimeError("ffmpeg exited 1")) is None
    assert BLOCKED_EXIT_CODE == 5


def test_an_unusable_anchor_is_regenerated_then_blocks_with_its_candidates(
    ctx: StageContext, monkeypatch
) -> None:
    monkeypatch.setenv("CF__IMAGE_SEQUENCES__MAX_REGEN_ATTEMPTS_PER_FRAME", "3")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    object.__setattr__(ctx, "params", {"style": MONOCHROME_STYLE})

    with pytest.raises(BlockedError) as info:
        stage_generate_anchor(ctx)
    blocked = info.value
    assert blocked.stage == "generate_anchor"
    assert blocked.attempts == 3, "every attempt is spent before a person is asked"
    assert "monochrome_honoured" in blocked.reason

    # Three different seeds, derived rather than random, so the second attempt is the same second
    # attempt on every machine — the pattern build_sequence already uses for keyframes.
    seeds = [c["seed"] for c in blocked.candidates]
    assert seeds == sorted(seeds) and len(set(seeds)) == 3
    assert seeds[0] == get_settings().image_sequences.anchor_seed

    # Every rejected candidate is on disk. The whole point is that someone looks at them.
    for candidate in blocked.candidates:
        assert (ctx.ddir() / candidate["path"]).exists()
        checks = {f["check"] for f in candidate["blockers"]}
        assert checks == {"monochrome_honoured"}
    # And nothing was written as if it had passed.
    assert not (ctx.ddir() / "anchors" / "anchor.done.json").exists()


def test_a_frame_that_passes_first_time_costs_one_attempt_and_the_locks_own_seed(
    ctx: StageContext,
) -> None:
    """The ordinary case has to stay exactly what it was: one call, the lock's seed, no extra
    files. A check that changes the picture when it passes is not a check."""
    out = stage_generate_anchor(ctx)
    assert out.facts["attempts"] == 1
    marker = json.loads((ctx.ddir() / "anchors" / "anchor.done.json").read_text())
    assert marker["attempts"] == 1
    assert marker["seed"] == get_settings().image_sequences.anchor_seed
    assert not list((ctx.ddir() / "anchors").glob("*.attempt*.png"))
    # Idempotent: the second run is a cache hit and does not re-check anything.
    again = stage_generate_anchor(ctx)
    assert again.facts["cache_hits"] == 1 and again.outputs_hash == out.outputs_hash


def test_the_runner_reports_a_block_as_a_block_not_a_failure(ctx: StageContext) -> None:
    logs: list[str] = []
    object.__setattr__(ctx, "params", {})
    with pytest.raises(LocalRunError) as info:
        run_stages(
            [Stage.generate_anchor],
            ctx,
            params={Stage.generate_anchor: {"style": MONOCHROME_STYLE}},
            log=logs.append,
        )
    error = info.value
    assert error.blocked is not None and error.blocked["attempts"] == 3
    assert str(error).startswith("stage generate_anchor blocked:")
    assert any(line.startswith("    BLOCKED") for line in logs)

    report = json.loads((ctx.ddir() / "run.json").read_text())
    assert report["passed"] is False
    assert report["blocked_at"] == "generate_anchor"
    stage = report["stages"][-1]
    assert stage["ok"] is False and stage["blocked"]["stage"] == "generate_anchor"
    assert len(stage["blocked"]["candidates"]) == 3


def test_the_cli_labels_it_block_and_exits_five(tmp_path: Path, monkeypatch) -> None:
    """Exit 5, deliberately not 1: a wrapper script has to be able to tell "needs a human" from
    "is broken" without parsing text, and 4 is already the human-review gate."""
    from typer.testing import CliRunner

    from content_factory.cli.main import app

    monkeypatch.setenv("CF__IMAGE_SEQUENCES__BACKEND", "mock")
    get_settings.cache_clear()  # type: ignore[attr-defined]
    result = CliRunner().invoke(
        app,
        [
            "make",
            "single-image",
            "--project-dir",
            str(tmp_path / "run"),
            "--until",
            "anchor",
            # The lane freezes its own style, so the monochrome instruction goes on the node.
            "--set",
            f"anchor.style={MONOCHROME_STYLE}",
            "--force",
        ],
    )
    assert result.exit_code == BLOCKED_EXIT_CODE, result.output
    assert "BLOCK" in result.output
    payload = json.loads(result.output.strip().splitlines()[-1])
    assert payload["blocked_at"] == "generate_anchor"
    assert payload["blocked"]["attempts"] == 3
    assert payload["blocked"]["candidates"]


def test_the_durable_path_turns_it_into_a_non_retryable_blocked_outcome() -> None:
    """The workflow has to recognise a block through Temporal's wrapping, and must not retry it:
    the stage already spent every attempt, so a retry is GPU time for the same answer."""
    from temporalio.exceptions import ActivityError, ApplicationError

    from content_factory.workflows.blocked import BLOCKED_FAILURE_TYPE

    payload = BlockedError("three unusable frames", stage="generate_anchor", attempts=3).as_dict()
    app_error = ApplicationError(
        "three unusable frames", payload, type=BLOCKED_FAILURE_TYPE, non_retryable=True
    )
    assert app_error.non_retryable
    assert blocked_details(app_error) == payload

    wrapped = ActivityError(
        "activity failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="test",
        activity_type="execute_node",
        activity_id="1",
        retry_state=None,
    )
    wrapped.__cause__ = app_error
    assert blocked_details(wrapped) == payload

    # And the state the workflow sets for it is the one that has been reserved and unused.
    from content_factory.db.models import RunState

    assert RunState.blocked.value == "BLOCKED"
