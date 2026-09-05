"""Editorial style kit: the channel's visual identity as data, not prose.

Semantic palette, typography, and motion rules for the "technical editorial motion graphics"
look. Renderers and prompt builders consume these tokens; factual labels, equations, numbers
and captions stay deterministic vector/text overlays (Remotion/Manim) and are never baked into
AI images. Font names here are identity only — renders keep using the pinned local font files
(BrandTokens) until the named families are bundled and pinned the same way.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from content_factory.schemas.base import SchemaModel, VersionedModel

HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]


class IntRange(SchemaModel):
    lo: int = Field(ge=0)
    hi: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> IntRange:
        if self.hi < self.lo:
            msg = "range hi below lo"
            raise ValueError(msg)
        return self


class MotionRules(SchemaModel):
    """Timing grammar: animate to explain causality, never to add activity."""

    visual_change_interval_s: IntRange = IntRange(lo=2, hi=5)
    shot_length_s: IntRange = IntRange(lo=3, hi=8)
    scene_length_s: IntRange = IntRange(lo=20, hi=45)
    title_max_s: float = Field(default=1.0, gt=0, le=3)
    transition_overlap_frames: IntRange = IntRange(lo=6, hi=12)
    easing: Literal["cubic_ease_in_out"] = "cubic_ease_in_out"
    # A label appears slightly before the narrator says it and stays readable after.
    label_lead_frames: IntRange = IntRange(lo=4, hi=8)
    label_hold_after_ms: int = Field(default=500, ge=0)
    narration_handle_ms: IntRange = IntRange(lo=100, hi=250)
    music_duck_below_voice_db: IntRange = IntRange(lo=10, hi=18)
    narration_time_stretch_max_pct: float = Field(default=3.0, ge=0, le=10)
    generated_clip_seconds: IntRange = IntRange(lo=2, hi=8)
    # Photosensitive safety is a floor, not a preference (the accessibility QC enforces it).
    photosensitive_safe: Literal[True] = True


class EditorialStyleKit(VersionedModel):
    """Colors carry meaning: cyan explains, amber highlights, green confirms, coral fails."""

    kit_id: str = Field(min_length=1, max_length=64)
    background: HexColor = "#08111F"  # deep navy-black field
    ink: HexColor = "#F4F1E8"  # warm off-white text and linework
    accent: HexColor = "#47D7FF"  # cyan: main explanatory accent
    accent_warm: HexColor = "#FFB547"  # amber: secondary/highlight
    success: HexColor = "#66E39A"  # green: confirmation
    danger: HexColor = "#FF5C6C"  # coral: failure/warning/counterexample only
    font_sans: str = Field(default="IBM Plex Sans", max_length=64)  # narration-led labels
    font_mono: str = Field(default="IBM Plex Mono", max_length=64)  # code and data
    font_math: str = Field(default="STIX Two Math", max_length=64)  # equations
    grid_columns: int = Field(default=12, ge=1, le=24)
    spacing_px: int = Field(default=8, ge=1, le=64)
    motion: MotionRules = MotionRules()
    image_style_suffix: str = Field(min_length=1, max_length=1000)
