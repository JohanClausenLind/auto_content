"""Skill (2.6), model catalog (18.2), execution policy (18.4), and ExecutionDecision contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import SchemaModel, SemVer, VersionedModel


class Lifecycle(StrEnum):
    draft = "draft"
    canary = "canary"
    active = "active"
    deprecated = "deprecated"
    quarantined = "quarantined"
    revoked = "revoked"


class ExecutionLocation(StrEnum):
    local_cpu = "local_cpu"
    local_gpu = "local_gpu"
    tailnet_worker = "tailnet_worker"
    cloud = "cloud"


class ExecutorType(StrEnum):
    deterministic_code = "deterministic_code"
    model_role = "model_role"
    comfyui_workflow = "comfyui_workflow"
    provider_api = "provider_api"
    composition = "composition"


class SkillPermissions(SchemaModel):
    network_egress: bool = False
    egress_allowlist: tuple[str, ...] = ()
    filesystem_write_scopes: tuple[str, ...] = ()  # e.g. "artifacts", "scratch"
    gpu: bool = False
    external_side_effects: bool = False  # publishing, sending, spending


class QualityFloor(SchemaModel):
    evaluation_pack: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    minimum: float
    higher_is_better: bool = True


class CostEstimator(SchemaModel):
    kind: Literal["fixed", "per_token", "per_second", "per_image", "per_character"]
    usd: float = Field(ge=0)
    local_gpu_seconds: float = Field(default=0, ge=0)


class SkillManifest(VersionedModel):
    skill_id: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
    version: SemVer
    status: Lifecycle = Lifecycle.draft
    purpose: str = Field(min_length=1, max_length=500)
    input_schema: str = Field(min_length=1, description="Registered schema name for inputs")
    output_schema: str = Field(min_length=1, description="Registered schema name for outputs")
    executor: ExecutorType
    implementation_ref: str = Field(
        min_length=1,
        description="Immutable reference (module:callable, package@version, workflow id@version)",
    )
    permitted_locations: tuple[ExecutionLocation, ...] = Field(min_length=1)
    required_models: tuple[str, ...] = ()  # logical aliases
    required_runtimes: tuple[str, ...] = ()
    permissions: SkillPermissions = SkillPermissions()
    quality_floor: QualityFloor | None = None
    license_evidence: str = Field(min_length=1)
    cost: CostEstimator
    timeout_seconds: int = Field(ge=1, le=86400)
    max_retries: int = Field(ge=0, le=10)
    idempotent: bool = True
    cancellable: bool = True
    fallbacks: tuple[str, ...] = ()  # other skill ids
    signature: str | None = None  # base64 Ed25519 over canonical JSON without this field


class ModelDescriptor(VersionedModel):
    alias: str = Field(pattern=r"^[a-z][a-z0-9_]{2,40}$")  # logical alias, e.g. fast_structured
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    revision: str | None = None
    quantization: str | None = None
    capabilities: tuple[str, ...] = ()
    context_tokens: int | None = Field(default=None, ge=1)
    license: str = Field(min_length=1)
    commercial_use: bool
    location: ExecutionLocation
    vram_bytes_estimate: int | None = Field(default=None, ge=0)
    usd_per_million_input_tokens: float | None = Field(default=None, ge=0)
    usd_per_million_output_tokens: float | None = Field(default=None, ge=0)
    approved_for_skills: tuple[str, ...] = ()  # skill ids with a passed evaluation
    fallback_aliases: tuple[str, ...] = ()


class PolicyKind(StrEnum):
    cloud_only = "cloud_only"
    quality_auto = "quality_auto"
    prefer_local = "prefer_local"
    local_only = "local_only"
    pinned = "pinned"


class ExecutionPolicy(SchemaModel):
    kind: PolicyKind
    allow_cloud_fallback: bool = True
    allowed_providers: tuple[str, ...] = ()  # empty = any
    max_external_usd: float | None = Field(default=None, ge=0)
    max_wait_seconds: int | None = Field(default=None, ge=0)
    pinned_alias: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> ExecutionPolicy:
        if self.kind == PolicyKind.local_only and self.allow_cloud_fallback:
            msg = "local_only never allows cloud fallback"
            raise ValueError(msg)
        if self.kind == PolicyKind.pinned and not self.pinned_alias:
            msg = "pinned policy requires pinned_alias"
            raise ValueError(msg)
        return self


PRESETS: dict[str, ExecutionPolicy] = {
    "offline": ExecutionPolicy(kind=PolicyKind.local_only, allow_cloud_fallback=False),
    "private_local": ExecutionPolicy(kind=PolicyKind.local_only, allow_cloud_fallback=False),
    "economy": ExecutionPolicy(
        kind=PolicyKind.prefer_local, allow_cloud_fallback=True, max_external_usd=0.5
    ),
    "balanced": ExecutionPolicy(kind=PolicyKind.quality_auto, allow_cloud_fallback=True),
    "quality": ExecutionPolicy(kind=PolicyKind.quality_auto, allow_cloud_fallback=True),
    "maximum": ExecutionPolicy(kind=PolicyKind.cloud_only, allow_cloud_fallback=False),
}


class RejectedCandidate(SchemaModel):
    alias: str
    reason: str


class ExecutionDecision(SchemaModel):
    """Why this executor/model/location was chosen, what was rejected, whether fallback occurred."""

    skill_id: str
    skill_version: SemVer
    policy: ExecutionPolicy
    chosen_alias: str | None
    chosen_location: ExecutionLocation | None
    estimated_usd: float = Field(ge=0)
    rejected: tuple[RejectedCandidate, ...] = ()
    fallback_used: bool = False
    outcome: Literal["dispatch", "pause_insufficient_quality", "pause_budget", "pause_no_candidate"]
    explanation: str = Field(min_length=1)


class HardwareFit(SchemaModel):
    fits: bool
    reason: str
    headroom_bytes: int | None = None
