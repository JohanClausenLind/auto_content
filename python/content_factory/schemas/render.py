"""RenderBundle: everything a deterministic renderer needs, fully resolved, no network.

Numbers reach the renderer only through resolved dataset rows (2.4). Assets are local paths that
the render orchestrator materialized from the ArtifactStore.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.artboards import ArtboardSpec
from content_factory.schemas.base import OpaqueId, SchemaModel, VersionedModel
from content_factory.schemas.scenes import CompiledTimeline, StoryPlan


class DatasetTable(SchemaModel):
    dataset_id: OpaqueId
    classification: Literal["SOURCE_DATA", "DERIVED_DATA", "ESTIMATE", "ILLUSTRATIVE"]
    columns: tuple[str, ...] = Field(min_length=1)
    rows: tuple[dict[str, str | int | float | None], ...] = Field(min_length=1)
    unit: str = ""
    source_ids: tuple[OpaqueId, ...] = ()
    label: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _rows_match_columns(self) -> DatasetTable:
        for row in self.rows:
            if set(row) != set(self.columns):
                msg = f"dataset {self.dataset_id}: row keys must equal columns"
                raise ValueError(msg)
        return self


class SourceCard(SchemaModel):
    source_id: OpaqueId
    title: str = Field(min_length=1, max_length=200)
    publisher: str = Field(default="", max_length=120)
    url: str = Field(min_length=1)
    accessed: str = Field(min_length=4, max_length=32)  # ISO date


class BrandTokens(SchemaModel):
    """Approved token overrides only — never arbitrary CSS/JS/remote fonts (13.1)."""

    accent: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    paper: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    ink: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    font_family: Literal["Inter"] = "Inter"  # pinned local fonts only
    logo_asset_id: OpaqueId | None = None


class RenderBundle(VersionedModel):
    bundle_id: OpaqueId
    kind: Literal["artboard", "timeline"]
    artboard: ArtboardSpec | None = None
    plan: StoryPlan | None = None
    timeline: CompiledTimeline | None = None
    datasets: dict[str, DatasetTable] = Field(default_factory=dict)
    sources: dict[str, SourceCard] = Field(default_factory=dict)
    assets: dict[str, str] = Field(default_factory=dict, description="asset_id -> local file path")
    brand: BrandTokens = BrandTokens()
    seed: str = Field(default="content-factory", min_length=1, max_length=64)

    @model_validator(mode="after")
    def _complete(self) -> RenderBundle:
        if self.kind == "artboard" and self.artboard is None:
            msg = "artboard bundle needs an artboard"
            raise ValueError(msg)
        if self.kind == "timeline" and (self.plan is None or self.timeline is None):
            msg = "timeline bundle needs plan and timeline"
            raise ValueError(msg)
        refs: set[str] = set()
        if self.artboard:
            for layer in self.artboard.layers:
                ds = getattr(layer, "value", None)
                if ds is not None:
                    refs.add(ds.dataset_id)
        if self.plan:
            for scene in self.plan.scenes:
                for attr in ("value", "data"):
                    ds = getattr(scene, attr, None)
                    if ds is not None:
                        refs.add(ds.dataset_id)
        missing = sorted(refs - set(self.datasets))
        if missing:
            msg = f"bundle is missing datasets referenced by the spec: {missing}"
            raise ValueError(msg)
        return self
