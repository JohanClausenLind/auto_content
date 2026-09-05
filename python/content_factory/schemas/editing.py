"""EditorCore contracts: typed, reversible operations; critique mapping; FixPlans (2.9, 16.5)."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from content_factory.schemas.base import (
    OpaqueId,
    SchemaModel,
    Severity,
    Sha256Hex,
    VersionedModel,
)


class InvalidationScope(StrEnum):
    """What an operation may invalidate downstream (dependency impact)."""

    layout = "layout"
    timing = "timing"
    render = "render"
    evidence = "evidence"  # touching sourced claims reopens evidence validation
    originality = "originality"
    packaging = "packaging"


class ReplaceTextRange(SchemaModel):
    op: Literal["replace_text_range"] = "replace_text_range"
    unit_id: OpaqueId
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    replacement: str
    # Filled by the validator from the document; a claim-linked range reopens evidence checks.
    expected_before: str | None = None


class ReorderCarouselCard(SchemaModel):
    op: Literal["reorder_carousel_card"] = "reorder_carousel_card"
    deliverable_id: OpaqueId
    card_id: OpaqueId
    to_index: int = Field(ge=0)


class ChangeSceneVariant(SchemaModel):
    op: Literal["change_scene_variant"] = "change_scene_variant"
    scene_id: OpaqueId
    variant: str = Field(min_length=1, max_length=64)


class RetimeBeat(SchemaModel):
    op: Literal["retime_beat"] = "retime_beat"
    scene_id: OpaqueId
    duration_frames: int = Field(ge=1)


class UpdateChartEncoding(SchemaModel):
    op: Literal["update_chart_encoding"] = "update_chart_encoding"
    scene_id: OpaqueId
    # Presentation only: emphasis/labels/legibility. Never changes data.
    encoding_patch: dict[str, str | int | float | bool] = Field(default_factory=dict)


class SuppressDeliverable(SchemaModel):
    op: Literal["suppress_deliverable"] = "suppress_deliverable"
    deliverable_id: OpaqueId
    reason: str = Field(min_length=1, max_length=500)


EditOperation = Annotated[
    ReplaceTextRange
    | ReorderCarouselCard
    | ChangeSceneVariant
    | RetimeBeat
    | UpdateChartEncoding
    | SuppressDeliverable,
    Field(discriminator="op"),
]

OPERATION_INVALIDATION: dict[str, tuple[InvalidationScope, ...]] = {
    "replace_text_range": (
        InvalidationScope.layout,
        InvalidationScope.render,
        InvalidationScope.originality,
    ),
    "reorder_carousel_card": (InvalidationScope.layout, InvalidationScope.render),
    "change_scene_variant": (InvalidationScope.layout, InvalidationScope.render),
    "retime_beat": (InvalidationScope.timing, InvalidationScope.render),
    "update_chart_encoding": (InvalidationScope.layout, InvalidationScope.render),
    "suppress_deliverable": (InvalidationScope.packaging,),
}


class DependencyImpact(SchemaModel):
    affected_unit_ids: tuple[OpaqueId, ...] = ()
    invalidates: tuple[InvalidationScope, ...] = ()
    reopens_evidence_validation: bool = False
    reopens_preflight_gate: bool = False


class EditBatch(VersionedModel):
    batch_id: OpaqueId
    project_id: OpaqueId
    base_revision_hash: Sha256Hex
    operations: tuple[EditOperation, ...] = Field(min_length=1)
    provenance: Literal["operator", "model_proposal", "fix_plan", "replay"] = "operator"
    note: str | None = Field(default=None, max_length=1000)


class CritiqueCategory(StrEnum):
    legibility = "legibility"
    layout = "layout"
    pacing = "pacing"
    tone = "tone"
    consistency = "consistency"
    chart_semantics = "chart_semantics"
    factual = "factual"
    rights = "rights"
    policy = "policy"
    unclear = "unclear"


class CritiqueFinding(SchemaModel):
    category: CritiqueCategory
    severity: Severity
    target_unit_ids: tuple[OpaqueId, ...]
    summary: str = Field(min_length=1, max_length=500)
    evidence: str | None = Field(default=None, max_length=1000)


class FixPlan(SchemaModel):
    kind: Literal["fix_plan"] = "fix_plan"
    findings: tuple[CritiqueFinding, ...] = Field(min_length=1)
    operations: tuple[EditOperation, ...] = Field(min_length=1)
    impact: DependencyImpact
    plain_language: str = Field(min_length=1, max_length=2000)
    estimated_cost_usd: float = Field(ge=0)
    estimated_seconds: int = Field(ge=0)


class ClarifyingQuestion(SchemaModel):
    kind: Literal["clarifying_question"] = "clarifying_question"
    question: str = Field(min_length=1, max_length=500)
    candidate_unit_ids: tuple[OpaqueId, ...] = ()


class GateRequired(SchemaModel):
    """The request would change facts, rights, disclosures, or publish scope: surface the gate."""

    kind: Literal["gate_required"] = "gate_required"
    gate: Literal["evidence", "rights", "disclosure", "publish_scope", "budget", "policy"]
    reason: str = Field(min_length=1, max_length=1000)


class Refusal(SchemaModel):
    kind: Literal["refusal"] = "refusal"
    policy: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)


RevisionOutcome = Annotated[
    FixPlan | ClarifyingQuestion | GateRequired | Refusal, Field(discriminator="kind")
]


class RevisionRequest(VersionedModel):
    """One turn of the Revision Box thread, bound to the exact artifact revision it critiques."""

    request_id: OpaqueId
    project_id: OpaqueId
    artifact_id: OpaqueId
    artifact_revision_hash: Sha256Hex
    thread_id: OpaqueId
    feedback: str = Field(min_length=1, max_length=4000)
    outcome: RevisionOutcome | None = None
