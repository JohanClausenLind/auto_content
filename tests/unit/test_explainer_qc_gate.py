"""Gate F1: deterministic QC on the sixty fixture rendered once; render-marked, with PaddleOCR."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from content_factory.explainer import render
from content_factory.explainer.compile import compile_episode, write_bundle
from content_factory.explainer.qc import QcFinding
from content_factory.explainer.qc_checks import CHECKS, run_deterministic_qc, sampled_ms
from content_factory.explainer.review import report_from_findings
from content_factory.schemas.base import file_sha256
from content_factory.schemas.explainer import (
    EvidencePack,
    ExplainerRenderBundle,
    ReviewedArtifact,
    ScriptPlan,
    VisualSpec,
)

pytestmark = pytest.mark.render

REPO = Path(__file__).resolve().parents[2]
SIXTY = REPO / "fixtures" / "explainer" / "sixty"


@dataclass(frozen=True)
class Judged:
    pack: EvidencePack
    script: ScriptPlan
    bundle: ExplainerRenderBundle
    mp4: Path
    findings: list[QcFinding]


@pytest.fixture(scope="module")
def judged(tmp_path_factory: pytest.TempPathFactory) -> Judged:
    pack = EvidencePack.model_validate_json((SIXTY / "pack.json").read_text())
    script = ScriptPlan.model_validate_json((SIXTY / "script.json").read_text())
    spec = VisualSpec.model_validate_json((SIXTY / "spec.json").read_text())
    bundle = compile_episode(pack, script, spec)
    out_dir = tmp_path_factory.mktemp("qc-gate")
    path = write_bundle(bundle, out_dir / "bundle.json")
    mp4 = Path(render.render_bundle(path, out_dir / "sixty.mp4").out)
    findings = run_deterministic_qc(bundle, mp4, pack=pack, script=script, spec=spec)
    (out_dir / "findings.json").write_text(
        json.dumps([f.__dict__ for f in findings], indent=2, ensure_ascii=False)
    )
    return Judged(pack, script, bundle, mp4, findings)


def _describe(findings: list[QcFinding]) -> str:
    return "\n".join(
        f"{f.check} {f.scene_id} {f.entity_id or '-'} @{f.at_ms} ms: {f.evidence}" for f in findings
    )


def test_f1_every_check_ran_on_the_sixty_render(judged: Judged) -> None:
    assert {f.check for f in judged.findings} == set(CHECKS)
    measured = {f.check for f in judged.findings if f.passed is not None}
    # The mix is not part of this gate; everything else must have produced a verdict.
    assert measured >= set(CHECKS) - {"true_peak", "loudness", "cue_coverage"}


def test_f1_no_check_fails_on_the_sixty_render(judged: Judged) -> None:
    failed = [f for f in judged.findings if f.passed is False]
    assert not failed, _describe(failed)


# Checks a rendered episode can always measure; the rest may be unknown where an element is dimmed
# or too small on the 360 px decode, but never without a reason.
ALWAYS_MEASURABLE = frozenset(CHECKS) - {
    "true_peak",
    "loudness",
    "cue_coverage",
    "text_apca",
    "small_screen",
    "ink_overflow",
    "line_edge",
}


def test_f1_unknowns_carry_a_reason_and_never_hide_a_measurable_check(judged: Judged) -> None:
    unknown = [f for f in judged.findings if f.passed is None]
    assert all(f.evidence for f in unknown)
    assert {f.check for f in unknown} & ALWAYS_MEASURABLE == set(), _describe(unknown)
    ocr = [f for f in judged.findings if f.check == "ocr_text"]
    assert ocr and all(f.passed is not None for f in ocr), _describe(ocr)


def test_f1_the_report_passes_with_blocking_authority(judged: Judged) -> None:
    artifact = ReviewedArtifact(kind="mp4", sha256=file_sha256(judged.mp4))
    report = report_from_findings(
        judged.findings, bundle=judged.bundle, artifact=artifact, sampled=sampled_ms(judged.bundle)
    )
    assert report.authority == "blocking"
    assert report.disposition != "fail", _describe(
        [f for f in judged.findings if f.passed is False]
    )
    assert len(report.coverage) == len(judged.bundle.timeline.scenes)
    assert {c.category for c in report.categories} >= {
        "readability",
        "composition",
        "animation_completion",
        "narration_alignment",
        "misleading_comparison",
        "source_correctness",
        "technical",
    }
