"""The compiler boundary: every cross-record check on one explainer episode, each one actionable."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from content_factory.explainer.errors import (
    ContractIssue,
    EpisodeInvalidError,
    explain_validation_error,
)
from content_factory.schemas.explainer import (
    AnnotateAction,
    AssetRef,
    Beat,
    ChartTemplate,
    EvidenceDataset,
    EvidencePack,
    NarrationManifest,
    QuoteAction,
    Scene,
    ScriptPlan,
    ScriptSegment,
    ScrollToAction,
    SourceCaptureManifest,
    SourceDocumentTemplate,
    TextTemplate,
    VisualSpec,
)

# Actions that work on something already on screen; reveal, hide and highlight have their own rules.
NEEDS_VISIBLE = frozenset(
    {"compare", "focus", "isolate", "zoom_to", "pan_to", "trace", "draw", "annotate", "flow"}
)
NEEDS_SHOWN = frozenset({"scroll_to", "focus_passage", "highlight_quote", "zoom_to", "pan_to"})


def load_or_explain[T: BaseModel](
    model: type[T], payload: Mapping[str, Any]
) -> T | list[ContractIssue]:
    """Validate a payload; pydantic's error list comes back as issues instead of an exception."""
    try:
        return model.model_validate(dict(payload))
    except ValidationError as error:
        return explain_validation_error(error, model)


def check_bindings(
    pack: EvidencePack,
    script: ScriptPlan,
    spec: VisualSpec,
    narration: NarrationManifest | None = None,
) -> list[ContractIssue]:
    """The pack is frozen and every record is bound to the exact content it was planned against."""
    issues: list[ContractIssue] = []
    if pack.frozen_at is None:
        issues.append(
            ContractIssue(
                kind="stale_binding",
                where="EvidencePack.frozen_at",
                message=f"pack {pack.pack_id} is not frozen.",
                fix="freeze the pack (EvidencePack.frozen(at)) before scripting.",
                ids=(pack.pack_id,),
            )
        )
    pack_hash = pack.pack_hash()
    script_hash = script.script_hash()
    bindings = [
        ("ScriptPlan.pack_hash", script.script_id, script.pack_hash, pack_hash, "evidence pack"),
        ("VisualSpec.script_hash", spec.spec_id, spec.script_hash, script_hash, "script"),
        ("VisualSpec.pack_hash", spec.spec_id, spec.pack_hash, pack_hash, "evidence pack"),
    ]
    if narration is not None:
        bindings.append(
            (
                "NarrationManifest.script_hash",
                narration.manifest_id,
                narration.script_hash,
                script_hash,
                "script",
            )
        )
    for where, record_id, bound, current, record in bindings:
        if bound != current:
            issues.append(
                ContractIssue(
                    kind="stale_binding",
                    where=where,
                    message=(
                        f"{record_id} is bound to {bound[:12]} but the current {record} "
                        f"hashes to {current[:12]}."
                    ),
                    fix=f"re-plan against the current {record}.",
                    ids=(record_id, bound, current),
                )
            )
    return issues


def check_references(
    pack: EvidencePack,
    script: ScriptPlan,
    spec: VisualSpec,
    manifests: Iterable[SourceCaptureManifest] = (),
) -> list[ContractIssue]:
    """Every id the script or spec points at exists in the pack, script or capture manifests."""
    known = _Known(
        claims=frozenset(c.claim_id for c in pack.claims),
        sources=frozenset(s.source_id for s in pack.sources),
        datasets={d.dataset_id: d for d in pack.datasets},
        captures={m.capture_id: m for m in manifests},
        segments={s.segment_id: s for s in script.segments},
        assets={a.asset_id: a for a in spec.assets},
    )
    issues: list[ContractIssue] = []
    for i, promise in enumerate(script.promises):
        for cid in promise.claim_ids:
            if cid not in known.claims:
                where = f"ScriptPlan.promises[{i}].claim_ids"
                issues.append(_unknown(where, "script", script.script_id, "claim", cid))
    for i, segment in enumerate(script.segments):
        for cid in segment.claim_ids:
            if cid not in known.claims:
                where = f"ScriptPlan.segments[{i}].claim_ids"
                issues.append(_unknown(where, "segment", segment.segment_id, "claim", cid))
    for i, entity in enumerate(spec.entities):
        for cid in entity.claim_ids:
            if cid not in known.claims:
                where = f"VisualSpec.entities[{i}].claim_ids"
                issues.append(_unknown(where, "entity", entity.entity_id, "claim", cid))
    for i, asset in enumerate(spec.assets):
        if asset.dataset_id is not None and asset.dataset_id not in known.datasets:
            where = f"VisualSpec.assets[{i}].dataset_id"
            issues.append(_unknown(where, "asset", asset.asset_id, "dataset", asset.dataset_id))
        if asset.capture_id is not None and asset.capture_id not in known.captures:
            where = f"VisualSpec.assets[{i}].capture_id"
            holder = "the capture manifests"
            issues.append(
                _unknown(where, "asset", asset.asset_id, "capture", asset.capture_id, holder)
            )
    for i, scene in enumerate(spec.scenes):
        issues += _scene_references(f"VisualSpec.scenes[{i}]", scene, known)
    return issues


def check_narration_manifest(
    script: ScriptPlan, narration: NarrationManifest
) -> list[ContractIssue]:
    """Every segment is spoken by a take; every aligned word lands inside its segment's tokens."""
    issues: list[ContractIssue] = []
    segments = {s.segment_id: s for s in script.segments}
    spoken = {sid for take in narration.takes for sid in take.segment_ids}
    for segment in script.segments:
        if segment.segment_id not in spoken:
            issues.append(
                ContractIssue(
                    kind="invalid_reference",
                    where="NarrationManifest.takes",
                    message=(
                        f"segment {segment.segment_id} of script {script.script_id} has no take "
                        f"in manifest {narration.manifest_id}."
                    ),
                    fix=f"record or synthesize segment {segment.segment_id}.",
                    ids=(narration.manifest_id, segment.segment_id),
                )
            )
    sponsor_id = script.sponsor.segment_id if script.sponsor is not None else None
    for i, take in enumerate(narration.takes):
        for sid in take.segment_ids:
            if sid not in segments and sid != sponsor_id:
                where = f"NarrationManifest.takes[{i}].segment_ids"
                issues.append(_unknown(where, "take", take.take_id, "segment", sid, "the script"))
        if take.alignment is None:
            continue
        for k, word in enumerate(take.alignment.words):
            segment = segments.get(word.segment_id)
            if segment is None or word.token_index < len(segment.tokens):
                continue
            count = len(segment.tokens)
            issues.append(
                ContractIssue(
                    kind="invalid_reference",
                    where=f"NarrationManifest.takes[{i}].alignment.words[{k}]",
                    message=(
                        f"take {take.take_id} times token {word.token_index} of segment "
                        f"{segment.segment_id}, which has {count} tokens (last index {count - 1})."
                    ),
                    fix="re-align the take against the locked script.",
                    ids=(take.take_id, segment.segment_id),
                )
            )
    return issues


def check_state(spec: VisualSpec) -> list[ContractIssue]:
    """Simulate each scene's beats so no action presumes a state the screen is not in."""
    issues: list[ContractIssue] = []
    for i, scene in enumerate(spec.scenes):
        issues += _SceneWalk(f"VisualSpec.scenes[{i}]", scene).run()
    return issues


def check_episode(
    pack: EvidencePack,
    script: ScriptPlan,
    spec: VisualSpec,
    manifests: Iterable[SourceCaptureManifest] = (),
    narration: NarrationManifest | None = None,
) -> list[ContractIssue]:
    """Every check the compiler runs before it touches an episode, as one list."""
    # Imported here so this module loads even when the evidence checks do not.
    from content_factory.explainer.evidence import (
        check_calculations,
        check_datasets,
        check_narration,
        check_spec_text_numbers,
    )

    issues = check_bindings(pack, script, spec, narration)
    issues += check_references(pack, script, spec, manifests)
    if narration is not None:
        issues += check_narration_manifest(script, narration)
    issues += check_state(spec)
    issues += check_calculations(pack)
    issues += check_datasets(pack)
    issues += check_narration(pack, script)
    issues += check_spec_text_numbers(pack, spec)
    return issues


def validate_episode(
    pack: EvidencePack,
    script: ScriptPlan,
    spec: VisualSpec,
    manifests: Iterable[SourceCaptureManifest] = (),
    narration: NarrationManifest | None = None,
) -> None:
    """Raise EpisodeInvalidError carrying every issue, or return when the episode is sound."""
    issues = check_episode(pack, script, spec, manifests, narration)
    if issues:
        raise EpisodeInvalidError(issues)


@dataclass(frozen=True)
class _Known:
    claims: frozenset[str]
    sources: frozenset[str]
    datasets: dict[str, EvidenceDataset]
    captures: dict[str, SourceCaptureManifest]
    segments: dict[str, ScriptSegment]
    assets: dict[str, AssetRef]


def _unknown(
    where: str, referrer: str, referrer_id: str, kind: str, missing: str, holder: str = "the pack"
) -> ContractIssue:
    return ContractIssue(
        kind="invalid_reference",
        where=where,
        message=f"{referrer} {referrer_id} references unknown {kind} {missing}.",
        fix=f"point at an existing {kind} or add it to {holder}.",
        ids=(referrer_id, missing),
    )


def _scene_references(where: str, scene: Scene, known: _Known) -> list[ContractIssue]:
    sid = scene.scene_id
    issues: list[ContractIssue] = []
    for cid in scene.claim_ids:
        if cid not in known.claims:
            issues.append(_unknown(f"{where}.claim_ids", "scene", sid, "claim", cid))
    for source_id in scene.source_ids:
        if source_id not in known.sources:
            issues.append(_unknown(f"{where}.source_ids", "scene", sid, "source", source_id))
    issues += _template_references(f"{where}.template", scene, known)
    manifest = _capture_for(scene, known)
    sections = {s.section_id for s in manifest.sections} if manifest else set()
    quotes = {q.quote_id for q in manifest.quotes} if manifest else set()
    holder = f"capture {manifest.capture_id}" if manifest else ""
    for j, beat in enumerate(scene.beats):
        issues += _cue_references(f"{where}.beats[{j}].cue", scene, beat, known)
        for k, action in enumerate(beat.actions):
            at = f"{where}.beats[{j}].actions[{k}]"
            if isinstance(action, AnnotateAction):
                if action.claim_id is not None and action.claim_id not in known.claims:
                    issues.append(
                        _unknown(f"{at}.claim_id", "scene", sid, "claim", action.claim_id)
                    )
            elif isinstance(action, ScrollToAction) and manifest is not None:
                if action.section_id not in sections:
                    at = f"{at}.section_id"
                    issues.append(_unknown(at, "scene", sid, "section", action.section_id, holder))
            elif isinstance(action, QuoteAction) and manifest is not None:
                if action.quote_id not in quotes:
                    at = f"{at}.quote_id"
                    issues.append(_unknown(at, "scene", sid, "quote", action.quote_id, holder))
    return issues


def _cue_references(where: str, scene: Scene, beat: Beat, known: _Known) -> list[ContractIssue]:
    cue = beat.cue
    segment = known.segments.get(cue.segment_id)
    if segment is None:
        holder = "the script"
        return [_unknown(where, "beat", beat.beat_id, "segment", cue.segment_id, holder)]
    count = len(segment.tokens)
    if cue.token_end < count:
        return []
    return [
        ContractIssue(
            kind="invalid_reference",
            where=where,
            message=(
                f"scene {scene.scene_id} beat {beat.beat_id} cues token {cue.token_end} of "
                f"segment {segment.segment_id}, which has {count} tokens (last index {count - 1})."
            ),
            fix=f"keep token_start and token_end within 0..{count - 1}.",
            ids=(scene.scene_id, beat.beat_id, segment.segment_id),
        )
    ]


def _template_references(where: str, scene: Scene, known: _Known) -> list[ContractIssue]:
    template = scene.template
    sid = scene.scene_id
    if isinstance(template, TextTemplate):
        return [
            _unknown(
                f"{where}.items[{k}].claim_id", "text item", item.entity_id, "claim", item.claim_id
            )
            for k, item in enumerate(template.items)
            if item.claim_id is not None and item.claim_id not in known.claims
        ]
    if isinstance(template, SourceDocumentTemplate):
        asset = known.assets.get(template.capture_asset_id)
        if asset is not None and asset.capture_id is None:
            return [_wrong_asset_kind(f"{where}.capture_asset_id", sid, asset, "capture")]
        manifest = _capture_for(scene, known)
        if manifest is None or template.initial_section_id in {
            s.section_id for s in manifest.sections
        }:
            return []
        holder = f"capture {manifest.capture_id}"
        at = f"{where}.initial_section_id"
        return [_unknown(at, "scene", sid, "section", template.initial_section_id, holder)]
    if isinstance(template, ChartTemplate):
        return _chart_references(where, sid, template, known)
    return []


def _chart_references(
    where: str, sid: str, chart: ChartTemplate, known: _Known
) -> list[ContractIssue]:
    asset = known.assets.get(chart.dataset_asset_id)
    if asset is not None and asset.dataset_id is None:
        return [_wrong_asset_kind(f"{where}.dataset_asset_id", sid, asset, "dataset")]
    dataset = known.datasets.get(asset.dataset_id) if asset and asset.dataset_id else None
    if dataset is None:
        return []
    columns = [c.name for c in dataset.columns]
    issues: list[ContractIssue] = []
    encodings = (("x.field", chart.x.field), ("y.field", chart.y.field))
    for label, name in (*encodings, ("series_field", chart.series_field)):
        if name is not None and name not in columns:
            issues.append(
                ContractIssue(
                    kind="invalid_reference",
                    where=f"{where}.{label}",
                    message=(
                        f"scene {sid} chart {label} {name!r} is not a column of dataset "
                        f"{dataset.dataset_id}; columns: {', '.join(columns)}."
                    ),
                    fix="use one of the dataset's columns or add the column to the dataset.",
                    ids=(sid, dataset.dataset_id, name),
                )
            )
    if chart.series_field is None or chart.series_field not in columns:
        return issues
    column = columns.index(chart.series_field)
    values = {str(row.values[column]) for row in dataset.rows}
    for k, binding in enumerate(chart.series):
        if binding.value in values:
            continue
        sample = ", ".join(sorted(values)[:6])
        issues.append(
            ContractIssue(
                kind="invalid_reference",
                where=f"{where}.series[{k}].value",
                message=(
                    f"scene {sid} binds series {binding.value!r} to {binding.entity_id}, but "
                    f"column {chart.series_field!r} of dataset {dataset.dataset_id} holds: "
                    f"{sample}."
                ),
                fix="bind a value that appears in the series column or add its rows.",
                ids=(sid, binding.entity_id, binding.value),
            )
        )
    return issues


def _wrong_asset_kind(where: str, sid: str, asset: AssetRef, wanted: str) -> ContractIssue:
    return ContractIssue(
        kind="invalid_reference",
        where=where,
        message=(
            f"scene {sid} uses asset {asset.asset_id} as a {wanted}, but its kind is {asset.kind}."
        ),
        fix=f"point at an asset of kind {wanted}.",
        ids=(sid, asset.asset_id),
    )


def _capture_for(scene: Scene, known: _Known) -> SourceCaptureManifest | None:
    if not isinstance(scene.template, SourceDocumentTemplate):
        return None
    asset = known.assets.get(scene.template.capture_asset_id)
    if asset is None or asset.capture_id is None:
        return None
    return known.captures.get(asset.capture_id)


@dataclass(frozen=True)
class _Step:
    where: str
    beat_id: str
    index: int
    action: str


class _SceneWalk:
    """The screen state of one scene while its beats run: visible, highlighted, shown, quoted."""

    def __init__(self, path: str, scene: Scene) -> None:
        self.scene = scene
        self.path = path
        self.visible: set[str] = set(scene.initial_visible)
        self.highlighted: set[str] = set()
        self.is_source = scene.template.template == "source_document"
        self.shown = False
        self.quote: str | None = None
        self.issues: list[ContractIssue] = []

    def run(self) -> list[ContractIssue]:
        for j, beat in enumerate(self.scene.beats):
            revealed: dict[str, int] = {}
            hidden: dict[str, int] = {}
            for k, action in enumerate(beat.actions):
                where = f"{self.path}.beats[{j}].actions[{k}]"
                step = _Step(where, beat.beat_id, k, action.action)
                self._apply(step, action, revealed, hidden)
        return self.issues

    def _apply(
        self, step: _Step, action: Any, revealed: dict[str, int], hidden: dict[str, int]
    ) -> None:
        targets: tuple[str, ...] = tuple(getattr(action, "targets", ()))
        if step.action == "reveal":
            for target in targets:
                if target in self.visible:
                    fix = "drop the reveal or hide it first."
                    self._issue(step, (target,), f"{target} is already visible", fix)
                if target in hidden:
                    problem = (
                        f"{target} is hidden by action {hidden[target]} and revealed by action "
                        f"{step.index}"
                    )
                    self._issue(step, (target,), problem, "keep one of the two in this beat.")
                self.visible.add(target)
                revealed[target] = step.index
        elif step.action == "hide":
            for target in targets:
                if target not in self.visible:
                    fix = "reveal it first or remove the hide."
                    self._issue(step, (target,), f"{target} is not visible", fix)
                if target in revealed:
                    problem = (
                        f"{target} is revealed by action {revealed[target]} and hidden by action "
                        f"{step.index}"
                    )
                    self._issue(step, (target,), problem, "keep one of the two in this beat.")
                self.visible.discard(target)
                self.highlighted.discard(target)
                hidden[target] = step.index
        elif step.action == "highlight":
            for target in targets:
                if target not in self.visible:
                    self._issue(step, (target,), f"{target} is not visible", "reveal it first.")
                self.highlighted.add(target)
        elif step.action == "clear_highlight":
            self._clear_highlight(step, targets)
        elif step.action == "show_source":
            if self.shown:
                fix = "drop the second show_source."
                self._issue(step, (), "the source is already shown", fix)
            self.shown = True
        elif self.is_source and step.action in NEEDS_SHOWN:
            named = targets or _source_ids(action)
            if not self.shown:
                problem = f"{step.action} of {', '.join(named)} comes before any show_source"
                self._issue(step, named, problem, "show_source first.")
            if step.action == "highlight_quote":
                self._highlight_quote(step, action.quote_id)
        elif step.action in NEEDS_VISIBLE:
            for target in targets:
                if target not in self.visible:
                    fix = "reveal it first or list it in initial_visible."
                    self._issue(step, (target,), f"{target} is not visible", fix)

    def _clear_highlight(self, step: _Step, targets: tuple[str, ...]) -> None:
        if not targets:
            if not self.highlighted and self.quote is None:
                self._issue(step, (), "nothing is highlighted", "drop the clear_highlight.")
            self.highlighted.clear()
            self.quote = None
            return
        for target in targets:
            if target not in self.highlighted:
                fix = "highlight it first or drop it from clear_highlight."
                self._issue(step, (target,), f"{target} is not highlighted", fix)
            self.highlighted.discard(target)

    def _highlight_quote(self, step: _Step, quote_id: str) -> None:
        if self.quote is not None and self.quote != quote_id:
            problem = f"highlights quote {quote_id} while {self.quote} is still highlighted"
            fix = "clear_highlight before highlighting another quote."
            self._issue(step, (self.quote, quote_id), problem, fix)
        self.quote = quote_id

    def _issue(self, step: _Step, targets: tuple[str, ...], problem: str, fix: str) -> None:
        self.issues.append(
            ContractIssue(
                kind="conflicting_state",
                where=step.where,
                message=(
                    f"scene {self.scene.scene_id} beat {step.beat_id} action {step.index} "
                    f"({step.action}): {problem}."
                ),
                fix=fix,
                ids=(self.scene.scene_id, step.beat_id, *targets),
            )
        )


def _source_ids(action: Any) -> tuple[str, ...]:
    if isinstance(action, ScrollToAction):
        return (action.section_id,)
    if isinstance(action, QuoteAction):
        return (action.quote_id,)
    return ()
