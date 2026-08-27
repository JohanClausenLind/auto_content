"""Scene grammar (section 9), beats, and the compiled timeline (2.2, 2.3).

Models supply semantic parameters and narration anchors; deterministic code compiles layout and
timing. A scene may display a factual number only through a dataset/claim reference.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import OpaqueId, SchemaModel, Sha256Hex, VersionedModel


class ChartKind(StrEnum):
    line = "line"
    area = "area"
    bar = "bar"
    stacked_bar = "stacked_bar"
    horizontal_bar = "horizontal_bar"
    scatter = "scatter"
    bubble = "bubble"
    histogram = "histogram"
    waterfall = "waterfall"
    donut = "donut"


class DataRef(SchemaModel):
    """Every on-screen figure resolves through a dataset (and optionally a claim) to evidence."""

    dataset_id: OpaqueId
    claim_id: OpaqueId | None = None
    column: str | None = None
    row_key: str | None = None


class TextRef(SchemaModel):
    """Display text; if it carries a factual statement it must link to claims."""

    text: str = Field(min_length=1, max_length=2000)
    claim_ids: tuple[OpaqueId, ...] = ()


class SceneBase(SchemaModel):
    scene_id: OpaqueId
    beat_id: OpaqueId  # narration/editorial beat that anchors this scene
    variant: str = Field(default="default", max_length=64)
    emphasis: tuple[str, ...] = ()


class TitleScene(SceneBase):
    kind: Literal["title"] = "title"
    title: TextRef
    subtitle: TextRef | None = None


class SectionIntroScene(SceneBase):
    kind: Literal["section_intro"] = "section_intro"
    label: TextRef
    heading: TextRef


class BigNumberScene(SceneBase):
    kind: Literal["big_number"] = "big_number"
    value: DataRef
    unit: str = Field(default="", max_length=32)
    label: TextRef
    context: TextRef | None = None


class ChartScene(SceneBase):
    kind: Literal["chart"] = "chart"
    chart: ChartKind
    data: DataRef
    x: str
    y: tuple[str, ...] = Field(min_length=1)
    title: TextRef
    caption: TextRef | None = None
    zero_baseline: bool = True
    dual_axis: bool = False
    truncation_disclosure: str | None = None
    encoding: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _semantics(self) -> ChartScene:
        if (
            self.chart
            in {ChartKind.bar, ChartKind.stacked_bar, ChartKind.horizontal_bar, ChartKind.histogram}
            and not self.zero_baseline
        ):
            if not self.truncation_disclosure:
                msg = "bar-family charts need a zero baseline or an explicit truncation disclosure"
                raise ValueError(msg)
        if self.chart == ChartKind.donut and len(self.y) > 1:
            msg = "donut charts take exactly one series"
            raise ValueError(msg)
        return self


class RankingScene(SceneBase):
    kind: Literal["ranking"] = "ranking"
    data: DataRef
    label_column: str
    value_column: str
    top_n: int = Field(default=5, ge=1, le=10)
    title: TextRef


class ComparisonScene(SceneBase):
    kind: Literal["comparison"] = "comparison"
    left: TextRef
    right: TextRef
    left_value: DataRef | None = None
    right_value: DataRef | None = None
    title: TextRef


class DataTableScene(SceneBase):
    kind: Literal["data_table"] = "data_table"
    data: DataRef
    columns: tuple[str, ...] = Field(min_length=1, max_length=6)
    max_rows: int = Field(default=6, ge=1, le=10)
    title: TextRef


class TimelineEvent(SchemaModel):
    date_label: str = Field(max_length=40)
    text: TextRef


class TimelineScene(SceneBase):
    kind: Literal["timeline"] = "timeline"
    events: tuple[TimelineEvent, ...] = Field(min_length=2, max_length=8)
    title: TextRef


class MapScene(SceneBase):
    kind: Literal["map"] = "map"
    region: str = Field(min_length=1)  # topojson asset id
    projection: Literal["equalEarth", "mercator", "albersUsa", "naturalEarth1"] = "equalEarth"
    data: DataRef | None = None
    title: TextRef


class DiagramNode(SchemaModel):
    node_id: str = Field(min_length=1, max_length=40)
    label: TextRef


class DiagramEdge(SchemaModel):
    from_id: str
    to_id: str
    label: str | None = Field(default=None, max_length=60)


class FlowDiagramScene(SceneBase):
    kind: Literal["flow_diagram"] = "flow_diagram"
    nodes: tuple[DiagramNode, ...] = Field(min_length=2, max_length=12)
    edges: tuple[DiagramEdge, ...] = ()
    title: TextRef


class RelationshipDiagramScene(SceneBase):
    kind: Literal["relationship_diagram"] = "relationship_diagram"
    nodes: tuple[DiagramNode, ...] = Field(min_length=2, max_length=12)
    edges: tuple[DiagramEdge, ...] = ()
    title: TextRef


class ImageScene(SceneBase):
    kind: Literal["image"] = "image"
    asset_id: OpaqueId
    alt_text: str = Field(min_length=1, max_length=500)
    caption: TextRef | None = None
    motion: Literal["none", "slow_push", "slow_pull"] = "none"


class ScreenshotScene(SceneBase):
    kind: Literal["screenshot"] = "screenshot"
    asset_id: OpaqueId
    alt_text: str = Field(min_length=1, max_length=500)
    highlight_region: tuple[float, float, float, float] | None = None
    source_id: OpaqueId


class QuoteScene(SceneBase):
    kind: Literal["quote"] = "quote"
    quote: TextRef
    attribution: TextRef
    source_id: OpaqueId


class DefinitionScene(SceneBase):
    kind: Literal["definition"] = "definition"
    term: TextRef
    definition: TextRef


class BulletSequenceScene(SceneBase):
    kind: Literal["bullet_sequence"] = "bullet_sequence"
    title: TextRef
    bullets: tuple[TextRef, ...] = Field(min_length=1, max_length=6)


class CalloutScene(SceneBase):
    kind: Literal["callout"] = "callout"
    text: TextRef
    tone: Literal["neutral", "warning", "positive"] = "neutral"


class SourceCardScene(SceneBase):
    kind: Literal["source_card"] = "source_card"
    source_ids: tuple[OpaqueId, ...] = Field(min_length=1, max_length=12)


class ChapterTransitionScene(SceneBase):
    kind: Literal["chapter_transition"] = "chapter_transition"
    label: TextRef


class ManimAssetScene(SceneBase):
    kind: Literal["manim_asset"] = "manim_asset"
    asset_id: OpaqueId
    alt_text: str = Field(min_length=1, max_length=500)


class OutroScene(SceneBase):
    kind: Literal["outro"] = "outro"
    text: TextRef
    cta: TextRef | None = None


SceneSpec = Annotated[
    TitleScene
    | SectionIntroScene
    | BigNumberScene
    | ChartScene
    | RankingScene
    | ComparisonScene
    | DataTableScene
    | TimelineScene
    | MapScene
    | FlowDiagramScene
    | RelationshipDiagramScene
    | ImageScene
    | ScreenshotScene
    | QuoteScene
    | DefinitionScene
    | BulletSequenceScene
    | CalloutScene
    | SourceCardScene
    | ChapterTransitionScene
    | ManimAssetScene
    | OutroScene,
    Field(discriminator="kind"),
]


class WordTiming(SchemaModel):
    word: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> WordTiming:
        if self.end_ms < self.start_ms:
            msg = "end_ms before start_ms"
            raise ValueError(msg)
        return self


class VisualBeat(SchemaModel):
    """An editorial beat: 1–3 sentences of narration and the scene that visualizes it."""

    beat_id: OpaqueId
    order: int = Field(ge=0)
    display_text: str = Field(min_length=1, max_length=1000)
    spoken_text: str | None = None
    claim_ids: tuple[OpaqueId, ...] = ()
    # Measured from narration (2.2). None until audio exists; then durations are compiled from it.
    measured_start_ms: int | None = Field(default=None, ge=0)
    measured_end_ms: int | None = Field(default=None, ge=0)
    words: tuple[WordTiming, ...] = ()
    # Silent deliverables (no narration) use a planned duration instead.
    planned_duration_ms: int | None = Field(default=None, ge=200)


class StoryPlan(VersionedModel):
    plan_id: OpaqueId
    deliverable_id: OpaqueId
    fps: Literal[24, 25, 30, 60]
    width: int = Field(ge=16)
    height: int = Field(ge=16)
    beats: tuple[VisualBeat, ...] = Field(min_length=1)
    scenes: tuple[SceneSpec, ...] = Field(min_length=1)
    handle_ms: int = Field(default=250, ge=0, le=2000)  # padding after speech before the cut
    min_scene_ms: int = Field(default=1200, ge=200)

    @model_validator(mode="after")
    def _scenes_anchor_beats(self) -> StoryPlan:
        beat_ids = {b.beat_id for b in self.beats}
        for s in self.scenes:
            if s.beat_id not in beat_ids:
                msg = f"scene {s.scene_id} anchors unknown beat {s.beat_id}"
                raise ValueError(msg)
        orders = [b.order for b in self.beats]
        if orders != sorted(orders) or len(set(orders)) != len(orders):
            msg = "beats must have unique increasing order"
            raise ValueError(msg)
        return self


class CompiledScene(SchemaModel):
    scene_id: OpaqueId
    beat_id: OpaqueId
    start_frame: int = Field(ge=0)
    duration_frames: int = Field(ge=1)
    transition_in_frames: int = Field(default=0, ge=0)
    # Word cue frames relative to the scene start (for captions / animation cues).
    word_cues: tuple[tuple[int, str], ...] = ()


class AudioCue(SchemaModel):
    beat_id: OpaqueId
    asset_sha256: Sha256Hex | None = None  # None for silent/mock timelines
    start_frame: int = Field(ge=0)
    duration_frames: int = Field(ge=1)


class CompiledTimeline(VersionedModel):
    """Renderer truth: integer frames only. Owned by the timeline compiler."""

    timeline_id: OpaqueId
    plan_id: OpaqueId
    fps: Literal[24, 25, 30, 60]
    width: int
    height: int
    total_frames: int = Field(ge=1)
    scenes: tuple[CompiledScene, ...] = Field(min_length=1)
    audio: tuple[AudioCue, ...] = ()
    compiler_version: str
    plan_hash: Sha256Hex

    @model_validator(mode="after")
    def _contiguous(self) -> CompiledTimeline:
        cursor = 0
        for s in self.scenes:
            if s.start_frame != cursor:
                msg = f"scene {s.scene_id} starts at {s.start_frame}, expected {cursor} (no gaps/overlaps)"
                raise ValueError(msg)
            cursor += s.duration_frames
        if cursor != self.total_frames:
            msg = f"scenes cover {cursor} frames but total_frames is {self.total_frames}"
            raise ValueError(msg)
        return self
