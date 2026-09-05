"""The durable path and the local path ask for the same things, and a timeout means something.

Three gaps between running a lane locally and running it through Temporal:

* `StageNode.resource_class` has been in the DAG since it was written and the workflow **threw it
  away**, so every activity got 240 seconds: minutes of slack for a JSON write, and far too little
  for a GPU stage (one LTX-2.5 anchor pair measured 100-330 s on this host, and a render-gpu node
  holds several shots). When the deadline passed mid-generation Temporal retried the whole
  activity, so the failure mode was "the GPU work restarts for ever".
* The human-gate stages were never run before the workflow parked on them, so the ActionItem said
  "drop the finished asset onto the node" and pointed at a contact sheet that did not exist.
* `review_frames` and `review_assets` had **no validator at all**, so a durable run parked on them
  for ever: there was no way to submit the verdict the stage reads.

No Temporal server here: the pieces under test are the plan, the options table, the ActionItem
body and the two validators.
"""

from __future__ import annotations

import datetime as dt
import json
import typing
from pathlib import Path

import pytest

from content_factory.schemas.dag import ResourceClass
from content_factory.workflows.production import (
    ACTIVITY_TIMEOUTS,
    DEFAULT_ACTIVITY_TIMEOUT_S,
    HEARTBEAT_TIMEOUT_S,
    PREPARABLE_HUMAN_STAGES,
    ExecuteNodeInput,
    HumanValidationInput,
    NodePlan,
    _human_task_body,
    _validate_asset_review,
    _validate_frame_review,
)

DELIVERABLE = "dlv_short0000001"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    """`get_settings` is an `lru_cache`, and conftest's env isolation does not reach it. Without
    clearing on the way *out*, a tmp_path-derived approvals directory leaks into every later test
    in the process — which is a much more annoying failure than the one it would be hiding."""
    from content_factory.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    yield
    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_every_resource_class_has_a_timeout() -> None:
    """A class with no entry silently gets the old 240 seconds, which is the bug this replaces."""
    declared = set(typing.get_args(ResourceClass))
    assert declared == set(ACTIVITY_TIMEOUTS), declared ^ set(ACTIVITY_TIMEOUTS)


def test_the_gpu_classes_get_more_than_the_old_ceiling_and_control_gets_less() -> None:
    """The point is not "be generous", it is to make a timeout mean something."""
    for gpu in ("inference-video", "inference-image", "render-gpu"):
        assert ACTIVITY_TIMEOUTS[gpu] > DEFAULT_ACTIVITY_TIMEOUT_S, gpu
    # A control node that hangs for four minutes was hiding a bug behind a generous deadline.
    assert ACTIVITY_TIMEOUTS["control"] < DEFAULT_ACTIVITY_TIMEOUT_S
    # Nothing publishes during development, so a publish node sitting for an hour is a hang.
    assert ACTIVITY_TIMEOUTS["publish"] < ACTIVITY_TIMEOUTS["inference-llm"]


def test_the_video_timeout_covers_a_measured_shot_list() -> None:
    """40 s a clip measured at 704x384, and a shot list is tens of clips; the post chain runs per
    clip on top of that. Twenty clips plus a post pass has to fit."""
    assert ACTIVITY_TIMEOUTS["inference-video"] >= 20 * 40 * 2


def test_the_heartbeat_stays_short_for_every_class() -> None:
    """It is how a *stuck* activity is told from a slow one. A GPU stage heartbeats through its
    generation, so one that stopped has died — raising this with the timeout would hide that."""
    assert HEARTBEAT_TIMEOUT_S <= 10


def test_a_node_plan_carries_its_resource_class() -> None:
    assert NodePlan("n", "s", None, []).resource_class == "control"
    assert NodePlan("n", "s", None, [], resource_class="inference-video").resource_class == (
        "inference-video"
    )


def test_the_compiled_plan_keeps_the_dag_s_resource_classes() -> None:
    """The whole point of carrying it: the DAG decides, and the workflow has to receive it."""
    from content_factory.deliverables.dag_compiler import stage_defaults

    defaults = stage_defaults()
    gpu_stages = {s.value for s, (rc, _ex) in defaults.items() if rc.startswith("inference-")}
    assert gpu_stages, "the DAG should mark some stages as inference"
    # And each of those classes resolves to a timeout longer than a control node's.
    for stage, (rc, _ex) in defaults.items():
        assert ACTIVITY_TIMEOUTS[rc] > 0, stage
        if rc.startswith("inference-") or rc == "render-gpu":
            assert ACTIVITY_TIMEOUTS[rc] > ACTIVITY_TIMEOUTS["control"], stage


def test_prepare_mode_is_off_by_default() -> None:
    """Every existing caller constructs this without the field."""
    inp = ExecuteNodeInput(
        run_id="r",
        workspace_id="ws_x",
        node_id="n",
        stage="s",
        deliverable_id=None,
        campaign_json="{}",
        quality="demo",
        project_dir=".",
        artifacts_dir=".",
        input_hash="h",
    )
    assert inp.prepare is False


def test_only_the_review_gates_are_prepared() -> None:
    """`write_copy`'s human executor has no deterministic half — the person *is* the executor — so
    preparing it would run the model the human slot exists to replace."""
    assert PREPARABLE_HUMAN_STAGES == {"review_frames", "review_assets"}
    assert "write_copy" not in PREPARABLE_HUMAN_STAGES


def test_the_action_item_names_the_sheet_for_a_review_gate() -> None:
    plain = _human_task_body("write_copy", {})
    assert "Drop the finished asset" in plain
    prepared = _human_task_body(
        "review_frames",
        {"contact_sheet": "reviews/frames/contact-sheet.png", "frames": 30, "gate": "3 unreviewed"},
    )
    assert "reviews/frames/contact-sheet.png" in prepared
    assert "frames: 30" in prepared
    assert "3 unreviewed" in prepared
    # And it does not tell someone to drop an asset onto a node whose job is to look at a sheet.
    assert "Drop the finished asset" not in prepared


def _frame_batch(sha: str, *, reviewed: bool) -> dict:
    now = dt.datetime.now(dt.UTC).isoformat()
    record = {
        "frame_id": "sht_a:0000",
        "png_sha256": sha,
        "findings": [
            {"check": "tonal_range", "passed": True, "severity": "advisory", "detail": "ok"}
        ],
        "verdict": "accept" if reviewed else "unreviewed",
    }
    batch = {
        "schema_version": 1,
        "deliverable_id": DELIVERABLE,
        "contact_sheet_sha256": "c" * 64,
        "contact_sheet_path": "reviews/frames/contact-sheet.png",
        "frames": [record],
        "created_at": now,
    }
    if reviewed:
        batch |= {"reviewer": "operator", "reviewed_at": now}
    return batch


def _human_input(stage: str, payload: dict, project: Path) -> HumanValidationInput:
    return HumanValidationInput(
        run_id="r",
        workspace_id="ws_x",
        node_id=f"{stage}:{DELIVERABLE}",
        stage=stage,
        deliverable_id=DELIVERABLE,
        project_dir=str(project),
        payload_json=json.dumps(payload),
        input_hash="h",
    )


def _prepare_frames(project: Path, sha: str) -> Path:
    path = project / "deliverables" / DELIVERABLE / "reviews" / "frames" / "batch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_frame_batch(sha, reviewed=False)))
    return path


def test_a_frame_verdict_is_written_where_the_stage_reads_it(tmp_path: Path) -> None:
    sha = "a" * 64
    _prepare_frames(tmp_path, sha)
    inp = _human_input("review_frames", _frame_batch(sha, reviewed=True), tmp_path)
    result = _validate_frame_review(inp, tmp_path, json.loads(inp.payload_json))
    assert json.loads(result.facts_json)["accepted"] is True
    verdict = tmp_path / "deliverables" / DELIVERABLE / "reviews" / "frames" / "verdict.json"
    assert verdict.is_file()
    assert json.loads(verdict.read_text())["frames"][0]["verdict"] == "accept"


def test_a_verdict_about_regenerated_images_is_refused(tmp_path: Path) -> None:
    """The reason `FrameRecord` carries a digest at all: a verdict on an image that has since been
    redrawn is not a verdict on what would ship."""
    _prepare_frames(tmp_path, "a" * 64)
    inp = _human_input("review_frames", _frame_batch("b" * 64, reviewed=True), tmp_path)
    result = _validate_frame_review(inp, tmp_path, json.loads(inp.payload_json))
    rejected = json.loads(result.facts_json)["rejected"]
    assert any("regenerated" in r for r in rejected)
    assert not (
        tmp_path / "deliverables" / DELIVERABLE / "reviews" / "frames" / "verdict.json"
    ).exists()


def test_a_verdict_missing_a_frame_is_refused(tmp_path: Path) -> None:
    path = _prepare_frames(tmp_path, "a" * 64)
    prepared = json.loads(path.read_text())
    prepared["frames"].append({**prepared["frames"][0], "frame_id": "sht_a:0001"})
    path.write_text(json.dumps(prepared))
    inp = _human_input("review_frames", _frame_batch("a" * 64, reviewed=True), tmp_path)
    result = _validate_frame_review(inp, tmp_path, json.loads(inp.payload_json))
    assert any("no verdict for" in r for r in json.loads(result.facts_json)["rejected"])


def test_a_verdict_with_no_reviewer_is_refused(tmp_path: Path) -> None:
    """An unsigned verdict is not a verdict; the contract pairs reviewer with reviewed_at."""
    _prepare_frames(tmp_path, "a" * 64)
    inp = _human_input("review_frames", _frame_batch("a" * 64, reviewed=False), tmp_path)
    result = _validate_frame_review(inp, tmp_path, json.loads(inp.payload_json))
    assert any("reviewer" in r for r in json.loads(result.facts_json)["rejected"])


def test_a_verdict_before_the_stage_has_run_is_refused(tmp_path: Path) -> None:
    inp = _human_input("review_frames", _frame_batch("a" * 64, reviewed=True), tmp_path)
    result = _validate_frame_review(inp, tmp_path, json.loads(inp.payload_json))
    assert "no prepared review" in json.loads(result.facts_json)["rejected"]


def test_a_submission_that_is_not_a_frame_batch_is_refused(tmp_path: Path) -> None:
    _prepare_frames(tmp_path, "a" * 64)
    inp = _human_input("review_frames", {"looks": "nothing like a batch"}, tmp_path)
    result = _validate_frame_review(inp, tmp_path, json.loads(inp.payload_json))
    assert json.loads(result.facts_json)["rejected"]


def _asset_review(blend: str, *, approved: bool) -> dict:
    now = dt.datetime.now(dt.UTC).isoformat()
    review = {
        "schema_version": 1,
        "asset": "chr_runner",
        "blend_sha256": blend,
        "reviewed_at": now,
        "checks": [{"check": "manifold", "passed": True, "severity": "blocker", "detail": "ok"}],
    }
    if approved:
        review |= {"approved_by": "human", "approved_at": now}
    return review


def test_an_asset_approval_must_be_about_the_build_on_disk(tmp_path: Path, monkeypatch) -> None:
    """`CharacterAssetReview` carries `blend_sha256` because a rebuild withdraws the approval."""
    from content_factory.config import get_settings

    approvals = tmp_path / "approvals"
    prepared = tmp_path / "deliverables" / DELIVERABLE / "reviews" / "assets"
    prepared.mkdir(parents=True)
    (prepared / "chr_runner.review.json").write_text(
        json.dumps(_asset_review("a" * 64, approved=False))
    )
    # The settings are frozen models, so the override goes through the environment and the cache
    # is cleared either side — approvals live outside the run, so the test must not write into the
    # repo's own approvals directory.
    monkeypatch.setenv("CF__CONTROLS__ASSET_APPROVALS_DIR", str(approvals))
    get_settings.cache_clear()  # type: ignore[attr-defined]

    good = _human_input("review_assets", _asset_review("a" * 64, approved=True), tmp_path)
    result = _validate_asset_review(good, tmp_path, json.loads(good.payload_json))
    assert json.loads(result.facts_json)["accepted"] is True
    assert (approvals / "chr_runner.json").is_file()

    stale = _human_input("review_assets", _asset_review("b" * 64, approved=True), tmp_path)
    refused = _validate_asset_review(stale, tmp_path, json.loads(stale.payload_json))
    assert any("different build" in r for r in json.loads(refused.facts_json)["rejected"])


def test_an_unapproved_asset_review_is_not_an_approval(tmp_path: Path) -> None:
    prepared = tmp_path / "deliverables" / DELIVERABLE / "reviews" / "assets"
    prepared.mkdir(parents=True)
    (prepared / "chr_runner.review.json").write_text(
        json.dumps(_asset_review("a" * 64, approved=False))
    )
    inp = _human_input("review_assets", _asset_review("a" * 64, approved=False), tmp_path)
    result = _validate_asset_review(inp, tmp_path, json.loads(inp.payload_json))
    assert any("approved_by" in r for r in json.loads(result.facts_json)["rejected"])


def test_the_workspace_graph_adapter_is_typed() -> None:
    """It used to return a bare dict in a shape that was not `WorkspaceGraph`'s, so none of the
    contract's invariants applied to the fifteen committed lane definitions."""
    from content_factory.schemas.workspace_graph import WorkspaceGraph
    from content_factory.workflows.catalog import load_definitions, to_workspace_graph

    for workflow_id, template in load_definitions().items():
        graph = to_workspace_graph(template)
        assert isinstance(graph, WorkspaceGraph), workflow_id
        assert graph.graph_id == workflow_id
        # Every link resolves, which is one of the invariants the dict shape could violate.
        ids = {n.id for n in graph.nodes}
        for link in graph.links:
            assert link.from_node in ids and link.to_node in ids, workflow_id
