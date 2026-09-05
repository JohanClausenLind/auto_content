"""Technical/mathematical animation specs (rendered by the builtin renderer or the Manim skill).

An AnimationSpec is content, not code: a typed description of a short explanatory animation that
a deterministic renderer turns into a frame sequence. The builtin renderer (Pillow) covers the
common cases offline; the opt-in Manim skill (skills/video/manim) renders the same spec with
full mathematical typesetting when the operator has set it up.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import OpaqueId, SchemaModel


class AnimationSpec(SchemaModel):
    animation_id: OpaqueId
    kind: Literal["count_up", "equation", "diagram_build"]
    title: str = Field(min_length=1, max_length=200)
    # count_up: the number counted to; equation/diagram_build: ignored.
    value: float | None = None
    unit: str = Field(default="", max_length=40)
    # equation: one line per reveal step; diagram_build: one labelled box per step.
    steps: tuple[str, ...] = Field(default=(), max_length=12)
    duration_ms: int = Field(ge=500, le=60_000)
    fps: Literal[24, 30] = 30
    width: int = Field(default=1280, ge=320, le=3840)
    height: int = Field(default=720, ge=180, le=2160)

    @model_validator(mode="after")
    def _kind_payload(self) -> AnimationSpec:
        if self.kind == "count_up" and self.value is None:
            raise ValueError("count_up animations need a value")
        if self.kind in {"equation", "diagram_build"} and len(self.steps) == 0:
            raise ValueError(f"{self.kind} animations need at least one step")
        return self
