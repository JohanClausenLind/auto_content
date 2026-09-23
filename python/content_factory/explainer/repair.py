"""Typed repairs on the spec, and the bounded loop: QC, repair, recompile, rerender, QC again."""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from content_factory.explainer.errors import ContractIssue, EpisodeInvalidError
from content_factory.explainer.qc import QcFinding
from content_factory.explainer.qc_checks import sampled_ms
from content_factory.explainer.review import report_from_findings
from content_factory.explainer.timing import ESTIMATED_TOKEN_MS
from content_factory.schemas.base import file_sha256
from content_factory.schemas.explainer import (
    Beat,
    CueOffsetRepair,
    EvidencePack,
    ExplainerRenderBundle,
    HoldAction,
    HoldRepair,
    LabelWordingRepair,
    LayoutChoiceRepair,
    QuoteAction,
    ReviewedArtifact,
    ReviewReport,
    Scene,
    ScriptPlan,
    SourcePassageRepair,
    SplitSceneRepair,
    TakeSelectionRepair,
    TypedRepair,
    VisualSpec,
)

Compile = Callable[[EvidencePack, ScriptPlan, VisualSpec], ExplainerRenderBundle]
# render(bundle, scene_ids) returns the whole episode's mp4; None means every scene is new.
Render = Callable[[ExplainerRenderBundle, Sequence[str] | None], Path]
Qc = Callable[[ExplainerRenderBundle, Path], Sequence[QcFinding]]
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepairOutcome:
    """What the loop ended with; blocked findings still fail and nothing here marks them ready."""

    spec: VisualSpec
    script: ScriptPlan
    rounds: int
    resolved: tuple[str, ...]
    blocked: tuple[QcFinding, ...]
    reports: tuple[ReviewReport, ...]


def apply_repair(
    spec: VisualSpec, script: ScriptPlan, repair: TypedRepair
) -> tuple[VisualSpec, ScriptPlan]:
    """The repaired spec, re-validated; the script never changes, a take repair changes nothing."""
    if isinstance(repair, CueOffsetRepair):
        spec = _cue_offset(spec, script, repair)
    elif isinstance(repair, LabelWordingRepair):
        spec = _label_wording(spec, repair)
    elif isinstance(repair, LayoutChoiceRepair):
        layout = repair.layout
        spec = _map_scene(spec, repair.scene_id, lambda s: s.model_copy(update={"layout": layout}))
    elif isinstance(repair, HoldRepair):
        spec = _map_beat(spec, repair.beat_id, lambda b: _hold(b, repair.duration_class))
    elif isinstance(repair, TakeSelectionRepair):
        log.info("take_selection for %s is a narration repair; spec unchanged", repair.segment_id)
    elif isinstance(repair, SourcePassageRepair):
        spec = _source_passage(spec, repair)
    elif isinstance(repair, SplitSceneRepair):
        spec = _split_scene(spec, repair)
    return VisualSpec.model_validate(spec.model_dump()), script


def split_scene_id(scene_id: str, after_beat_id: str) -> str:
    return "scn_" + hashlib.sha256(f"{scene_id}/{after_beat_id}".encode()).hexdigest()[:12]


def finding_key(finding: QcFinding) -> str:
    return f"{finding.check}:{finding.scene_id}:{finding.entity_id or '-'}"


def repair_loop(
    pack: EvidencePack,
    script: ScriptPlan,
    spec: VisualSpec,
    *,
    compile: Compile,
    render: Render,
    qc: Qc,
    max_rounds: int = 2,
    budget_rounds: int = 6,
) -> RepairOutcome:
    """Repair what QC can name a fix for, at most max_rounds per scene and budget_rounds in all."""
    bundle = compile(pack, script, spec)
    mp4 = render(bundle, None)
    findings = list(qc(bundle, mp4))
    reports = [_report(bundle, mp4, findings)]
    failed = _failed(findings)
    resolved: set[str] = set()
    uncompilable: list[QcFinding] = []
    per_scene: Counter[str] = Counter()
    rounds = 0
    while failed and rounds < budget_rounds:
        repairs = _repairs(reports[-1], per_scene, max_rounds)
        if not repairs:
            break
        candidate, touched = _apply_all(spec, script, repairs, per_scene)
        if not touched:
            break
        rounds += 1
        try:
            bundle = compile(pack, script, candidate)
        except EpisodeInvalidError as error:
            # The repaired spec does not compile: the last good spec stands, its issues are blocked.
            uncompilable = [_issue_finding(bundle, issue) for issue in error.issues]
            break
        spec = candidate
        mp4 = render(bundle, _affected(spec, touched))
        findings = list(qc(bundle, mp4))
        reports.append(_report(bundle, mp4, findings))
        now = _failed(findings)
        resolved.update(failed.keys() - now.keys())
        failed = now
    blocked = (*failed.values(), *uncompilable)
    return RepairOutcome(spec, script, rounds, tuple(sorted(resolved)), blocked, tuple(reports))


def _failed(findings: Sequence[QcFinding]) -> dict[str, QcFinding]:
    return {finding_key(f): f for f in findings if f.passed is False}


def _repairs(
    report: ReviewReport, per_scene: Counter[str], max_rounds: int
) -> list[tuple[TypedRepair, str]]:
    seen: set[str] = set()
    chosen: list[tuple[TypedRepair, str]] = []
    for finding in report.findings:
        repair = finding.proposed_repair
        if repair is None or finding.disposition != "fail":
            continue
        if per_scene[finding.scene_id] >= max_rounds:
            continue
        key = repair.model_dump_json()
        if key not in seen:
            seen.add(key)
            chosen.append((repair, finding.scene_id))
    return chosen


def _apply_all(
    spec: VisualSpec,
    script: ScriptPlan,
    repairs: Sequence[tuple[TypedRepair, str]],
    per_scene: Counter[str],
) -> tuple[VisualSpec, set[str]]:
    touched: set[str] = set()
    for repair, scene_id in repairs:
        try:
            candidate, _ = apply_repair(spec, script, repair)
        except ValueError as why:
            log.info("repair %s skipped: %s", repair.repair, why)
            continue
        if candidate == spec:
            continue
        spec = candidate
        touched.add(scene_id)
        per_scene[scene_id] += 1
        if isinstance(repair, SplitSceneRepair):
            touched.add(split_scene_id(repair.scene_id, repair.after_beat_id))
    return spec, touched


def _affected(spec: VisualSpec, touched: set[str]) -> list[str]:
    """Touched scenes and their neighbours, in spec order: a boundary moves both sides."""
    ids = [s.scene_id for s in spec.scenes]
    wanted: set[int] = set()
    for i, scene_id in enumerate(ids):
        if scene_id in touched:
            wanted.update({i - 1, i, i + 1})
    return [ids[i] for i in sorted(wanted) if 0 <= i < len(ids)]


def _report(
    bundle: ExplainerRenderBundle, mp4: Path, findings: Sequence[QcFinding]
) -> ReviewReport:
    artifact = ReviewedArtifact(kind="mp4", sha256=file_sha256(mp4))
    return report_from_findings(
        findings, bundle=bundle, artifact=artifact, sampled=sampled_ms(bundle)
    )


def _issue_finding(bundle: ExplainerRenderBundle, issue: ContractIssue) -> QcFinding:
    scenes = {s.scene_id for s in bundle.timeline.scenes}
    scene_id = next((i for i in issue.ids if i in scenes), bundle.timeline.scenes[0].scene_id)
    entities = {e.entity_id for e in bundle.spec.entities}
    entity_id = next((i for i in issue.ids if i in entities), None)
    return QcFinding("contract", scene_id, entity_id, 0, 1.0, 0.0, False, str(issue))


def _cue_offset(spec: VisualSpec, script: ScriptPlan, repair: CueOffsetRepair) -> VisualSpec:
    def shift(beat: Beat) -> Beat:
        try:
            count = len(script.segment(beat.cue.segment_id).tokens)
        except KeyError as missing:
            msg = f"beat {beat.beat_id} cues unknown segment {missing}"
            raise ValueError(msg) from None
        step = max(1, round(abs(repair.offset_ms) / ESTIMATED_TOKEN_MS))
        tokens = step if repair.offset_ms > 0 else -step
        start = min(count - 1, max(0, beat.cue.token_start + tokens))
        end = min(count - 1, max(start, beat.cue.token_end + tokens))
        return _with_cue(beat, token_start=start, token_end=end)

    if repair.offset_ms == 0:
        return spec
    return _map_beat(spec, repair.beat_id, shift)


def _label_wording(spec: VisualSpec, repair: LabelWordingRepair) -> VisualSpec:
    if all(e.entity_id != repair.entity_id for e in spec.entities):
        msg = f"unknown entity {repair.entity_id}"
        raise ValueError(msg)
    entities = tuple(
        e.model_copy(update={"short_label": repair.short_label})
        if e.entity_id == repair.entity_id
        else e
        for e in spec.entities
    )
    return spec.model_copy(update={"entities": entities})


def _hold(beat: Beat, duration_class: str) -> Beat:
    """The beat holds at the new duration class; a beat without a hold action gains one."""
    actions = beat.actions
    if all(a.action != "hold" for a in actions):
        actions = (*actions, HoldAction(action="hold"))
    return _with_cue(beat, duration_class=duration_class).model_copy(update={"actions": actions})


def _source_passage(spec: VisualSpec, repair: SourcePassageRepair) -> VisualSpec:
    if _scene(spec, repair.scene_id).template.template != "source_document":
        msg = f"scene {repair.scene_id} shows no source document; nothing to re-quote"
        raise ValueError(msg)

    def swap(scene: Scene) -> Scene:
        beats = tuple(
            beat.model_copy(
                update={
                    "actions": tuple(
                        a.model_copy(update={"quote_id": repair.quote_id})
                        if isinstance(a, QuoteAction)
                        else a
                        for a in beat.actions
                    )
                }
            )
            for beat in scene.beats
        )
        return scene.model_copy(update={"beats": beats})

    return _map_scene(spec, repair.scene_id, swap)


def _split_scene(spec: VisualSpec, repair: SplitSceneRepair) -> VisualSpec:
    scene = _scene(spec, repair.scene_id)
    if scene.template.template == "source_document":
        msg = f"scene {scene.scene_id} shows a source: its page state cannot move to a new scene"
        raise ValueError(msg)
    ids = [b.beat_id for b in scene.beats]
    if repair.after_beat_id not in ids:
        msg = f"scene {scene.scene_id} has no beat {repair.after_beat_id}"
        raise ValueError(msg)
    cut = ids.index(repair.after_beat_id) + 1
    if cut >= len(ids):
        msg = f"beat {repair.after_beat_id} is the last beat of {scene.scene_id}; nothing to split"
        raise ValueError(msg)
    visible: dict[str, None] = dict.fromkeys(scene.initial_visible)
    for beat in scene.beats[:cut]:
        for action in beat.actions:
            targets = getattr(action, "targets", ())
            if action.action == "reveal":
                visible.update(dict.fromkeys(targets))
            elif action.action == "hide":
                for target in targets:
                    visible.pop(target, None)
    head = scene.model_copy(update={"beats": scene.beats[:cut]})
    tail = scene.model_copy(
        update={
            "scene_id": split_scene_id(scene.scene_id, repair.after_beat_id),
            "beats": scene.beats[cut:],
            "initial_visible": tuple(visible),
        }
    )
    return spec.model_copy(update={"scenes": _insert(spec.scenes, scene.scene_id, head, tail)})


def _insert(
    scenes: tuple[Scene, ...], scene_id: str, head: Scene, tail: Scene
) -> tuple[Scene, ...]:
    out: list[Scene] = []
    for s in scenes:
        if s.scene_id == scene_id:
            out.extend((head, tail))
        else:
            out.append(s)
    return tuple(out)


def _scene(spec: VisualSpec, scene_id: str) -> Scene:
    for scene in spec.scenes:
        if scene.scene_id == scene_id:
            return scene
    msg = f"unknown scene {scene_id}"
    raise ValueError(msg)


def _map_scene(spec: VisualSpec, scene_id: str, change: Callable[[Scene], Scene]) -> VisualSpec:
    _scene(spec, scene_id)
    scenes = tuple(change(s) if s.scene_id == scene_id else s for s in spec.scenes)
    return spec.model_copy(update={"scenes": scenes})


def _map_beat(spec: VisualSpec, beat_id: str, change: Callable[[Beat], Beat]) -> VisualSpec:
    def change_beats(scene: Scene) -> Scene:
        beats = tuple(change(b) if b.beat_id == beat_id else b for b in scene.beats)
        return scene.model_copy(update={"beats": beats})

    for scene in spec.scenes:
        if any(b.beat_id == beat_id for b in scene.beats):
            return _map_scene(spec, scene.scene_id, change_beats)
    msg = f"unknown beat {beat_id}"
    raise ValueError(msg)


def _with_cue(beat: Beat, **fields: object) -> Beat:
    return beat.model_copy(update={"cue": beat.cue.model_copy(update=fields)})
