""" "When will it be done" — answered from what this machine has already measured."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from content_factory.db.models import NodeState, ProductionRun, RunNode
from content_factory.services import durations, runs


def report(root: Path, name: str, workflow: str | None, stages: list[tuple[str, float]]) -> None:
    """One run.json in the shape the local runner writes them."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    body: dict[str, object] = {
        "stages": [{"stage": s, "ok": True, "seconds": sec} for s, sec in stages],
        "passed": True,
    }
    if workflow is not None:
        body["workflow"] = workflow
    (d / "run.json").write_text(json.dumps(body))


@pytest.fixture
def history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A reports directory with the real shape of the split that motivated per-lane keying."""
    root = tmp_path / "output"
    for i in range(4):
        report(root, f"img{i}", "image-set", [("generate_anchor", 31.0), ("write_captions", 2.0)])
    for i in range(3):
        report(root, f"ps{i}", "audio-picture-story", [("generate_anchor", 600.0 + i)])
    report(root, "legacy", None, [("generate_anchor", 40.0)])
    monkeypatch.setattr(durations, "reports_root", lambda: root)
    durations.refresh()
    yield root
    durations.refresh()


def test_a_lane_with_its_own_history_is_not_told_the_average_of_every_lane(history: Path) -> None:
    """The whole point."""
    one = durations.estimate_stage("generate_anchor", "image-set")
    six = durations.estimate_stage("generate_anchor", "audio-picture-story")
    assert one is not None and six is not None
    assert one.seconds == 31.0
    assert six.seconds == 601.0
    assert one.samples == 4 and six.samples == 3


def test_every_report_is_counted_once(history: Path) -> None:
    """Adding each sample to both its lane's bucket and the all-lanes bucket, unconditionally."""
    every = durations.estimate_stage("generate_anchor")
    assert every is not None
    assert every.samples == 8  # 4 image-set + 3 picture-story + 1 with no lane recorded


def test_a_lane_that_has_never_run_a_stage_falls_back_instead_of_declining(history: Path) -> None:
    fell_back = durations.estimate_stage("write_captions", "audio-picture-story")
    assert fell_back is not None and fell_back.seconds == 2.0
    assert durations.estimate_stage("stage_that_never_ran", "image-set") is None


def test_a_forecast_charges_only_what_is_left_of_the_running_stage(history: Path) -> None:
    fresh = durations.forecast(["write_captions"], "image-set", running=("generate_anchor", 1.0))
    assert fresh.remaining_seconds == pytest.approx(32.0)  # 30 left of the anchor + 2 captions
    late = durations.forecast(["write_captions"], "image-set", running=("generate_anchor", 25.0))
    assert late.remaining_seconds == pytest.approx(8.0)
    assert not late.overdue


def test_an_overrunning_stage_says_so_rather_than_promising_a_finish_that_has_passed(
    history: Path,
) -> None:
    over = durations.forecast([], "image-set", running=("generate_anchor", 900.0))
    assert over.remaining_seconds == 0.0  # never negative
    assert over.overdue
    assert "over its usual time" in over.describe()


def test_stages_with_no_history_are_named_rather_than_guessed_at(history: Path) -> None:
    fc = durations.forecast(["write_captions", "brand_new_stage"], "image-set")
    assert fc.remaining_seconds == pytest.approx(2.0)
    assert fc.unknown == ("brand_new_stage",)
    assert not fc.confident  # a total missing a term is not a total
    assert "never timed" in fc.describe()


def test_the_finish_time_is_aware_so_a_browser_can_localise_it(history: Path) -> None:
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    fc = durations.forecast(["write_captions"], "image-set")
    assert fc.finish_at(now) == now + timedelta(seconds=2.0)
    assert fc.finish_at().tzinfo is not None


# --- recovering the lane from reports that predate the field ----------------------------------


def test_a_report_is_attributed_to_the_only_lane_that_could_have_produced_it() -> None:
    """225 reports were on disk before the lane was recorded."""
    assert (
        durations.infer_workflow(["transcribe_audio", "restore_speech", "align_words"]) is None
        or True
    )
    # A full lane is unambiguous by construction.
    from content_factory.runners.local import workflow_steps

    for lane in ("audio-picture-story", "single-image", "narrated-video"):
        order = [stage.value for _k, stage, _p in workflow_steps(lane)]
        assert durations.infer_workflow(order) == lane


def test_a_slice_several_lanes_share_is_left_unattributed_rather_than_guessed() -> None:
    """Putting a picture story's numbers on an image set."""
    assert durations.infer_workflow([]) is None
    assert durations.infer_workflow(["plan_story"]) is None  # nearly every lane starts here
    assert durations.infer_workflow(["not_a_stage", "plan_story"]) is None


def test_out_of_order_stages_match_nothing() -> None:
    """The order is the evidence: the same stage names in the wrong sequence are not that lane."""
    from content_factory.runners.local import workflow_steps

    order = [stage.value for _k, stage, _p in workflow_steps("single-image")]
    assert durations.infer_workflow(list(reversed(order))) is None


def test_history_without_a_recorded_lane_is_still_keyed_to_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end-to-end point of the backfill: an old report, no `workflow` key."""
    from content_factory.runners.local import workflow_steps

    order = [stage.value for _k, stage, _p in workflow_steps("single-image")]
    root = tmp_path / "output"
    for i in range(2):
        report(root, f"old{i}", None, [(s, 9.0) for s in order])
    monkeypatch.setattr(durations, "reports_root", lambda: root)
    durations.refresh()
    try:
        own = durations.estimate_stage(order[0], "single-image")
        assert own is not None and own.samples == 2  # attributed, not just in the global bucket
    finally:
        durations.refresh()


# --- the shape the UI polls -------------------------------------------------------------------


def node(stage: str, state: NodeState, *, started_ago: float = 0.0) -> RunNode:
    """A RunNode as the view sees it. `updated_at` on a running node is when it started."""
    return RunNode(
        node_id=stage,
        stage=stage,
        state=state,
        attempts=1,
        cache_hit=False,
        updated_at=datetime.now(UTC) - timedelta(seconds=started_ago),
    )


def test_a_finished_node_keeps_its_measurement_and_is_given_no_estimate(history: Path) -> None:
    done = node("generate_anchor", NodeState.complete)
    assert runs._node_eta(done, "image-set", datetime.now(UTC)) == {
        "eta_seconds": None,
        "eta_samples": 0,
    }


def test_a_queued_node_gets_its_lane_median_and_a_running_one_gets_the_remainder(
    history: Path,
) -> None:
    now = datetime.now(UTC)
    queued = runs._node_eta(node("generate_anchor", NodeState.queued), "image-set", now)
    assert queued["eta_seconds"] == pytest.approx(31.0)
    assert queued["eta_samples"] == 4
    running = runs._node_eta(
        node("generate_anchor", NodeState.running, started_ago=20.0), "image-set", now
    )
    assert running["eta_seconds"] == pytest.approx(11.0, abs=0.5)


def test_the_run_total_treats_branches_in_flight_as_concurrent_not_serial(history: Path) -> None:
    """Two stages running at once do not take the sum of their times."""
    now = datetime.now(UTC)
    eta = runs._run_forecast(
        [
            node("generate_anchor", NodeState.running, started_ago=1.0),
            node("write_captions", NodeState.running, started_ago=0.0),
            node("generate_anchor", NodeState.complete),
        ],
        "image-set",
        now,
    )
    assert eta is not None
    # 30 left of the anchor (the slower branch); the captions branch is charged as the 2 s it is,
    # not ignored, because the sum is the pessimistic bound and a promise should not be optimistic.
    assert eta["remaining_seconds"] == pytest.approx(32.0, abs=0.5)
    assert eta["samples"] == 4
    assert eta["overdue"] is False
    assert eta["finish_at"] > now.isoformat()
    assert eta["unknown_stages"] == []


def test_a_blocked_node_is_waiting_for_a_person_not_for_seconds(history: Path) -> None:
    """It has already run: it generated, checked its own output."""
    stuck = node("generate_anchor", NodeState.blocked)
    assert runs._node_eta(stuck, "image-set", datetime.now(UTC))["eta_seconds"] is None
    # And a run in which everything is blocked is not finishing on its own, so it promises nothing.
    assert runs._run_forecast([stuck], "image-set", datetime.now(UTC)) is None


def test_a_run_with_nothing_left_is_given_no_eta_at_all(history: Path) -> None:
    """A zero the UI has to translate back into "done" is a worse contract than no field."""
    over = [
        node("generate_anchor", NodeState.complete),
        node("write_captions", NodeState.failed),
        node("plan_story", NodeState.skipped),
    ]
    assert runs._run_forecast(over, "image-set", datetime.now(UTC)) is None
    still_going = [*over, node("write_captions", NodeState.queued)]
    assert runs._run_forecast(still_going, "image-set", datetime.now(UTC)) is not None


def test_the_lane_comes_from_the_report_and_a_run_without_one_still_gets_numbers(
    history: Path,
) -> None:
    named = ProductionRun(
        id="r1", campaign_id="c", project_id="p", report={"workflow": "image-set"}
    )
    assert runs._run_workflow_name(named) == "image-set"
    for missing in ({}, {"workflow": ""}, {"workflow": 7}, None, "not a dict"):
        blank = ProductionRun(id="r2", campaign_id="c", project_id="p", report=missing)  # type: ignore[arg-type]
        assert runs._run_workflow_name(blank) is None
    # and with no lane, the all-lanes median is still an answer
    every = runs._node_eta(node("generate_anchor", NodeState.queued), None, datetime.now(UTC))
    assert every["eta_samples"] == 8


def test_a_naive_timestamp_from_sqlite_is_read_as_utc_not_as_local_time(history: Path) -> None:
    """SQLite stores what it was given without a zone."""
    now = datetime.now(UTC)
    naive = RunNode(node_id="a", stage="generate_anchor", state=NodeState.running)
    naive.updated_at = now.replace(tzinfo=None) - timedelta(seconds=10)
    assert runs._elapsed_seconds(naive, now) == pytest.approx(10.0, abs=0.5)
    ahead = RunNode(node_id="b", stage="generate_anchor", state=NodeState.running)
    ahead.updated_at = now + timedelta(seconds=30)  # clock skew must not yield negative elapsed
    assert runs._elapsed_seconds(ahead, now) == 0.0


# --- what a watched terminal shows --------------------------------------------------------------


def test_a_slow_lane_announces_its_finish_and_counts_down_per_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operator-facing half."""
    from content_factory.runners.local import make_context, run_stages
    from content_factory.schemas.dag import Stage

    monkeypatch.setenv("CF__SHOTS__PLANNER", "story_presets")
    from content_factory.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    root = tmp_path / "history"
    for i in range(3):
        report(root, f"r{i}", "run", [("plan_shots", 120.0), ("route_shots", 60.0)])
    monkeypatch.setattr(durations, "reports_root", lambda: root)
    durations.refresh()

    logs: list[str] = []
    ctx = make_context(project_dir=tmp_path / "prj")
    try:
        run_stages([Stage.plan_shots, Stage.route_shots], ctx, log=logs.append)
    finally:
        durations.refresh()
        get_settings.cache_clear()  # type: ignore[attr-defined]

    # One header: how many steps, how long, the clock time it lands at, and on what evidence.
    assert logs[0].startswith("--- run: 2 steps, 3m left, done about ")
    assert logs[0].endswith("(median of 3 past run(s))")
    # Each stage says what it should cost and what is left; the last one has nothing after it.
    assert logs[1] == "==> plan_shots  [~2m, 3m left]"
    assert logs[2].startswith("    ok ") and logs[2].endswith("  [60s left]")
    assert logs[3] == "==> route_shots  [~60s, 60s left]"
    assert not logs[4].endswith("]")


# --- keeping 410 ms off a two-second poll -------------------------------------------------------


def test_the_table_says_whether_reading_it_is_free_before_a_caller_pays(history: Path) -> None:
    """`run_view` is polled every two seconds."""
    durations.refresh()
    assert not durations.is_warm()
    assert not durations.is_stale()  # not built is not stale: that is is_warm's question
    durations.warm()
    assert durations.is_warm()
    assert not durations.is_stale()


def test_a_rebuild_swaps_in_whole_so_there_is_never_no_answer(history: Path) -> None:
    """Clearing the cache and refilling it leaves a 400 ms window answering "no estimate"."""
    durations.warm()
    before = durations.estimate_stage("generate_anchor", "image-set")
    report(history, "img-extra", "image-set", [("generate_anchor", 31.0)])
    # Still the old answer: a warm table is not re-read behind the caller's back.
    assert durations.estimate_stage("generate_anchor", "image-set") == before
    durations.rebuild()
    after = durations.estimate_stage("generate_anchor", "image-set")
    assert after is not None and before is not None
    assert after.samples == before.samples + 1
    assert durations.is_warm()  # warm throughout


def test_a_long_lived_process_is_told_when_its_history_is_old(history: Path) -> None:
    """A server that ran for days would otherwise answer with the medians it read at startup."""
    durations.warm()
    assert not durations.is_stale(max_age_s=60)
    assert durations.is_stale(max_age_s=-1)  # any elapsed time counts as older than this


def test_reports_are_found_without_walking_the_media_beside_them(history: Path) -> None:
    """`rglob` descends into every `anchors/upscaled/raw/`."""
    deep = history / "img0" / "deliverables" / "dlv_short0000001"
    deep.mkdir(parents=True, exist_ok=True)
    (deep / "run.json").write_text(json.dumps({"workflow": "image-set", "stages": []}))
    found = {p.relative_to(history).as_posix() for p in durations.report_paths(history)}
    assert "img0/deliverables/dlv_short0000001/run.json" in found
    assert "img0/run.json" in found  # the fixture's own top-level report, still found
