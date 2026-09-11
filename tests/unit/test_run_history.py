"""Reading a run's outputs off disk, without a database and without leaving the output root.

226 runs and 35 films were on this machine with no way to look at any of them except by knowing
the path. These tests cover the parts that decide whether the panel is useful or noise: which file
gets shown first, what counts as bookkeeping rather than output, and that an id cannot name
somewhere else on the filesystem.
"""

from __future__ import annotations

import json
from pathlib import Path

from content_factory.services import run_history as rh


def write(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_run(root: Path, name: str, *, stages: list[tuple[str, bool]], passed: bool) -> Path:
    run_dir = root / name
    deliverable = run_dir / "deliverables" / "dlv_short0000001"
    write(
        deliverable / "run.json",
        json.dumps(
            {
                "workflow": "audio-picture-story",
                "project_dir": str(run_dir),
                "deliverable_id": "dlv_short0000001",
                "stages": [{"stage": s, "ok": ok, "seconds": 3.0} for s, ok in stages],
                "passed": passed,
            }
        ).encode(),
    )
    return run_dir


def test_the_film_and_the_first_real_drawing_are_what_represent_a_run(tmp_path: Path) -> None:
    """`exports/final.mp4` is the run's own answer to which video is the deliverable, and a
    history row showing a pose skeleton is a row nobody recognises."""
    run = make_run(tmp_path, "story", stages=[("generate_anchor", True)], passed=True)
    d = run / "deliverables" / "dlv_short0000001"
    write(d / "controls" / "shot_a" / "pose_skeleton" / "frames" / "0000.png")
    write(d / "exports" / "generated.mp4")
    write(d / "exports" / "final.mp4")
    write(d / "anchors" / "frames" / "0000.png")

    record = rh.get_run("story", root=tmp_path)
    assert record is not None
    assert record.film == "deliverables/dlv_short0000001/exports/final.mp4"
    assert record.poster == "deliverables/dlv_short0000001/anchors/frames/0000.png"
    # Debug output is last, so it cannot bury the six pictures that are the actual result.
    roles = [o.role for o in record.outputs]
    assert roles.index("anchor") < roles.index("control")


def test_done_markers_are_bookkeeping_not_output(tmp_path: Path) -> None:
    """Measured on a real picture story: 85 of its 125 JSON files were `<frame>.done.json`
    markers the runner writes so a stage can be re-run safely. Listing those as output made a
    six-picture story a list of 130 rows of nothing."""
    run = make_run(tmp_path, "markers", stages=[("generate_anchor", True)], passed=True)
    d = run / "deliverables" / "dlv_short0000001"
    for i in range(3):
        write(d / "anchors" / f"{i:04d}.png")
        write(d / "anchors" / f"{i:04d}.done.json")
    write(d / "anchors" / "manifest.json")
    write(d / "story" / "plan.json")

    record = rh.get_run("markers", root=tmp_path)
    assert record is not None
    by_path = {o.path: o for o in record.outputs}
    assert by_path["deliverables/dlv_short0000001/anchors/0000.done.json"].role == "marker"
    assert by_path["deliverables/dlv_short0000001/anchors/manifest.json"].role == "marker"
    # The story plan is a real artifact and stays visible.
    assert by_path["deliverables/dlv_short0000001/story/plan.json"].role != "marker"
    assert all(r in rh.DEBUG_ROLES for r in ["marker", "control", "anchor-upscaled"])


def test_the_manifest_wins_over_the_scan_for_a_packaged_run(tmp_path: Path) -> None:
    """A run that reached packaging said what it produced, with roles and content types. Where
    that exists it is the answer — the scan only knows what a path looks like."""
    run = make_run(
        tmp_path, "packaged", stages=[("compile_destination_packages", True)], passed=True
    )
    d = run / "deliverables" / "dlv_short0000001"
    write(d / "audio" / "narration-mastered.wav")
    write(
        d / "destination-packages" / "packages.json",
        json.dumps(
            [
                {
                    "files": [
                        {
                            "role": "audio",
                            "path": "audio/narration-mastered.wav",
                            "bytes": 2_879_118,
                            "content_type": "audio/x-wav",
                        }
                    ]
                }
            ]
        ).encode(),
    )
    record = rh.get_run("packaged", root=tmp_path)
    assert record is not None
    narration = next(o for o in record.outputs if o.path.endswith("audio/narration-mastered.wav"))
    # The manifest's byte count and content type, not the one-byte file the test wrote.
    assert narration.bytes == 2_879_118
    assert narration.content_type == "audio/x-wav"


def test_a_run_that_never_packaged_still_shows_what_it_drew(tmp_path: Path) -> None:
    """The case that matters most here: six of seven overnight image sets blocked at review with
    their drawings on disk and no package at all."""
    run = make_run(
        tmp_path,
        "parked",
        stages=[("generate_anchor", True), ("review_frames", False)],
        passed=False,
    )
    write(run / "deliverables" / "dlv_short0000001" / "anchors" / "anchor.png")
    record = rh.get_run("parked", root=tmp_path)
    assert record is not None
    assert record.outcome == "review"  # a gate is not a defect
    assert record.poster == "deliverables/dlv_short0000001/anchors/anchor.png"


def test_a_run_id_round_trips_and_cannot_name_anywhere_else(tmp_path: Path) -> None:
    root = tmp_path / "output"
    nested = root / "overnight" / "ps1c-pinecone"
    nested.mkdir(parents=True)
    assert rh.encode_run_id(nested, root) == "overnight~ps1c-pinecone"
    assert rh.decode_run_id("overnight~ps1c-pinecone", root) == nested.resolve()

    # A symlink inside the output tree pointing outside it is the case a string check misses.
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (root / "escape").symlink_to(outside)
    assert rh.decode_run_id("escape", root) is None
    for hostile in ("", "..", "..~..~etc", ".ssh", "a/b", "a\\b", "~root", "nope"):
        assert rh.decode_run_id(hostile, root) is None, hostile


def test_an_output_path_cannot_leave_its_run(tmp_path: Path) -> None:
    run = tmp_path / "run"
    write(run / "deliverables" / "a.png")
    write(tmp_path / "secret.png")
    (run / "link.png").symlink_to(tmp_path / "secret.png")

    assert rh.resolve_output(run, "deliverables/a.png") is not None
    for hostile in (
        "../secret.png",
        "link.png",
        "deliverables/../../secret.png",
        "/etc/passwd",
        "",
    ):
        assert rh.resolve_output(run, hostile) is None, hostile
    # An extension that is not on the allowlist is not servable even inside the run.
    write(run / "script.sh")
    assert rh.resolve_output(run, "script.sh") is None


def test_the_list_is_newest_first_and_carries_the_cost_of_each_run(tmp_path: Path) -> None:
    make_run(tmp_path, "older", stages=[("plan_story", True)], passed=True)
    newer = make_run(
        tmp_path, "newer", stages=[("plan_story", True), ("voice_over", True)], passed=True
    )
    import os

    report = newer / "deliverables" / "dlv_short0000001" / "run.json"
    os.utime(report, (2_000_000_000, 2_000_000_000))

    rows = rh.list_runs(root=tmp_path)
    assert rows[0].run_id == "newer"
    assert rows[0].seconds == 6.0  # two stages at 3s: the evidence behind every ETA
    assert rows[0].stages_ok == 2
    # The list does not scan outputs — that walk is what the detail view pays for.
    assert rows[0].outputs == [] and rows[0].outputs_total == 0


def test_a_half_written_report_is_skipped_rather_than_raising(tmp_path: Path) -> None:
    """A live run rewrites its report after every stage, so reading a partial one is normal."""
    write(tmp_path / "broken" / "run.json", b"{not json")
    write(tmp_path / "notarun" / "run.json", json.dumps({"hello": "world"}).encode())
    make_run(tmp_path, "fine", stages=[("plan_story", True)], passed=True)
    assert [r.run_id for r in rh.list_runs(root=tmp_path)] == ["fine"]
