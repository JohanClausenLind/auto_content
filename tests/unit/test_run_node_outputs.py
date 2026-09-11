"""Which node made which file — recorded by the run, and inferred for the runs that predate it.

The history could group a run's files by file type and could not say which step produced any of
them, so a bad drawing was five lists away from the stage that drew it. These tests pin both
halves of the fix: a run now observes what each step leaves behind, and the 241 reports already on
disk are read through a path table that says when it is guessing.

The honesty rules are what actually matter here, so each has its own test: a file two candidate
stages could have written is left unattributed rather than hung on the likelier one; a node the
lane has and this run did not execute is reported as not-run rather than as failed; and a pinned
node keeps the files of the run that made them instead of appearing to have produced nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

from content_factory.runners import attribution
from content_factory.runners.local import make_context, run_plan
from content_factory.schemas.dag import Stage
from content_factory.services import run_history, run_nodes

# --- the snapshot ------------------------------------------------------------------------------


def test_a_snapshot_sees_new_and_changed_files_and_ignores_the_resume_cache(tmp_path: Path) -> None:
    (tmp_path / "anchors").mkdir()
    (tmp_path / "anchors" / "0000.png").write_bytes(b"one")
    before = attribution.snapshot(tmp_path)

    (tmp_path / "anchors" / "0001.png").write_bytes(b"two")
    (tmp_path / "anchors" / "0000.png").write_bytes(b"one, redrawn")
    (tmp_path / ".stages").mkdir()
    (tmp_path / ".stages" / "cache.json").write_text("{}")
    after = attribution.snapshot(tmp_path)

    assert attribution.changed(before, after) == ["anchors/0000.png", "anchors/0001.png"]
    # `.stages` is the runner's resume cache, not something a step produced.
    assert not any(path.startswith(".stages") for path in after)


def test_a_deleted_file_is_not_reported_as_produced(tmp_path: Path) -> None:
    (tmp_path / "gone.png").write_bytes(b"x")
    before = attribution.snapshot(tmp_path)
    (tmp_path / "gone.png").unlink()
    # A node that removed a file did not make one, and a panel must not offer a link to it.
    assert attribution.changed(before, attribution.snapshot(tmp_path)) == []


def test_an_unreadable_directory_is_nothing_observed_rather_than_an_error(tmp_path: Path) -> None:
    assert attribution.snapshot(tmp_path / "never-existed") == {}


def test_the_cap_keeps_the_files_a_person_reviews_and_still_reports_the_real_count() -> None:
    # The shape of one `generate_keyframes` step: a handful of frames among thousands of markers.
    paths = [f"audio/bea_{i:05d}.done.json" for i in range(400)]
    paths += [f"sequence/frames/{i:04d}.png" for i in range(8)]
    record = attribution.node_files(paths, limit=10)

    assert record.total == 408
    # Alphabetically the 400 `audio/` markers come first and would have spent every slot; the
    # frames the step exists to make are what the record has to keep.
    assert list(record.paths[:8]) == [f"sequence/frames/{i:04d}.png" for i in range(8)]


# --- the run records it ------------------------------------------------------------------------


def test_a_run_records_the_files_each_node_left_behind(tmp_path: Path) -> None:
    ctx = make_context(project_dir=tmp_path / "prj")
    report = run_plan(
        [("shots", Stage.plan_shots, {}), ("routing", Stage.route_shots, {})],
        ctx,
        workflow="test-lane",
        log=lambda *_a: None,
    )

    by_node = {s["node"]: s for s in report["stages"]}
    assert by_node["shots"]["outputs"]["paths"] == ["deliverables/dlv_short0000001/shots/plan.json"]
    assert by_node["routing"]["outputs"]["paths"] == [
        "deliverables/dlv_short0000001/shots/routing.json"
    ]
    # Written to disk, not only returned: the history reads the file.
    on_disk = json.loads((ctx.ddir() / "run.json").read_text())
    assert on_disk["stages"][0]["outputs"]["total"] == 1


def test_a_failing_node_still_records_what_it_managed_to_write(tmp_path: Path) -> None:
    """A stage that gave up part way is exactly when its partial output is the evidence."""
    ctx = make_context(project_dir=tmp_path / "prj")
    # route_shots without plan_shots before it has nothing to route.
    try:
        run_plan([("routing", Stage.route_shots, {})], ctx, log=lambda *_a: None)
    except Exception:
        pass  # the failure is the point here; the report is what is asserted
    report = json.loads((ctx.ddir() / "run.json").read_text())
    assert report["stages"][0]["ok"] is False
    assert "outputs" in report["stages"][0]


def test_a_pinned_node_keeps_the_files_of_the_run_that_made_them(tmp_path: Path) -> None:
    from content_factory.runners import pins

    ctx = make_context(project_dir=tmp_path / "prj")
    steps = [("shots", Stage.plan_shots, {}), ("routing", Stage.route_shots, {})]
    first = run_plan(steps, ctx, workflow="test-lane", log=lambda *_a: None)
    produced = first["stages"][0]["outputs"]["paths"]
    assert produced

    pins.save(
        ctx.ddir(),
        {
            "shots": pins.Pin(
                node="shots",
                stage=Stage.plan_shots.value,
                outputs_hash=first["stages"][0]["outputs_hash"],
                facts=first["stages"][0]["facts"],
                pinned_at=0.0,
            )
        },
    )
    second = run_plan(steps, ctx, workflow="test-lane", log=lambda *_a: None)

    pinned_record = next(s for s in second["stages"] if s["node"] == "shots")
    assert pinned_record["pinned"] is True
    # The walk saw nothing at this node, because nothing ran. Saying "produced no files" would be
    # the opposite of what a pin means: the output is there and later stages read it.
    assert pinned_record["outputs"]["paths"] == produced


# --- reading it back ---------------------------------------------------------------------------


def _report(**extra: object) -> dict:
    return {"project_dir": "/tmp/x", "stages": [], "passed": False, **extra}


def test_a_recorded_path_beats_the_table_and_says_so() -> None:
    report = _report(
        stages=[
            {
                "node": "anchor",
                "stage": "generate_anchor",
                "ok": True,
                "outputs": {"paths": ["captions/captions.srt"], "total": 1},
            }
        ]
    )
    placed = run_nodes.attribute(report, ["captions/captions.srt"])
    # The table would call this `compile_captions`. The run watched `anchor` write it, and what
    # the run observed outranks what a path looks like.
    assert placed["captions/captions.srt"] == ("anchor", "generate_anchor", "recorded")


def test_a_path_two_stages_of_this_lane_could_have_written_is_left_unattributed() -> None:
    both = _report(
        stages=[
            {"node": "voice", "stage": "voice_over", "ok": True},
            {"node": "tts", "stage": "synthesize_narration", "ok": True},
        ]
    )
    one = _report(stages=[{"node": "tts", "stage": "synthesize_narration", "ok": True}])
    path = "audio/narration-stem.wav"

    assert path not in run_nodes.attribute(both, [path])
    assert run_nodes.attribute(one, [path])[path] == ("tts", "synthesize_narration", "inferred")


def test_nothing_is_hung_on_a_stage_this_run_never_ran() -> None:
    report = _report(stages=[{"node": "story", "stage": "plan_story", "ok": True}])
    assert run_nodes.attribute(report, ["anchors/0000.png"]) == {}


def test_a_resumed_run_still_attributes_the_files_its_earlier_slice_made() -> None:
    """The run somebody opens is the one that needed a resume, and its report is a slice.

    `ps1c-pinecone` resumed at `upscale_video`: read from its stage records alone the lane has no
    `anchor` node, and every anchor drawing in the run belongs to nobody.
    """
    resumed = _report(
        workflow="image-set",
        steps=[{"node": key, "stage": "", "values": {}} for key in ("story", "anchor", "lock")],
        stages=[{"node": "package", "stage": "package_sequence", "ok": True, "seconds": 1.0}],
    )
    records = {r.node: r for r in run_nodes.node_records(resumed, workflow="image-set")}

    # The whole lane is present, in lane order, from the definition rather than from the slice.
    assert [r.node for r in run_nodes.node_records(resumed, workflow="image-set")][:3] == [
        "story",
        "anchor",
        "lock",
    ]
    # A node this run did not execute is not-run, which is not the same as failed.
    assert records["anchor"].ran is False and records["anchor"].error is None
    assert records["package"].ran is True and records["package"].ok is True
    placed = run_nodes.attribute(resumed, ["anchors/0000.png"], workflow="image-set")
    assert placed["anchors/0000.png"] == ("anchor", "generate_anchor", "inferred")


def test_the_subject_is_what_the_operator_asked_for_before_what_the_lane_made_of_it(
    tmp_path: Path,
) -> None:
    (tmp_path / "story").mkdir()
    (tmp_path / "story" / "plan.json").write_text(
        json.dumps({"visual_subject": "a pine cone", "hook_text": "Why cones open"})
    )
    briefed = _report(steps=[{"node": "brief", "stage": "", "values": {"topic": "how cones open"}}])
    assert run_nodes.subject(tmp_path, briefed) == "how cones open"
    # No brief widget: the lane's own subject answers, so a fixture-started run is still named.
    assert run_nodes.subject(tmp_path, _report()) == "a pine cone"
    # Neither: a row falls back to its directory name rather than inventing a subject.
    assert run_nodes.subject(tmp_path / "elsewhere", _report()) is None


def test_a_long_subject_is_trimmed_to_something_a_row_can_hold() -> None:
    long = (
        "one small iceberg of blue-white ice floating in dark green water under a flat grey "
        "sky, nothing else on the water at all, shot from the deck of a passing ship"
    )
    subject = run_nodes.subject(
        Path("/nonexistent"), _report(steps=[{"node": "b", "values": {"topic": long}}])
    )
    assert subject is not None
    assert len(subject) <= run_nodes.MAX_SUBJECT and subject.endswith("…")


def test_the_history_hangs_a_runs_outputs_off_its_nodes(tmp_path: Path) -> None:
    run_dir = tmp_path / "mylane"
    (run_dir / "deliverables" / "dlv_1" / "anchors").mkdir(parents=True)
    (run_dir / "deliverables" / "dlv_1" / "anchors" / "0000.png").write_bytes(b"png")
    (run_dir / "deliverables" / "dlv_1" / "captions").mkdir()
    (run_dir / "deliverables" / "dlv_1" / "captions" / "captions.srt").write_text("1\n")
    (run_dir / "deliverables" / "dlv_1" / "run.json").write_text(
        json.dumps(
            {
                "workflow": "image-set",
                "project_dir": str(run_dir),
                "deliverable_id": "dlv_1",
                "passed": True,
                "stages": [
                    {
                        "node": "anchor",
                        "stage": "generate_anchor",
                        "ok": True,
                        "seconds": 48.1,
                        "facts": {"backend": "hidream-o1", "attempts": 3},
                        "outputs": {
                            "paths": ["deliverables/dlv_1/anchors/0000.png"],
                            "total": 1,
                        },
                    }
                ],
            }
        )
    )

    record = run_history.get_run("mylane", root=tmp_path)
    assert record is not None
    anchor = next(n for n in record.nodes if n.node == "anchor")
    assert anchor.outputs == ["deliverables/dlv_1/anchors/0000.png"]
    assert anchor.attribution == "recorded"
    # The facts travel with the node: "3 attempts, backend hidream-o1" is the first thing to know
    # about a drawing that came out wrong.
    assert anchor.facts == {"backend": "hidream-o1", "attempts": 3}
    # The caption file belongs to a node this lane does not have, so nobody claims it, and the
    # count of those is reported rather than hidden.
    assert record.unattributed == 1
    assert record.as_dict()["unattributed"] == 1


def test_a_run_in_progress_is_running_and_not_failed(tmp_path: Path, monkeypatch) -> None:
    """The defect this fixes was visible on screen the first time a run was watched.

    A report is written after every step and only carries ``passed`` at the very end, so mid-flight
    it is indistinguishable from a run whose last stage gave up — and the workspace's history row
    for a lane that was busy drawing frames said **failed**, in red. The run registry is the only
    thing that knows, so the reader asks it.
    """
    run_dir = tmp_path / "inflight"
    deliverable = run_dir / "deliverables" / "dlv_1"
    deliverable.mkdir(parents=True)
    report = {
        "workflow": "image-set",
        "project_dir": str(run_dir),
        "deliverable_id": "dlv_1",
        "passed": False,  # as every report reads until the last step finishes
        "stages": [{"node": "anchor", "stage": "generate_anchor", "ok": True, "seconds": 48.1}],
    }
    (deliverable / "run.json").write_text(json.dumps(report))

    # Nothing registered: the only honest reading of a half-written report is that it stopped.
    stopped = run_history.get_run("inflight", root=tmp_path)
    assert stopped is not None and stopped.outcome == "failed"

    class Registered:
        project_dir = str(run_dir)

    monkeypatch.setattr(
        "content_factory.runners.registry.active_runs", lambda **_: [Registered()], raising=False
    )
    running = run_history.get_run("inflight", root=tmp_path)
    assert running is not None and running.outcome == "running"
    assert run_history.list_runs(root=tmp_path)[0].outcome == "running"


def test_reading_the_history_never_prunes_the_run_registry(monkeypatch) -> None:
    """This module promises to write nothing. A page refresh must not clean up after a run.

    ``active_runs`` deletes the registry files of dead runs by default, which is right for
    ``content-factory stop`` and wrong for a reader polled every ten seconds by a browser.
    """
    seen: list[dict] = []

    def spy(**kwargs):
        seen.append(kwargs)
        return []

    monkeypatch.setattr("content_factory.runners.registry.active_runs", spy, raising=False)
    run_history.live_project_dirs()
    assert seen == [{"prune": False}]
