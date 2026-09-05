"""Registry of exported contracts and the JSON Schema exporter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter
from pydantic.json_schema import GenerateJsonSchema

from content_factory.schemas import (
    animation,
    artboards,
    audio,
    comfyui,
    content,
    dag,
    delivery,
    documentary,
    editing,
    energy,
    hardware,
    nodes,
    offers,
    placement,
    reference,
    render,
    research,
    scenes,
    sequences,
    shots,
    skills,
    storage_plan,
    style_kit,
    workflow_template,
    workspace_graph,
)

SCHEMA_REGISTRY: dict[str, type[BaseModel] | Any] = {
    "EditBatch": editing.EditBatch,
    "EditOperation": editing.EditOperation,
    "DependencyImpact": editing.DependencyImpact,
    "FixPlan": editing.FixPlan,
    "RevisionRequest": editing.RevisionRequest,
    "RevisionOutcome": editing.RevisionOutcome,
    "MotionPlan": sequences.MotionPlan,
    "ControlAsset": sequences.ControlAsset,
    "GenerationLock": sequences.GenerationLock,
    "FrameSpec": sequences.FrameSpec,
    "ShotSpec": shots.ShotSpec,
    "ShotPlan": shots.ShotPlan,
    "ControlBundle": shots.ControlBundle,
    "ShotRouting": shots.ShotRouting,
    "WorkflowTemplate": workflow_template.WorkflowTemplate,
    "ReferenceClip": reference.ReferenceClip,
    "ReferenceLibrary": reference.ReferenceLibrary,
    "ReferenceQuery": reference.ReferenceQuery,
    "ReferenceMatchSet": reference.ReferenceMatchSet,
    "WorkspaceGraph": workspace_graph.WorkspaceGraph,
    "AnimationSpec": animation.AnimationSpec,
    "MusicTrack": audio.MusicTrack,
    "ComfyWorkflowPackage": comfyui.ComfyWorkflowPackage,
    "ComfyProvenance": comfyui.ComfyProvenance,
    "HardwareInventory": hardware.HardwareInventory,
    "SkillManifest": skills.SkillManifest,
    "ModelDescriptor": skills.ModelDescriptor,
    "ExecutionPolicy": skills.ExecutionPolicy,
    "ExecutionDecision": skills.ExecutionDecision,
    "ProjectBrief": content.ProjectBrief,
    "ContentCampaign": content.ContentCampaign,
    "ContentDeliverable": content.ContentDeliverable,
    "DestinationBinding": content.DestinationBinding,
    "SceneSpec": scenes.SceneSpec,
    "StoryPlan": scenes.StoryPlan,
    "CompiledTimeline": scenes.CompiledTimeline,
    "ArtboardSpec": artboards.ArtboardSpec,
    "LayerSpec": artboards.LayerSpec,
    "DeliverableDAG": dag.DeliverableDAG,
    "RenderBundle": render.RenderBundle,
    "DatasetTable": render.DatasetTable,
    "NarrationRequest": audio.NarrationRequest,
    "NarrationSegment": audio.NarrationSegment,
    "AlignmentReport": audio.AlignmentReport,
    "CaptionTrack": audio.CaptionTrack,
    "LoudnessReport": audio.LoudnessReport,
    "AudioMixSpec": audio.AudioMixSpec,
    "AudioArtifactReport": audio.AudioArtifactReport,
    "SpeechRestorationSpec": audio.SpeechRestorationSpec,
    "SpeechRestorationReport": audio.SpeechRestorationReport,
    "MasterChainSpec": audio.MasterChainSpec,
    "SoundConditionSpec": audio.SoundConditionSpec,
    "SoundConditionReport": audio.SoundConditionReport,
    "CueSheet": audio.CueSheet,
    "DeliveryPackage": delivery.DeliveryPackage,
    "NodeCapabilityReport": nodes.NodeCapabilityReport,
    "ComputeOffer": offers.ComputeOffer,
    "PlacementDecision": placement.PlacementDecision,
    "StoragePlan": storage_plan.StoragePlan,
    "EnergyReport": energy.EnergyReport,
    "EpisodeOutline": documentary.EpisodeOutline,
    "ShortsPlan": documentary.ShortsPlan,
    "EpisodeMetadata": documentary.EpisodeMetadata,
    "EditorialStyleKit": style_kit.EditorialStyleKit,
    "SourceRecord": research.SourceRecord,
    "EvidenceRecord": research.EvidenceRecord,
    "ClaimRecord": research.ClaimRecord,
    "EvidenceRequirementPlan": research.EvidenceRequirementPlan,
    "ResearchPack": research.ResearchPack,
}

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_ID_BASE = "https://content-factory.local/schema/"


class _Generator(GenerateJsonSchema):
    schema_dialect = SCHEMA_DIALECT


def json_schema_for(name: str) -> dict[str, Any]:
    target = SCHEMA_REGISTRY[name]
    adapter: TypeAdapter[Any] = TypeAdapter(target)
    schema = adapter.json_schema(mode="serialization", schema_generator=_Generator)
    schema = _normalize(schema)
    schema = {"$schema": SCHEMA_DIALECT, "$id": f"{SCHEMA_ID_BASE}{name}.schema.json", **schema}
    if "title" not in schema:
        schema["title"] = name
    return schema


def _normalize(node: Any) -> Any:
    """Make the emitted schema match what the serializer actually produces and what Ajv (strict)
    requires:

    * Strict models always serialize every field, so every property of a closed object
      (``additionalProperties: false``) is ``required`` — including fields with defaults such as
      the ``op``/``kind`` discriminator tags and ``schema_version``.
    * ``discriminator`` needs an explicit ``type: object`` sibling under Ajv strictTypes.
    """
    if isinstance(node, list):
        return [_normalize(n) for n in node]
    if not isinstance(node, dict):
        return node
    out = {k: _normalize(v) for k, v in node.items()}
    if (
        out.get("type") == "object"
        and out.get("additionalProperties") is False
        and "properties" in out
    ):
        out["required"] = sorted(out["properties"].keys())
    if "discriminator" in out:
        # Ajv supports only ``propertyName``; branch ``const`` tags make ``mapping`` redundant.
        out["discriminator"] = {"propertyName": out["discriminator"]["propertyName"]}
        out.setdefault("type", "object")
    return out


def export_json_schemas(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in sorted(SCHEMA_REGISTRY):
        path = out_dir / f"{name}.schema.json"
        text = json.dumps(json_schema_for(name), indent=2, sort_keys=True, ensure_ascii=False)
        path.write_text(text + "\n", encoding="utf-8")
        written.append(path)
    return written
