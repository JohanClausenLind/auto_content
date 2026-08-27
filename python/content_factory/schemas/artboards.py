"""Static artboards and layers (16.3, 13.2): immutable specs from which PNG/PDF exports derive."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import OpaqueId, SchemaModel, VersionedModel
from content_factory.schemas.scenes import DataRef, TextRef


class Rect(SchemaModel):
    """Normalized [0,1] rectangle inside the artboard."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def _inside(self) -> Rect:
        if self.x + self.w > 1.0001 or self.y + self.h > 1.0001:
            msg = "rect exceeds artboard bounds"
            raise ValueError(msg)
        return self


class LayerBase(SchemaModel):
    layer_id: OpaqueId
    frame: Rect
    locked: bool = False
    reading_order: int = Field(ge=0)


class TextLayer(LayerBase):
    kind: Literal["text"] = "text"
    text: TextRef
    role: Literal["headline", "subhead", "body", "caption", "label", "number", "source"] = "body"
    align: Literal["start", "center", "end"] = "start"
    max_lines: int = Field(default=4, ge=1, le=20)


class NumberLayer(LayerBase):
    kind: Literal["number"] = "number"
    value: DataRef
    unit: str = Field(default="", max_length=32)
    format: Literal["auto", "integer", "percent", "compact", "currency"] = "auto"


class ImageLayer(LayerBase):
    kind: Literal["image"] = "image"
    asset_id: OpaqueId
    alt_text: str = Field(min_length=1, max_length=500)
    fit: Literal["cover", "contain"] = "cover"
    crop: Rect | None = None


class ShapeLayer(LayerBase):
    kind: Literal["shape"] = "shape"
    shape: Literal["rect", "rule", "pill"] = "rect"
    color_role: Literal["surface", "accent", "muted", "series-1", "series-2", "series-3"] = "accent"
    radius_token: Literal["none", "sm", "md", "lg", "full"] = "none"


class ChartLayer(LayerBase):
    kind: Literal["chart"] = "chart"
    scene_ref: OpaqueId  # a ChartScene in the project; static rendering of the same grammar


class SourceLayer(LayerBase):
    kind: Literal["source"] = "source"
    source_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=6)


LayerSpec = Annotated[
    TextLayer | NumberLayer | ImageLayer | ShapeLayer | ChartLayer | SourceLayer,
    Field(discriminator="kind"),
]


class ArtboardSpec(VersionedModel):
    artboard_id: OpaqueId
    deliverable_id: OpaqueId
    width: int = Field(ge=64, le=8192)
    height: int = Field(ge=64, le=8192)
    theme: str = Field(default="editorial", max_length=40)
    brand_kit_id: OpaqueId | None = None
    background_role: Literal["paper", "ink", "surface"] = "paper"
    safe_area: Rect = Rect(x=0.06, y=0.06, w=0.88, h=0.88)
    layers: tuple[LayerSpec, ...] = Field(min_length=1)
    alt_text: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _unique_layers(self) -> ArtboardSpec:
        ids = [layer.layer_id for layer in self.layers]
        if len(ids) != len(set(ids)):
            msg = "layer ids must be unique"
            raise ValueError(msg)
        orders = sorted(layer.reading_order for layer in self.layers)
        if orders != list(range(len(orders))):
            msg = "reading_order must be 0..n-1 without gaps"
            raise ValueError(msg)
        return self
