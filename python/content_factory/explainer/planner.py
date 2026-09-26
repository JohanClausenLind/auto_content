"""The runtime visual planner: one arc section per model call, validated into a VisualSpec."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import deque
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import httpx
from pydantic import Field, model_validator

from content_factory.explainer.errors import ContractIssue, EpisodeInvalidError
from content_factory.explainer.evidence import check_calculations, check_datasets, check_narration
from content_factory.explainer.tokens_gen import DESIGN_SYSTEM_VERSION
from content_factory.explainer.validate import check_episode, load_or_explain
from content_factory.schemas.base import OpaqueId, SchemaModel, Sha256Hex, canonical_dumps
from content_factory.schemas.explainer import (
    ACTION_TEMPLATES,
    AssetRef,
    CapabilityRequest,
    ChannelProfile,
    ChartTemplate,
    Claim,
    DiagramTemplate,
    Entity,
    EvidenceDataset,
    EvidencePack,
    Scene,
    ScriptPlan,
    Section,
    SourceCaptureManifest,
    SourceDocumentTemplate,
    TemplateName,
    TextTemplate,
    VisualSpec,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PLANNER_PROMPT_PATH = REPO_ROOT / "docs" / "PLANNER_PROMPT.md"
RENDERED_TEMPLATES: tuple[TemplateName, ...] = ("chart", "diagram", "text")
SECTION_KEYS = ("entities", "assets", "scenes", "capability_requests")
# Unmeasured ceiling: a truncated answer fails to parse and is re-asked, never silently cut.
SECTION_ANSWER_TOKENS = 8192
_PLACEHOLDER = re.compile(r"\{\{(context|schema_slice)\}\}")
_SPEC_INDEX = re.compile(r"VisualSpec\.(entities|assets)\[(\d+)\]")


class PlannerUnavailableError(RuntimeError):
    """The model server refused or failed the call; the message carries its answer."""


class PlannerPromptError(ValueError):
    """docs/PLANNER_PROMPT.md lost its `---` rule or one of its two placeholders."""


@dataclass(frozen=True)
class CapabilitySet:
    """Templates the renderer draws now, each with the actions ACTION_TEMPLATES allows on it."""

    actions: Mapping[str, tuple[str, ...]]

    @classmethod
    def for_episode(cls, manifests: Sequence[SourceCaptureManifest] = ()) -> CapabilitySet:
        """Source documents join only when there is a capture to show."""
        names: list[str] = list(RENDERED_TEMPLATES)
        if manifests:
            names.append("source_document")
        return cls(
            {name: tuple(a for a, on in ACTION_TEMPLATES.items() if name in on) for name in names}
        )

    @property
    def templates(self) -> tuple[str, ...]:
        return tuple(self.actions)

    def allowed_actions(self) -> frozenset[str]:
        return frozenset(a for actions in self.actions.values() for a in actions)

    def as_context(self) -> list[dict[str, Any]]:
        return [{"template": t, "actions": list(actions)} for t, actions in self.actions.items()]


class PlannedAsset(SchemaModel):
    """A pack dataset or a capture a section draws on; the planner hashes it, never the model."""

    asset_id: OpaqueId
    kind: Literal["dataset", "capture"]
    dataset_id: OpaqueId | None = None
    capture_id: OpaqueId | None = None

    @model_validator(mode="after")
    def _names_its_target(self) -> PlannedAsset:
        wanted, other = (
            (self.dataset_id, self.capture_id)
            if self.kind == "dataset"
            else (self.capture_id, self.dataset_id)
        )
        if wanted is None or other is not None:
            msg = f"asset {self.asset_id}: a {self.kind} asset names its {self.kind}_id only"
            raise ValueError(msg)
        return self


class SectionPlan(SchemaModel):
    """One section's answer: entities new to the registry, the assets it uses, its scenes."""

    entities: tuple[Entity, ...]
    assets: tuple[PlannedAsset, ...]
    scenes: tuple[Scene, ...] = Field(min_length=1)
    capability_requests: tuple[CapabilityRequest, ...]


class PlanCall(SchemaModel):
    """One model call: the section, the attempt, the prompt's hash, the raw answer, its issues."""

    section: Section
    attempt: int = Field(ge=0)
    prompt_sha256: Sha256Hex
    response: str
    issues: tuple[str, ...] = ()


class PlanRecord(SchemaModel):
    """A plan's trail, stored next to the spec: replayed from its answers, a seed is not enough."""

    script_id: OpaqueId
    script_hash: Sha256Hex
    pack_hash: Sha256Hex
    model_id: str = Field(min_length=1, max_length=160)
    prompt_doc_sha256: Sha256Hex
    max_tokens: int = Field(ge=1)
    retries: int = Field(ge=0)
    calls: tuple[PlanCall, ...] = ()
    issues_seen: tuple[str, ...] = ()
    spec_id: OpaqueId | None = None
    spec_hash: Sha256Hex | None = None


def schema_slice(caps: CapabilitySet) -> dict[str, Any]:
    """VisualSpec's JSON Schema holding only the templates and actions the renderer supports."""
    schema = copy.deepcopy(VisualSpec.model_json_schema())
    allowed = {"template": set(caps.templates), "action": set(caps.allowed_actions())}
    narrowed: dict[str, str] = {}
    _prune_unions(schema, allowed, narrowed)
    for name, prop in narrowed.items():
        spec = schema["$defs"][name]["properties"][prop]
        if "enum" in spec:
            spec["enum"] = [v for v in spec["enum"] if v in allowed[prop]]
    return _reachable(schema)


def section_schema(
    slice_: Mapping[str, Any],
    pack: EvidencePack,
    script: ScriptPlan,
    manifests: Sequence[SourceCaptureManifest] = (),
    *,
    section: str,
) -> dict[str, Any]:
    """The answer schema for one section: its scenes, its segments, assets picked from the pack."""
    defs = copy.deepcopy(dict(slice_["$defs"]))
    defs.pop("AssetRef", None)
    defs["Scene"]["properties"]["section"] = {
        "enum": [section],
        "title": "Section",
        "type": "string",
    }
    segment_ids = [s.segment_id for s in script.segments if s.section == section]
    defs["Cue"]["properties"]["segment_id"] = {
        "enum": segment_ids,
        "title": "Segment Id",
        "type": "string",
    }
    dataset_ids = [d.dataset_id for d in pack.datasets]
    capture_ids = [m.capture_id for m in manifests]
    assets: dict[str, Any] = {"title": "Assets", "type": "array", "maxItems": 0}
    if dataset_ids or capture_ids:
        defs["PlannedAsset"] = _planned_asset_schema(dataset_ids, capture_ids)
        assets = {"items": {"$ref": "#/$defs/PlannedAsset"}, "title": "Assets", "type": "array"}
    props = slice_["properties"]
    entities = {k: v for k, v in props["entities"].items() if k != "minItems"}
    requests = {k: v for k, v in props["capability_requests"].items() if k != "default"}
    schema: dict[str, Any] = {
        "title": "SectionPlan",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "entities": copy.deepcopy(entities),
            "assets": assets,
            "scenes": copy.deepcopy(dict(props["scenes"])),
            "capability_requests": copy.deepcopy(requests),
        },
        "required": list(SECTION_KEYS),
        "$defs": defs,
    }
    return _reachable(schema)


def planner_context(
    profile: ChannelProfile,
    pack: EvidencePack,
    script: ScriptPlan,
    manifests: Sequence[SourceCaptureManifest],
    *,
    section: str,
    entities: Sequence[Entity],
) -> dict[str, Any]:
    """What the model sees for one section and nothing else, so the call stays scoped."""
    segments = [s for s in script.segments if s.section == section]
    if not segments:
        msg = f"script {script.script_id} has no segments in section {section}"
        raise ValueError(msg)
    return {
        "channel": {"audience": profile.audience, "language": profile.episode_format.language},
        "section": section,
        "claims": [_claim_context(c) for c in pack.claims],
        "datasets": [_dataset_context(d) for d in pack.datasets],
        "segments": [
            {
                "segment_id": s.segment_id,
                "claim_ids": list(s.claim_ids),
                "tokens": [{"i": i, "token": t} for i, t in enumerate(s.tokens)],
            }
            for s in segments
        ],
        "captures": [_capture_context(m) for m in manifests],
        "entities": [
            {"entity_id": e.entity_id, "label": e.label, "kind": e.kind} for e in entities
        ],
        "capabilities": CapabilitySet.for_episode(manifests).as_context(),
    }


def prompt_template(path: Path = PLANNER_PROMPT_PATH) -> str:
    """The runtime prompt: the planner doc's text after its `---` rule, placeholders intact."""
    _, rule, body = path.read_text(encoding="utf-8").partition("\n---\n")
    if not rule:
        msg = f"{path} has no --- rule before the runtime prompt"
        raise PlannerPromptError(msg)
    missing = [p for p in ("{{context}}", "{{schema_slice}}") if p not in body]
    if missing:
        msg = f"{path} lacks {' and '.join(missing)} after the --- rule"
        raise PlannerPromptError(msg)
    return body.strip()


def render_prompt(
    context: Mapping[str, Any], slice_: Mapping[str, Any], path: Path = PLANNER_PROMPT_PATH
) -> str:
    """Fill both placeholders in one pass, so text inside the context is never substituted."""
    values = {"context": _pretty(context), "schema_slice": _pretty(slice_)}
    return _PLACEHOLDER.sub(lambda m: values[m[1]], prompt_template(path))


class PlannerModel(Protocol):
    model: str

    def complete(
        self, system: str, user: str, *, json_schema: dict[str, Any] | None, max_tokens: int
    ) -> str: ...


class OpenAICompatPlanner:
    """The planner's model seam over an OpenAI-style chat server; vLLM enforces the schema."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        timeout_s: float = 600.0,
        seed: int = 0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.seed = seed
        self._http = httpx.Client(timeout=timeout_s, transport=transport)

    def complete(
        self, system: str, user: str, *, json_schema: dict[str, Any] | None, max_tokens: int
    ) -> str:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "seed": self.seed,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "visual_plan", "schema": json_schema},
            }
        try:
            response = self._http.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.HTTPError as error:
            msg = f"planner server at {self.base_url} unreachable: {error}"
            raise PlannerUnavailableError(msg) from error
        if response.status_code != 200:
            msg = f"planner server answered {response.status_code}: {response.text[:300]}"
            raise PlannerUnavailableError(msg)
        return str(response.json()["choices"][0]["message"].get("content") or "")


@dataclass(frozen=True)
class PlannerCall:
    system: str
    user: str
    json_schema: dict[str, Any] | None
    max_tokens: int


class FakePlanner:
    """Queued answers in order, for tests and replays; every prompt is recorded."""

    def __init__(self, answers: Iterable[str], model: str = "fake-planner") -> None:
        self.model = model
        self.answers = deque(answers)
        self.calls: list[PlannerCall] = []

    @classmethod
    def replaying(cls, record: PlanRecord) -> FakePlanner:
        return cls([c.response for c in record.calls], model=record.model_id)

    def complete(
        self, system: str, user: str, *, json_schema: dict[str, Any] | None, max_tokens: int
    ) -> str:
        self.calls.append(PlannerCall(system, user, json_schema, max_tokens))
        if not self.answers:
            msg = f"FakePlanner has no answer queued for call {len(self.calls)}"
            raise PlannerUnavailableError(msg)
        return self.answers.popleft()


def plan_episode(
    profile: ChannelProfile,
    pack: EvidencePack,
    script: ScriptPlan,
    *,
    model: PlannerModel,
    manifests: Sequence[SourceCaptureManifest] = (),
    max_retries: int = 2,
    record_path: Path | None = None,
) -> VisualSpec:
    """Plan each arc section in script order, re-asking only failing sections, into a valid spec."""
    planner = _EpisodePlanner(profile, pack, script, tuple(manifests), model)
    try:
        return planner.run(max_retries)
    finally:
        if record_path is not None:
            record_path.parent.mkdir(parents=True, exist_ok=True)
            text = planner.record().model_dump_json(indent=2) + "\n"
            record_path.write_text(text, encoding="utf-8")


@dataclass
class _Call:
    section: str
    attempt: int
    prompt_sha256: str
    response: str
    issues: list[str] = field(default_factory=list)


@dataclass
class _Registry:
    """What earlier sections introduced: entities and assets by id, scene ids by section."""

    entities: dict[str, Entity] = field(default_factory=dict)
    assets: dict[str, AssetRef] = field(default_factory=dict)
    scenes: dict[str, str] = field(default_factory=dict)

    def absorb(self, section: str, plan: SectionPlan, assets: Sequence[AssetRef]) -> None:
        for entity in plan.entities:
            self.entities.setdefault(entity.entity_id, entity)
        for asset in assets:
            self.assets.setdefault(asset.asset_id, asset)
        for scene in plan.scenes:
            self.scenes.setdefault(scene.scene_id, section)


class _EpisodePlanner:
    def __init__(
        self,
        profile: ChannelProfile,
        pack: EvidencePack,
        script: ScriptPlan,
        manifests: tuple[SourceCaptureManifest, ...],
        model: PlannerModel,
    ) -> None:
        self.profile = profile
        self.pack = pack
        self.script = script
        self.manifests = manifests
        self.model = model
        self.sections: list[str] = list(dict.fromkeys(s.section for s in script.segments))
        self.caps = CapabilitySet.for_episode(manifests)
        self.slice = schema_slice(self.caps)
        self.datasets = {d.dataset_id: d for d in pack.datasets}
        self.captures = {m.capture_id: m for m in manifests}
        self.spec_id = f"vs_{script.script_hash()[:16]}"
        self.template_sha = hashlib.sha256(prompt_template().encode()).hexdigest()
        self.plans: dict[str, SectionPlan] = {}
        self.parse_issues: dict[str, list[ContractIssue]] = {}
        self.raw: dict[str, str] = {}
        self.issues: dict[str, list[ContractIssue]] = {s: [] for s in self.sections}
        self.calls: list[_Call] = []
        self.seen: dict[str, None] = {}
        self.spec: VisualSpec | None = None

    def run(self, max_retries: int) -> VisualSpec:
        inputs = _input_issues(self.pack, self.script)
        if inputs:
            raise EpisodeInvalidError(inputs)
        pending = list(self.sections)
        for attempt in range(max_retries + 1):
            for section in pending:
                if attempt:
                    # A fixed earlier section may have cleared this one; ask only with fresh issues.
                    self.issues[section] = self._validate()[section]
                    if not self.issues[section]:
                        continue
                self._ask(section, attempt)
            found = self._validate()
            for call in self.calls:
                if call.attempt == attempt:
                    call.issues = [str(i) for i in found[call.section]]
            self.issues = found
            self.seen.update((str(i), None) for s in self.sections for i in found[s])
            pending = [s for s in self.sections if found[s]]
            if not pending:
                return self._finish()
        raise EpisodeInvalidError([i for s in self.sections for i in self.issues[s]])

    def record(self) -> PlanRecord:
        return PlanRecord(
            script_id=self.script.script_id,
            script_hash=self.script.script_hash(),
            pack_hash=self.pack.pack_hash(),
            model_id=self.model.model,
            prompt_doc_sha256=self.template_sha,
            max_tokens=SECTION_ANSWER_TOKENS,
            retries=max((c.attempt for c in self.calls), default=0),
            calls=tuple(
                PlanCall(
                    section=cast(Section, c.section),
                    attempt=c.attempt,
                    prompt_sha256=c.prompt_sha256,
                    response=c.response,
                    issues=tuple(c.issues),
                )
                for c in self.calls
            ),
            issues_seen=tuple(self.seen),
            spec_id=self.spec.spec_id if self.spec else None,
            spec_hash=self.spec.spec_hash() if self.spec else None,
        )

    def _ask(self, section: str, attempt: int) -> None:
        registry = self._registry(until=section)
        context = planner_context(
            self.profile,
            self.pack,
            self.script,
            self.manifests,
            section=section,
            entities=list(registry.entities.values()),
        )
        schema = section_schema(self.slice, self.pack, self.script, self.manifests, section=section)
        system = render_prompt(context, schema)
        previous = self.raw.get(section) if attempt else None
        user = _user_message(section, previous, self.issues[section] if attempt else [])
        text = self.model.complete(
            system, user, json_schema=schema, max_tokens=SECTION_ANSWER_TOKENS
        )
        self.raw[section] = text
        self.calls.append(_Call(section, attempt, _prompt_sha256(system, user), text))
        parsed = _parse_section(text, section)
        if isinstance(parsed, SectionPlan):
            self.plans[section] = parsed
            self.parse_issues[section] = []
        else:
            self.plans.pop(section, None)
            self.parse_issues[section] = parsed

    def _registry(self, until: str | None = None) -> _Registry:
        registry = _Registry()
        for section in self.sections:
            if section == until:
                break
            plan = self.plans.get(section)
            if plan is not None:
                registry.absorb(section, plan, self._asset_refs(plan))
        return registry

    def _validate(self) -> dict[str, list[ContractIssue]]:
        registry = _Registry()
        found: dict[str, list[ContractIssue]] = {}
        for section in self.sections:
            plan = self.plans.get(section)
            if plan is None:
                found[section] = list(self.parse_issues.get(section, []))
                continue
            found[section] = self._section_issues(section, plan, registry)
            registry.absorb(section, plan, self._asset_refs(plan))
        return found

    def _section_issues(
        self, section: str, plan: SectionPlan, registry: _Registry
    ) -> list[ContractIssue]:
        issues = self._scope_issues(section, plan, registry)
        references = self._reference_issues(section, plan, registry)
        issues += references
        if references:
            return issues
        entities = [*registry.entities.values()]
        entities += [e for e in plan.entities if e.entity_id not in registry.entities]
        assets = [*registry.assets.values()]
        assets += [a for a in self._asset_refs(plan) if a.asset_id not in registry.assets]
        spec = self._spec(entities, assets, plan.scenes, plan.capability_requests)
        if isinstance(spec, VisualSpec):
            found = check_episode(self.pack, self.script, spec, self.manifests)
        else:
            found = spec
        earlier = {"entities": len(registry.entities), "assets": len(registry.assets)}
        for issue in found:
            match = _SPEC_INDEX.match(issue.where)
            # Entities and assets of earlier sections were checked, and reported, in their own.
            if match is None or int(match[2]) >= earlier[match[1]]:
                issues.append(replace(issue, where=f"section {section}: {issue.where}"))
        return issues

    def _scope_issues(
        self, section: str, plan: SectionPlan, registry: _Registry
    ) -> list[ContractIssue]:
        segment_ids = [s.segment_id for s in self.script.segments if s.section == section]
        issues: list[ContractIssue] = []
        for i, scene in enumerate(plan.scenes):
            where = f"section {section}: scenes[{i}]"
            sid = scene.scene_id
            if scene.section != section:
                message = f"scene {sid} says section {scene.section} in the {section} answer."
                fix = f"set section to {section}; plan other sections when asked for them."
                issues.append(ContractIssue("invalid_value", where, message, fix, (sid,)))
            if sid in registry.scenes:
                message = f"scene id {sid} is already used in section {registry.scenes[sid]}."
                issues.append(
                    ContractIssue("conflicting_state", where, message, "pick a new id.", (sid,))
                )
            name = scene.template.template
            allowed = self.caps.actions.get(name)
            if allowed is None:
                message = f"scene {sid} uses the {name} template, which is not available now."
                fix = (
                    f"use one of {', '.join(self.caps.templates)}, or keep the intent as a "
                    "capability_request with the nearest supported fallback."
                )
                issues.append(ContractIssue("unsupported", where, message, fix, (sid, name)))
                allowed = ()
            for j, beat in enumerate(scene.beats):
                if beat.cue.segment_id not in segment_ids:
                    message = (
                        f"scene {sid} beat {beat.beat_id} cues segment {beat.cue.segment_id}, "
                        f"which is not in section {section}."
                    )
                    fix = f"cue one of this section's segments: {', '.join(segment_ids)}."
                    ids = (sid, beat.beat_id, beat.cue.segment_id)
                    issues.append(
                        ContractIssue("invalid_reference", f"{where}.beats[{j}]", message, fix, ids)
                    )
                for action in beat.actions:
                    if allowed and action.action not in allowed:
                        message = f"action {action.action} is not available on {name} now."
                        fix = f"use one of {', '.join(allowed)}."
                        ids = (sid, beat.beat_id, action.action)
                        at = f"{where}.beats[{j}]"
                        issues.append(ContractIssue("unsupported", at, message, fix, ids))
        return issues

    def _reference_issues(
        self, section: str, plan: SectionPlan, registry: _Registry
    ) -> list[ContractIssue]:
        where = f"section {section}"
        issues: list[ContractIssue] = []
        declared: set[str] = set()
        for i, entity in enumerate(plan.entities):
            eid = entity.entity_id
            known = registry.entities.get(eid)
            if eid in declared or (known is not None and known != entity):
                shown = f"{known.label!r} ({known.kind})" if known else "an earlier entry here"
                message = f"entity {eid} is already declared as {shown}."
                fix = "reuse the registry entity unchanged (omit it here) or pick a new id."
                at = f"{where}: entities[{i}]"
                issues.append(ContractIssue("conflicting_state", at, message, fix, (eid,)))
            declared.add(eid)
        declared |= set(registry.entities)
        assets = set(registry.assets)
        for i, asset in enumerate(plan.assets):
            issues += self._asset_issues(f"{where}: assets[{i}]", asset, registry)
            assets.add(asset.asset_id)
        for scene in plan.scenes:
            sid = scene.scene_id
            for eid in dict.fromkeys(_entity_refs(scene)):
                if eid not in declared:
                    sample = ", ".join(sorted(declared)[:8]) or "none yet"
                    message = f"scene {sid} references entity {eid}, which nobody declared."
                    fix = f"declare {eid} in this section's entities or reuse one of: {sample}."
                    at = f"{where}: scene {sid}"
                    issues.append(ContractIssue("invalid_reference", at, message, fix, (sid, eid)))
            for aid in _asset_refs(scene):
                if aid not in assets:
                    message = f"scene {sid} uses asset {aid}, which nobody declared."
                    fix = f"declare {aid} in this section's assets, picking a pack dataset."
                    at = f"{where}: scene {sid}"
                    issues.append(ContractIssue("invalid_reference", at, message, fix, (sid, aid)))
        scene_ids = {s.scene_id for s in plan.scenes}
        for i, request in enumerate(plan.capability_requests):
            if request.scene_id is not None and request.scene_id not in scene_ids:
                message = f"capability request names scene {request.scene_id}, not in section."
                fix = "name one of this section's scenes or leave scene_id null."
                at = f"{where}: capability_requests[{i}]"
                issues.append(
                    ContractIssue("invalid_reference", at, message, fix, (request.scene_id,))
                )
        return issues

    def _asset_issues(
        self, where: str, asset: PlannedAsset, registry: _Registry
    ) -> list[ContractIssue]:
        aid = asset.asset_id
        if asset.kind == "dataset" and asset.dataset_id not in self.datasets:
            known = ", ".join(self.datasets) or "none"
            message = f"asset {aid} names dataset {asset.dataset_id}, which the pack lacks."
            fix = f"pick one of the pack datasets: {known}."
            return [ContractIssue("invalid_reference", where, message, fix, (aid,))]
        if asset.kind == "capture" and asset.capture_id not in self.captures:
            known = ", ".join(self.captures) or "none"
            message = f"asset {aid} names capture {asset.capture_id}, which was not captured."
            fix = f"pick one of the captures: {known}."
            return [ContractIssue("invalid_reference", where, message, fix, (aid,))]
        earlier = registry.assets.get(aid)
        if earlier is not None and (earlier.dataset_id, earlier.capture_id) != (
            asset.dataset_id,
            asset.capture_id,
        ):
            message = f"asset {aid} already names {earlier.dataset_id or earlier.capture_id}."
            fix = "reuse it unchanged or pick a new asset id."
            return [ContractIssue("conflicting_state", where, message, fix, (aid,))]
        return []

    def _asset_refs(self, plan: SectionPlan) -> list[AssetRef]:
        refs: list[AssetRef] = []
        for asset in plan.assets:
            dataset = self.datasets.get(asset.dataset_id or "")
            manifest = self.captures.get(asset.capture_id or "")
            if asset.kind == "dataset" and dataset is not None:
                sha = hashlib.sha256(dataset.canonical_json().encode()).hexdigest()
                refs.append(
                    AssetRef(
                        asset_id=asset.asset_id,
                        kind="dataset",
                        sha256=sha,
                        dataset_id=dataset.dataset_id,
                    )
                )
            elif asset.kind == "capture" and manifest is not None:
                refs.append(
                    AssetRef(
                        asset_id=asset.asset_id,
                        kind="capture",
                        sha256=manifest.artifact_sha256,
                        capture_id=manifest.capture_id,
                    )
                )
        return refs

    def _spec(
        self,
        entities: Sequence[Entity],
        assets: Sequence[AssetRef],
        scenes: Sequence[Scene],
        requests: Sequence[CapabilityRequest],
    ) -> VisualSpec | list[ContractIssue]:
        payload = {
            "spec_id": self.spec_id,
            "script_id": self.script.script_id,
            "script_hash": self.script.script_hash(),
            "pack_hash": self.pack.pack_hash(),
            "design_system_version": DESIGN_SYSTEM_VERSION,
            "entities": [e.model_dump(mode="json") for e in entities],
            "assets": [a.model_dump(mode="json") for a in assets],
            "scenes": [s.model_dump(mode="json") for s in scenes],
            "capability_requests": [r.model_dump(mode="json") for r in requests],
        }
        return load_or_explain(VisualSpec, payload)

    def _finish(self) -> VisualSpec:
        registry = self._registry()
        plans = [self.plans[s] for s in self.sections]
        scenes = [scene for plan in plans for scene in plan.scenes]
        requests = [r for plan in plans for r in plan.capability_requests]
        entities = list(registry.entities.values())
        spec = self._spec(entities, list(registry.assets.values()), scenes, requests)
        # Every cross-reference was checked per section, so an issue here is a planner bug.
        if not isinstance(spec, VisualSpec):
            raise EpisodeInvalidError(spec)
        leftover = check_episode(self.pack, self.script, spec, self.manifests)
        if leftover:
            raise EpisodeInvalidError(leftover)
        self.spec = spec
        return spec


def _input_issues(pack: EvidencePack, script: ScriptPlan) -> list[ContractIssue]:
    """What the model cannot fix: unfrozen or mismatched inputs and pack-level arithmetic."""
    issues: list[ContractIssue] = []
    if pack.frozen_at is None:
        message = f"pack {pack.pack_id} is not frozen."
        fix = "freeze the pack before scripting and planning."
        issues.append(
            ContractIssue("stale_binding", "EvidencePack.frozen_at", message, fix, (pack.pack_id,))
        )
    if script.locked_at is None:
        message = f"script {script.script_id} is not locked; cues point at its tokens."
        fix = "lock the script before planning."
        ids = (script.script_id,)
        issues.append(ContractIssue("stale_binding", "ScriptPlan.locked_at", message, fix, ids))
    if script.pack_hash != pack.pack_hash():
        message = f"script {script.script_id} was written against another version of the pack."
        fix = "re-lock the script against the current pack."
        ids = (script.script_id, pack.pack_id)
        issues.append(ContractIssue("stale_binding", "ScriptPlan.pack_hash", message, fix, ids))
    claims = {c.claim_id for c in pack.claims}
    for i, promise in enumerate(script.promises):
        for cid in promise.claim_ids:
            if cid not in claims:
                message = f"promise {promise.title!r} cites unknown claim {cid}."
                fix = "cite a claim in the pack."
                where = f"ScriptPlan.promises[{i}].claim_ids"
                issues.append(ContractIssue("invalid_reference", where, message, fix, (cid,)))
    return issues + check_calculations(pack) + check_datasets(pack) + check_narration(pack, script)


def _parse_section(text: str, section: str) -> SectionPlan | list[ContractIssue]:
    try:
        payload = _json_object(text)
    except ValueError as error:
        message = f"the answer is not one JSON object ({error})."
        fix = f"answer with one JSON object holding {', '.join(SECTION_KEYS)} and nothing else."
        return [ContractIssue("invalid_value", f"section {section}: answer", message, fix)]
    parsed = load_or_explain(SectionPlan, payload)
    if isinstance(parsed, SectionPlan):
        return parsed
    return [replace(i, where=f"section {section}: {i.where}") for i in parsed]


def _json_object(text: str) -> dict[str, Any]:
    body = text.rsplit("</think>", 1)[-1].strip()
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", body)
    payload = json.loads(body)
    if not isinstance(payload, dict):
        msg = f"got a JSON {type(payload).__name__}"
        raise ValueError(msg)
    return cast(dict[str, Any], payload)


def _user_message(section: str, previous: str | None, issues: Sequence[ContractIssue]) -> str:
    ask = (
        f"Plan the visuals for section {section}. Answer with one JSON object holding exactly "
        f"{', '.join(SECTION_KEYS)}; list only entities that are not in the registry yet."
    )
    if previous is None:
        return ask
    failed = "\n".join(f"- {issue}" for issue in issues)
    return (
        f"{ask}\n\nYour previous answer for this section:\n{previous}\n\n"
        f"It failed these checks; fix every one:\n{failed}"
    )


def _prompt_sha256(system: str, user: str) -> str:
    return hashlib.sha256(canonical_dumps([system, user]).encode()).hexdigest()


def _pretty(value: Mapping[str, Any]) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False)


def _claim_context(claim: Claim) -> dict[str, Any]:
    return {
        "claim_id": claim.claim_id,
        "statement": claim.statement,
        "short_label": claim.short_label,
        "value": claim.value.magnitude if claim.value else None,
        "unit": claim.value.unit if claim.value else "",
        "epistemic_class": claim.epistemic_class,
    }


def _dataset_context(dataset: EvidenceDataset) -> dict[str, Any]:
    return {
        "dataset_id": dataset.dataset_id,
        "title": dataset.title,
        "columns": [c.model_dump(mode="json") for c in dataset.columns],
        "rows": [r.model_dump(mode="json") for r in dataset.rows],
    }


def _capture_context(manifest: SourceCaptureManifest) -> dict[str, Any]:
    return {
        "capture_id": manifest.capture_id,
        "source_id": manifest.source_id,
        "title": manifest.title,
        "publisher": manifest.publisher,
        "sections": [{"section_id": s.section_id, "heading": s.heading} for s in manifest.sections],
        "quotes": [
            {
                "quote_id": q.quote_id,
                "section_id": q.section_id,
                "text": q.text,
                "context_before": q.context_before,
                "context_after": q.context_after,
                "claim_ids": list(q.claim_ids),
            }
            for q in manifest.quotes
        ],
    }


def _entity_refs(scene: Scene) -> Iterator[str]:
    template = scene.template
    if isinstance(template, ChartTemplate):
        yield from (s.entity_id for s in template.series)
    elif isinstance(template, DiagramTemplate):
        yield from (n.entity_id for n in template.nodes)
        yield from (e.entity_id for e in template.edges)
    elif isinstance(template, TextTemplate):
        yield from (i.entity_id for i in template.items)
    yield from scene.initial_visible
    for beat in scene.beats:
        for action in beat.actions:
            yield from getattr(action, "targets", ())


def _asset_refs(scene: Scene) -> list[str]:
    template = scene.template
    if isinstance(template, ChartTemplate):
        return [template.dataset_asset_id]
    if isinstance(template, SourceDocumentTemplate):
        return [template.capture_asset_id]
    return []


def _prune_unions(node: Any, allowed: Mapping[str, set[str]], narrowed: dict[str, str]) -> None:
    """Drop discriminated-union branches whose tag is not allowed; note which defs to narrow."""
    if isinstance(node, list):
        for item in cast(list[Any], node):
            _prune_unions(item, allowed, narrowed)
        return
    if not isinstance(node, dict):
        return
    node = cast(dict[str, Any], node)
    discriminator = node.get("discriminator")
    if isinstance(discriminator, dict) and "oneOf" in node:
        prop = cast(dict[str, Any], discriminator)["propertyName"]
        if prop in allowed:
            mapping = {
                tag: ref for tag, ref in discriminator["mapping"].items() if tag in allowed[prop]
            }
            discriminator["mapping"] = mapping
            kept = set(mapping.values())
            node["oneOf"] = [b for b in node["oneOf"] if b.get("$ref") in kept]
            for ref in kept:
                narrowed[ref.removeprefix("#/$defs/")] = prop
    for value in node.values():
        _prune_unions(value, allowed, narrowed)


def _reachable(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep only the $defs the root still reaches, in their original order."""
    defs: dict[str, Any] = schema.get("$defs", {})
    keep: set[str] = set()
    todo = list(_refs({k: v for k, v in schema.items() if k != "$defs"}))
    while todo:
        name = todo.pop()
        if name not in keep:
            keep.add(name)
            todo.extend(_refs(defs[name]))
    schema["$defs"] = {k: v for k, v in defs.items() if k in keep}
    return schema


def _refs(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        node = cast(dict[str, Any], node)
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            yield ref.removeprefix("#/$defs/")
        for value in node.values():
            yield from _refs(value)
    elif isinstance(node, list):
        for value in cast(list[Any], node):
            yield from _refs(value)


def _planned_asset_schema(dataset_ids: list[str], capture_ids: list[str]) -> dict[str, Any]:
    schema = PlannedAsset.model_json_schema()
    kinds = [k for k, ids in (("dataset", dataset_ids), ("capture", capture_ids)) if ids]
    props = schema["properties"]
    props["kind"] = {"enum": kinds, "title": "Kind", "type": "string"}
    props["dataset_id"] = _nullable_enum(dataset_ids, "Dataset Id")
    props["capture_id"] = _nullable_enum(capture_ids, "Capture Id")
    return schema


def _nullable_enum(ids: list[str], title: str) -> dict[str, Any]:
    if not ids:
        return {"default": None, "title": title, "type": "null"}
    return {
        "anyOf": [{"enum": ids, "type": "string"}, {"type": "null"}],
        "default": None,
        "title": title,
    }
