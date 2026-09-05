"""ComfyUI governance contracts (19.1, 19.2)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import SchemaModel, SemVer, Sha256Hex, VersionedModel


# Governs a ComfyUI NODE PACKAGE. Deliberately distinct from skills.Lifecycle, which governs
# signed skill manifests: the two vocabularies coincide today but are separate published
# contracts and are free to diverge. Do not merge them.
class LifecycleStatus(StrEnum):
    draft = "draft"
    canary = "canary"
    active = "active"
    deprecated = "deprecated"
    quarantined = "quarantined"
    revoked = "revoked"


class CapabilityFlag(StrEnum):
    text_to_image = "text_to_image"
    reference_image_edit = "reference_image_edit"
    control_pose = "control_pose"
    control_layout = "control_layout"
    control_depth = "control_depth"
    control_edge = "control_edge"
    mask_inpaint = "mask_inpaint"
    upscale = "upscale"
    image_to_video = "image_to_video"
    deterministic_seed = "deterministic_seed"
    keyframe_guide = "keyframe_guide"  # extra conditioning frames at given indices (LTXVAddGuide)
    control_segmentation = "control_segmentation"
    pose_video = "pose_video"


class ParameterBinding(SchemaModel):
    """A typed public parameter bound to exactly one node widget. Only these may be injected."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    node_id: str = Field(min_length=1)
    input_name: str = Field(min_length=1)
    kind: Literal["int", "float", "string", "seed", "image_ref"]
    minimum: float | None = None
    maximum: float | None = None
    description: str = Field(default="", max_length=500)


class RequiredModel(SchemaModel):
    filename: str = Field(min_length=1)
    relative_path: str = Field(pattern=r"^models/[a-z_]+$")
    source_url: str = Field(min_length=1)
    sha256: Sha256Hex | None = None
    license: str = Field(min_length=1)
    size_bytes: int | None = Field(default=None, ge=0)


class PinnedNode(SchemaModel):
    registry_name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    commit: str | None = None
    license: str = Field(min_length=1)
    security_review: Literal["approved", "pending", "rejected"] = "pending"


class ComfyWorkflowPackage(VersionedModel):
    package_id: str = Field(pattern=r"^[a-z][a-z0-9.-]{2,63}$")
    version: SemVer
    status: LifecycleStatus = LifecycleStatus.draft
    purpose: str = Field(min_length=1, max_length=500)
    comfyui_min_version: str = Field(min_length=1)
    comfyui_max_version: str | None = None
    api_workflow: dict[str, dict[str, Any]]
    parameters: tuple[ParameterBinding, ...] = ()
    required_models: tuple[RequiredModel, ...] = ()
    custom_nodes: tuple[PinnedNode, ...] = ()
    capabilities: tuple[CapabilityFlag, ...] = ()
    max_resolution: tuple[int, int] | None = None
    expected_outputs: tuple[str, ...] = Field(default=(), description="Output node ids")
    snapshot_sha256: Sha256Hex | None = None
    signature: str | None = None

    @model_validator(mode="after")
    def _bindings_resolve(self) -> ComfyWorkflowPackage:
        for p in self.parameters:
            node = self.api_workflow.get(p.node_id)
            if node is None:
                msg = f"parameter {p.name!r} binds to unknown node {p.node_id!r}"
                raise ValueError(msg)
            inputs = node.get("inputs", {})
            if p.input_name not in inputs:
                msg = f"parameter {p.name!r} binds to unknown input {p.input_name!r}"
                raise ValueError(msg)
        for out in self.expected_outputs:
            if out not in self.api_workflow:
                msg = f"expected output node {out!r} is not in the workflow"
                raise ValueError(msg)
        return self


class ComfyProvenance(SchemaModel):
    backend: Literal["comfyui"] = "comfyui"
    endpoint: str
    package_id: str
    package_version: SemVer
    comfyui_version: str
    prompt_id: str
    client_id: str
    parameters: dict[str, int | float | str]
    injected_workflow_sha256: Sha256Hex
    output_files: tuple[str, ...]
    output_sha256: tuple[Sha256Hex, ...]
    seed: int | None = None
    started_at: str
    finished_at: str
