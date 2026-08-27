"""Registry of exported contracts and the JSON Schema exporter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, TypeAdapter
from pydantic.json_schema import GenerateJsonSchema

from content_factory.schemas import comfyui, editing, hardware, sequences, skills
from content_factory.schemas.base import canonical_dumps

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
    "ComfyWorkflowPackage": comfyui.ComfyWorkflowPackage,
    "ComfyProvenance": comfyui.ComfyProvenance,
    "HardwareInventory": hardware.HardwareInventory,
    "SkillManifest": skills.SkillManifest,
    "ModelDescriptor": skills.ModelDescriptor,
    "ExecutionPolicy": skills.ExecutionPolicy,
    "ExecutionDecision": skills.ExecutionDecision,
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


def registry_digest() -> str:
    """Hash over all exported schemas — used to detect drift."""
    import hashlib

    h = hashlib.sha256()
    for name in sorted(SCHEMA_REGISTRY):
        h.update(canonical_dumps(json_schema_for(name)).encode("utf-8"))
    return h.hexdigest()
