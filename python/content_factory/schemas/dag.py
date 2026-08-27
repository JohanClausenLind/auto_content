"""Deliverable dependency DAG (2.12): only the branches the selected deliverables require."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import OpaqueId, SchemaModel, VersionedModel


class Stage(StrEnum):
    # shared
    ingest = "ingest"
    research = "research"
    verify_claims = "verify_claims"
    plan_story = "plan_story"
    compile_datasets = "compile_datasets"
    originality_topic = "originality_topic"
    preflight = "preflight"
    # text branch
    write_copy = "write_copy"
    compile_text_package = "compile_text_package"
    # static branch
    compile_artboards = "compile_artboards"
    render_static = "render_static"
    # carousel branch
    compile_cards = "compile_cards"
    render_cards = "render_cards"
    # article branch
    draft_article = "draft_article"
    link_check = "link_check"
    compile_seo = "compile_seo"
    export_article = "export_article"
    # newsletter branch
    compose_email = "compose_email"
    compile_email = "compile_email"
    email_preview_qc = "email_preview_qc"
    # image-sequence branch
    generate_anchor = "generate_anchor"
    lock_generation = "lock_generation"
    compile_controls = "compile_controls"
    generate_keyframes = "generate_keyframes"
    drift_qc = "drift_qc"
    interpolate = "interpolate"
    package_sequence = "package_sequence"
    # audio branch
    lock_script = "lock_script"
    synthesize_narration = "synthesize_narration"
    align_words = "align_words"
    compile_captions = "compile_captions"
    mix_audio = "mix_audio"
    # video branch
    compile_timeline = "compile_timeline"
    render_scenes = "render_scenes"
    compose_video = "compose_video"
    # per deliverable / destination
    qc_deliverable = "qc_deliverable"
    originality_gate = "originality_gate"
    compile_destination_packages = "compile_destination_packages"
    package_qc = "package_qc"


class Executor(StrEnum):
    ai = "ai"
    deterministic = "deterministic"
    human = "human"
    hybrid = "hybrid"


class StageNode(SchemaModel):
    node_id: str = Field(pattern=r"^[a-z_]+(:[A-Za-z0-9_-]+)?$")  # "stage" or "stage:deliverable"
    stage: Stage
    deliverable_id: OpaqueId | None = None  # None = shared
    depends_on: tuple[str, ...] = ()
    executor: Executor = Executor.deterministic
    resource_class: Literal[
        "control",
        "research",
        "render-cpu",
        "render-gpu",
        "inference-llm",
        "inference-image",
        "inference-audio",
        "publish",
    ] = "control"
    optional: bool = False


class NotRequired(SchemaModel):
    """Typed evidence that a stage was pruned by policy rather than run for symmetry."""

    stage: Stage
    deliverable_id: OpaqueId | None
    reason: str = Field(min_length=1)


class DeliverableDAG(VersionedModel):
    campaign_id: OpaqueId
    nodes: tuple[StageNode, ...] = Field(min_length=1)
    pruned: tuple[NotRequired, ...] = ()
    compiler_version: str

    @model_validator(mode="after")
    def _acyclic_and_resolved(self) -> DeliverableDAG:
        ids = {n.node_id for n in self.nodes}
        if len(ids) != len(self.nodes):
            msg = "node ids must be unique"
            raise ValueError(msg)
        for n in self.nodes:
            for d in n.depends_on:
                if d not in ids:
                    msg = f"{n.node_id} depends on unknown node {d}"
                    raise ValueError(msg)
        # Kahn's algorithm for cycle detection.
        indeg = {n.node_id: len(n.depends_on) for n in self.nodes}
        out: dict[str, list[str]] = {n.node_id: [] for n in self.nodes}
        for n in self.nodes:
            for d in n.depends_on:
                out[d].append(n.node_id)
        ready = [k for k, v in indeg.items() if v == 0]
        seen = 0
        while ready:
            cur = ready.pop()
            seen += 1
            for nxt in out[cur]:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    ready.append(nxt)
        if seen != len(self.nodes):
            msg = "dependency graph has a cycle"
            raise ValueError(msg)
        return self

    def stages(self) -> set[Stage]:
        return {n.stage for n in self.nodes}

    def topological(self) -> list[StageNode]:
        by_id = {n.node_id: n for n in self.nodes}
        indeg = {n.node_id: len(n.depends_on) for n in self.nodes}
        out: dict[str, list[str]] = {n.node_id: [] for n in self.nodes}
        for n in self.nodes:
            for d in n.depends_on:
                out[d].append(n.node_id)
        ready = sorted(k for k, v in indeg.items() if v == 0)
        order: list[StageNode] = []
        while ready:
            cur = ready.pop(0)
            order.append(by_id[cur])
            for nxt in sorted(out[cur]):
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    ready.append(nxt)
            ready.sort()
        return order
