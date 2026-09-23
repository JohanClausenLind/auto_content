"""ReviewReport from QC findings: shape, dispositions, typed repairs, cache key and coverage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from content_factory.explainer.compile import compile_episode
from content_factory.explainer.qc import QcFinding
from content_factory.explainer.qc_checks import CHECKS, sampled_ms
from content_factory.explainer.review import (
    MODEL_ID,
    PROMPT_SHA256,
    QC_VERSION,
    ReviewCache,
    propose_repair,
    report_from_findings,
    review_cache_key,
    reviewer_identity,
)
from content_factory.schemas.explainer import (
    EvidencePack,
    ExplainerRenderBundle,
    HoldRepair,
    LabelWordingRepair,
    ReviewedArtifact,
    ScriptPlan,
    SplitSceneRepair,
    VisualSpec,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "fixtures" / "explainer"
SHA = hashlib.sha256(b"an mp4").hexdigest()
ARTIFACT = ReviewedArtifact(kind="mp4", sha256=SHA)
CREATED = "2026-09-23T00:00:00+00:00"


def _accepted(name: str) -> ExplainerRenderBundle:
    path = FIXTURES / "review_set" / name / "accepted.json"
    return ExplainerRenderBundle.model_validate_json(path.read_text())


def _amdahl() -> ExplainerRenderBundle:
    d = FIXTURES / "amdahl"
    pack = EvidencePack.model_validate_json((d / "pack.json").read_text())
    script = ScriptPlan.model_validate_json((d / "script.json").read_text())
    spec = VisualSpec.model_validate_json((d / "spec.json").read_text())
    return compile_episode(pack, script, spec)


def _finding(
    check: str,
    scene_id: str,
    passed: bool | None,
    entity_id: str | None = None,
    at_ms: int = 0,
    evidence: str = "measured",
) -> QcFinding:
    measured = None if passed is None else (0.0 if passed else 1.0)
    return QcFinding(check, scene_id, entity_id, at_ms, measured, 0.0, passed, evidence)


def _report(bundle: ExplainerRenderBundle, findings: list[QcFinding]):
    return report_from_findings(
        findings, bundle=bundle, artifact=ARTIFACT, sampled=sampled_ms(bundle), created_at=CREATED
    )


def test_report_covers_every_scene_and_maps_checks_to_categories() -> None:
    bundle = _accepted("tiny_text")
    scene = bundle.timeline.scenes[0].scene_id
    findings = [
        _finding("text_floor", scene, True, "ent_open_stmt"),
        _finding("clipping", scene, False, "ent_open_stmt", 500, "box crosses the canvas edge"),
        _finding("ocr_text", scene, None, "ent_open_stmt", 2000, "OCR unavailable: no venv"),
    ]
    report = _report(bundle, findings)
    assert [c.scene_id for c in report.coverage] == [s.scene_id for s in bundle.timeline.scenes]
    assert report.coverage[0].sampled_ms == tuple(sampled_ms(bundle)[scene])
    assert report.coverage[0].beat_ids == tuple(b.beat_id for b in bundle.spec.scenes[0].beats)
    assert {c.category: c.covered_by for c in report.categories} == {
        "readability": "text_floor",
        "composition": "clipping",
        "source_correctness": "ocr_text",
    }
    blocker, note = report.findings
    assert (blocker.severity, blocker.disposition, blocker.confidence) == ("blocker", "fail", 1.0)
    assert blocker.category == "composition" and blocker.interval.start_ms == 500
    assert blocker.observed.startswith("clipping failed on ent_open_stmt")
    assert (note.severity, note.disposition, note.confidence) == ("note", "uncertain", None)
    assert note.observed == "ocr_text could not be measured on ent_open_stmt"
    assert report.disposition == "fail" and report.authority == "blocking"
    assert report.reviewer == reviewer_identity()
    assert report.reviewer.model_id == MODEL_ID and report.reviewer.model_revision == QC_VERSION
    assert report.timeline_id == bundle.timeline.timeline_id


def test_disposition_is_uncertain_with_only_unknowns_and_pass_when_everything_passes() -> None:
    bundle = _accepted("tiny_text")
    scene = bundle.timeline.scenes[0].scene_id
    assert _report(bundle, [_finding("duration", scene, None)]).disposition == "uncertain"
    passing = _report(bundle, [_finding(check, scene, True) for check in CHECKS])
    assert passing.disposition == "pass" and passing.findings == ()
    assert {c.category for c in passing.categories} >= {"audio", "technical", "readability"}


def test_ocr_text_on_a_source_scene_is_highlight_correctness() -> None:
    bundle = _accepted("wrong_highlight")
    scene = bundle.timeline.scenes[0].scene_id
    report = _report(bundle, [_finding("ocr_text", scene, False, "qt_passage00001")])
    assert report.findings[0].category == "highlight_correctness"
    assert any(c.category == "highlight_correctness" for c in report.categories)


def test_report_id_is_stable_for_the_same_findings_and_artifact() -> None:
    bundle = _accepted("tiny_text")
    scene = bundle.timeline.scenes[0].scene_id
    findings = [_finding("overlap", scene, False, "ent_open_stmt", 300)]
    first, second = _report(bundle, findings), _report(bundle, findings)
    assert first == second and first.report_id.startswith("rev_")
    other = ReviewedArtifact(kind="mp4", sha256=hashlib.sha256(b"other").hexdigest())
    changed = report_from_findings(
        findings, bundle=bundle, artifact=other, sampled=sampled_ms(bundle), created_at=CREATED
    )
    assert changed.report_id != first.report_id


def test_legend_label_with_an_unused_short_label_proposes_label_wording() -> None:
    bundle = _amdahl()
    finding = _finding("text_floor", "scn_latency_bars", False, "ent_series_other")
    repair = propose_repair(finding, bundle)
    assert repair == LabelWordingRepair(
        repair="label_wording", entity_id="ent_series_other", short_label="Other"
    )


def test_wording_finding_without_a_short_label_proposes_a_split_after_the_revealing_beat() -> None:
    bundle = _accepted("axis_change")
    finding = _finding("overlap", "scn_dwell_quiet", False, "ent_dwell_ser")
    assert propose_repair(finding, bundle) == SplitSceneRepair(
        repair="split_scene", scene_id="scn_dwell_quiet", after_beat_id="bt_dwell_a_reveal"
    )


def test_text_item_finding_proposes_a_split_even_when_the_entity_has_a_short_label() -> None:
    bundle = _amdahl()
    finding = _finding("ink_overflow", "scn_big_sixty", False, "ent_big_sixty")
    assert propose_repair(finding, bundle) == SplitSceneRepair(
        repair="split_scene", scene_id="scn_big_sixty", after_beat_id="bt_reveal_sixty"
    )


def test_split_is_proposed_before_the_last_beat_when_the_reveal_is_the_last_beat() -> None:
    bundle = _amdahl()
    finding = _finding("overlap", "scn_latency_bars", False, "ent_total_fast")
    repair = propose_repair(finding, bundle)
    assert isinstance(repair, SplitSceneRepair)
    assert repair.after_beat_id == "bt_reveal_fast"


def test_timing_contract_finding_proposes_a_long_hold_on_the_last_beat() -> None:
    bundle = _accepted("tiny_text")
    evidence = "[timing] VisualSpec.scenes[0]: scene x: ent_open_stmt needs 3425 ms. Fix: hold."
    finding = _finding("contract", "scn_open0000001", False, "ent_open_stmt", 0, evidence)
    assert propose_repair(finding, bundle) == HoldRepair(
        repair="hold", beat_id="bt_open_hold000", duration_class="long"
    )
    other = _finding("contract", "scn_open0000001", False, None, 0, "[stale_binding] x")
    assert propose_repair(other, bundle) is None
    assert propose_repair(_finding("duration", "scn_open0000001", False), bundle) is None


def test_cache_key_changes_with_every_input() -> None:
    base = {
        "media_sha256": SHA,
        "evidence_hash": hashlib.sha256(b"pack").hexdigest(),
        "model_id": MODEL_ID,
        "model_revision": QC_VERSION,
        "rubric_version": "1",
        "prompt_sha256": PROMPT_SHA256,
    }
    key = review_cache_key(**base)
    assert key == review_cache_key(**base)
    for field in base:
        changed = {**base, field: hashlib.sha256(field.encode()).hexdigest()[: len(base[field])]}
        assert review_cache_key(**changed) != key, field


def test_prompt_hash_follows_the_check_list() -> None:
    assert PROMPT_SHA256 == hashlib.sha256("\n".join(CHECKS).encode()).hexdigest()


def test_cache_roundtrip_and_covers_follow_the_file_bytes(tmp_path: Path) -> None:
    bundle = _accepted("tiny_text")
    mp4 = tmp_path / "episode.mp4"
    mp4.write_bytes(b"an mp4")
    report = _report(bundle, [_finding("clipping", bundle.timeline.scenes[0].scene_id, False)])
    cache = ReviewCache(tmp_path / "cache")
    key = review_cache_key(
        media_sha256=SHA,
        evidence_hash="e" * 64,
        model_id=MODEL_ID,
        model_revision=QC_VERSION,
        rubric_version="1",
        prompt_sha256=PROMPT_SHA256,
    )
    assert cache.get(key) is None
    path = cache.put(key, report)
    assert path == cache.path(key) and cache.get(key) == report
    assert json.loads(path.read_text())["report_id"] == report.report_id
    assert not list(path.parent.glob(".*.tmp"))
    assert ReviewCache.covers(report, mp4)
    mp4.write_bytes(b"re-rendered")
    assert not ReviewCache.covers(report, mp4)
    assert not ReviewCache.covers(report, tmp_path / "missing.mp4")


@pytest.mark.parametrize("name", ["tiny_text", "axis_change", "wrong_highlight"])
def test_a_report_round_trips_through_its_canonical_json(name: str) -> None:
    bundle = _accepted(name)
    scene = bundle.timeline.scenes[-1].scene_id
    report = _report(bundle, [_finding("transition", scene, False, None, 1000)])
    from content_factory.schemas.explainer import ReviewReport

    assert ReviewReport.model_validate_json(report.canonical_json()) == report
