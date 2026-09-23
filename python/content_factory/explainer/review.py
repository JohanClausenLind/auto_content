"""ReviewReport from deterministic QC findings, and the cache keyed by media, evidence, reviewer."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from content_factory.explainer.qc import QcFinding
from content_factory.explainer.qc_checks import CHECKS, QC_VERSION
from content_factory.schemas.base import canonical_dumps, file_sha256
from content_factory.schemas.explainer import (
    CategoryCoverage,
    CoverageSpan,
    Disposition,
    ExplainerRenderBundle,
    HoldRepair,
    LabelWordingRepair,
    ReviewCategory,
    ReviewedArtifact,
    ReviewerIdentity,
    ReviewFinding,
    ReviewReport,
    Scene,
    SplitSceneRepair,
    TimeInterval,
    TypedRepair,
)

MODEL_ID = "deterministic-qc"
RUBRIC_VERSION = "1"
PROMPT_SHA256 = hashlib.sha256("\n".join(CHECKS).encode()).hexdigest()
TEXT_LIMIT = 600
WORDING_CHECKS = frozenset({"text_floor", "ink_overflow", "overlap"})
# Templates whose text comes from the entity label, so a short_label changes what is drawn.
LABEL_TEMPLATES = frozenset({"chart", "diagram"})
CATEGORY_OF: dict[str, ReviewCategory] = {
    "contract": "technical",
    "evidence": "source_correctness",
    "assets": "technical",
    "capability": "technical",
    "cue_coverage": "narration_alignment",
    "clipping": "composition",
    "ink_overflow": "readability",
    "text_floor": "readability",
    "overlap": "composition",
    "safe_area": "composition",
    "duration": "technical",
    "true_peak": "audio",
    "loudness": "audio",
    "text_apca": "readability",
    "line_edge": "readability",
    "small_screen": "readability",
    "ocr_text": "source_correctness",
    "transition": "animation_completion",
    "axis_change": "misleading_comparison",
    "narration_visual": "narration_alignment",
    "misleading_axis": "misleading_comparison",
}
CATEGORY_ORDER: tuple[ReviewCategory, ...] = (
    "readability",
    "composition",
    "continuity",
    "animation_completion",
    "narration_alignment",
    "misleading_comparison",
    "source_correctness",
    "highlight_correctness",
    "communication",
    "technical",
    "audio",
)


def reviewer_identity() -> ReviewerIdentity:
    return ReviewerIdentity(
        model_id=MODEL_ID,
        model_revision=QC_VERSION,
        prompt_sha256=PROMPT_SHA256,
        rubric_version=RUBRIC_VERSION,
        modalities=("image", "audio"),
    )


def report_from_findings(
    findings: Sequence[QcFinding],
    *,
    bundle: ExplainerRenderBundle,
    artifact: ReviewedArtifact,
    sampled: Mapping[str, Sequence[int]],
    created_at: str | None = None,
) -> ReviewReport:
    """Failed checks become blockers, unknown ones notes; passes only widen the coverage."""
    fps = bundle.timeline.fps
    scenes = {s.scene_id: s for s in bundle.spec.scenes}
    ends = {s.scene_id: _ms(s.start_frame + s.duration_frames, fps) for s in bundle.timeline.scenes}
    reviewed: list[ReviewFinding] = []
    for i, finding in enumerate(findings):
        if finding.passed is True:
            continue
        failed = finding.passed is False
        category: ReviewCategory = CATEGORY_OF[finding.check]
        if finding.check == "ocr_text" and _is_source(scenes[finding.scene_id]):
            category = "highlight_correctness"
        end_ms = min(ends[finding.scene_id], finding.at_ms + max(1, round(1000 / fps)))
        reviewed.append(
            ReviewFinding(
                finding_id="fnd_" + _digest(i, finding)[:16],
                scene_id=finding.scene_id,
                interval=TimeInterval(start_ms=finding.at_ms, end_ms=max(finding.at_ms, end_ms)),
                category=category,
                severity="blocker" if failed else "note",
                observed=_clip(_observed(finding)),
                evidence=_clip(finding.evidence),
                proposed_repair=propose_repair(finding, bundle) if failed else None,
                disposition="fail" if failed else "uncertain",
                confidence=1.0 if failed else None,
            )
        )
    coverage = tuple(
        CoverageSpan(
            scene_id=s.scene_id,
            beat_ids=tuple(b.beat_id for b in scenes[s.scene_id].beats),
            interval=TimeInterval(start_ms=_ms(s.start_frame, fps), end_ms=ends[s.scene_id]),
            sampled_ms=tuple(sorted(set(sampled.get(s.scene_id) or [_ms(s.start_frame, fps)]))),
        )
        for s in bundle.timeline.scenes
    )
    ran = {f.check for f in findings}
    identity = reviewer_identity()
    body = canonical_dumps(
        [artifact.sha256, identity.model_dump(mode="json"), [f.model_dump() for f in reviewed]]
    )
    return ReviewReport(
        report_id="rev_" + hashlib.sha256(body.encode()).hexdigest()[:16],
        artifact=artifact,
        timeline_id=bundle.timeline.timeline_id,
        reviewer=identity,
        coverage=coverage,
        findings=tuple(reviewed),
        categories=_categories(ran, any(_is_source(s) for s in scenes.values())),
        disposition=_disposition(reviewed),
        authority="blocking",
        created_at=created_at or datetime.now(UTC).isoformat(timespec="seconds"),
    )


def propose_repair(finding: QcFinding, bundle: ExplainerRenderBundle) -> TypedRepair | None:
    """A typed repair only where the finding makes the fix unambiguous."""
    scene = next((s for s in bundle.spec.scenes if s.scene_id == finding.scene_id), None)
    if scene is None:
        return None
    if finding.check == "contract" and finding.evidence.startswith("[timing]"):
        return HoldRepair(repair="hold", beat_id=scene.beats[-1].beat_id, duration_class="long")
    if finding.check not in WORDING_CHECKS or finding.entity_id is None:
        return None
    entity = next((e for e in bundle.spec.entities if e.entity_id == finding.entity_id), None)
    compiled = next(s for s in bundle.timeline.scenes if s.scene_id == finding.scene_id)
    drawn = next((b.text for b in compiled.boxes if b.entity_id == finding.entity_id), None)
    if (
        entity is not None
        and entity.short_label
        and drawn != entity.short_label
        and scene.template.template in LABEL_TEMPLATES
    ):
        return LabelWordingRepair(
            repair="label_wording", entity_id=entity.entity_id, short_label=entity.short_label
        )
    after = _split_point(scene, finding.entity_id)
    if after is None:
        return None
    return SplitSceneRepair(repair="split_scene", scene_id=scene.scene_id, after_beat_id=after)


def review_cache_key(
    *,
    media_sha256: str,
    evidence_hash: str,
    model_id: str,
    model_revision: str,
    rubric_version: str,
    prompt_sha256: str,
) -> str:
    """Same media, evidence and reviewer means the same verdict; anything else is a new review."""
    payload = canonical_dumps(
        {
            "media_sha256": media_sha256,
            "evidence_hash": evidence_hash,
            "model_id": model_id,
            "model_revision": model_revision,
            "rubric_version": rubric_version,
            "prompt_sha256": prompt_sha256,
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class ReviewCache:
    """One ReviewReport JSON per key under root; put is atomic, a reader never sees half a file."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> ReviewReport | None:
        path = self.path(key)
        if not path.is_file():
            return None
        return ReviewReport.model_validate_json(path.read_text(encoding="utf-8"))

    def put(self, key: str, report: ReviewReport) -> Path:
        path = self.path(key)
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(report.canonical_json() + "\n", encoding="utf-8")
        tmp.replace(path)
        return path

    @staticmethod
    def covers(report: ReviewReport, mp4: Path) -> bool:
        """The report speaks for this file only if the bytes it reviewed are the bytes on disk."""
        return mp4.is_file() and report.artifact.sha256 == file_sha256(mp4)


def _is_source(scene: Scene) -> bool:
    return scene.template.template == "source_document"


def _split_point(scene: Scene, entity_id: str) -> str | None:
    """The beat revealing the entity, or the one before it when that is the last beat."""
    beats = scene.beats
    index = 0
    for k, beat in enumerate(beats):
        if any(
            a.action == "reveal" and entity_id in getattr(a, "targets", ()) for a in beat.actions
        ):
            index = k
            break
    if index == len(beats) - 1:
        index -= 1
    return beats[index].beat_id if index >= 0 else None


def _disposition(findings: Sequence[ReviewFinding]) -> Disposition:
    if any(f.disposition == "fail" for f in findings):
        return "fail"
    if any(f.disposition == "uncertain" for f in findings):
        return "uncertain"
    return "pass"


def _categories(ran: set[str], has_source: bool) -> tuple[CategoryCoverage, ...]:
    covered: dict[ReviewCategory, list[str]] = {}
    for check in CHECKS:
        if check in ran:
            covered.setdefault(CATEGORY_OF[check], []).append(check)
    if has_source and "ocr_text" in ran:
        covered["highlight_correctness"] = ["ocr_text"]
    return tuple(
        CategoryCoverage(category=c, covered_by=", ".join(covered[c])[:160])
        for c in CATEGORY_ORDER
        if c in covered
    )


def _observed(finding: QcFinding) -> str:
    state = "failed" if finding.passed is False else "could not be measured"
    where = f" on {finding.entity_id}" if finding.entity_id else ""
    if finding.measured is not None and finding.passed is False:
        return f"{finding.check} {state}{where}: {finding.measured:g} against {finding.threshold:g}"
    return f"{finding.check} {state}{where}"


def _digest(index: int, finding: QcFinding) -> str:
    key = f"{index}:{finding.check}:{finding.scene_id}:{finding.entity_id}:{finding.at_ms}"
    return hashlib.sha256(key.encode()).hexdigest()


def _clip(text: str) -> str:
    text = text or "-"
    return text if len(text) <= TEXT_LIMIT else text[: TEXT_LIMIT - 1] + "…"


def _ms(frame: int, fps: int) -> int:
    return frame * 1000 // fps
