"""Campaign, deliverables, destinations (2.12, 2.13, 4.1, 21). No deliverable is a hidden master."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import (
    OpaqueId,
    SchemaModel,
    Sha256Hex,
    VersionedModel,
    WorkspaceId,
)


class InputMode(StrEnum):
    brief_first = "brief_first"
    owned_media_repurpose = "owned_media_repurpose"
    approved_copy_transform = "approved_copy_transform"
    structured_data_campaign = "structured_data_campaign"
    project_revision = "project_revision"


class AspectRatio(StrEnum):
    r16x9 = "16:9"
    r9x16 = "9:16"
    r1x1 = "1:1"
    r4x5 = "4:5"


class Language(SchemaModel):
    code: str = Field(pattern=r"^[a-z]{2,3}(-[A-Z]{2})?$")


class ProjectBrief(VersionedModel):
    brief_id: OpaqueId
    workspace_id: WorkspaceId
    input_mode: InputMode
    topic: str = Field(min_length=1, max_length=500)
    objective: str = Field(min_length=1, max_length=1000)
    audience: str = Field(default="", max_length=500)
    language: Language = Language(code="en")
    operator_assertions: tuple[str, ...] = ()  # never converted to verified truth
    source_urls: tuple[str, ...] = ()
    uploaded_source_ids: tuple[OpaqueId, ...] = ()
    pasted_copy: str | None = Field(default=None, max_length=50000)


class Destination(SchemaModel):
    """A connected destination reference plus its current capability profile revision."""

    destination_id: OpaqueId
    platform: str = Field(min_length=1, max_length=40)  # bluesky, mastodon, youtube, export...
    capability_revision: str = Field(min_length=1)


class DestinationBinding(SchemaModel):
    destination: Destination
    visibility: Literal["public", "unlisted", "private", "draft", "export_only"] = "export_only"
    schedule_at: str | None = None  # ISO instant, UTC
    timezone: str = "UTC"


class QualityGateSet(SchemaModel):
    require_citations: bool = True
    require_alt_text: bool = True
    min_font_px_1080: int = Field(default=28, ge=8)
    max_lines_per_text_block: int = Field(default=6, ge=1)
    contrast_min: float = Field(default=4.5, ge=1)
    min_visible_ms: int = Field(default=1200, ge=0)


class DeliverableBase(SchemaModel):
    deliverable_id: OpaqueId
    title: str = Field(min_length=1, max_length=200)
    intent: str = Field(default="", max_length=1000)
    destinations: tuple[DestinationBinding, ...] = ()
    brand_kit_id: OpaqueId | None = None
    template_id: str | None = None
    template_version: str | None = None
    quality_gates: QualityGateSet = QualityGateSet()
    family_id: OpaqueId | None = None  # intentional ContentFamily membership


class LongVideoSpec(DeliverableBase):
    type: Literal["long_video"] = "long_video"
    aspect: AspectRatio = AspectRatio.r16x9
    target_duration_s: tuple[int, int] = (300, 900)
    fps: Literal[24, 25, 30, 60] = 30
    narration: bool = True
    music: bool = False
    captions: Literal["sidecar", "burned_in", "both", "none"] = "sidecar"
    chapters: bool = True


class ShortVideoSpec(DeliverableBase):
    type: Literal["short_video"] = "short_video"
    aspect: AspectRatio = AspectRatio.r9x16
    target_duration_s: tuple[int, int] = (15, 60)
    fps: Literal[24, 25, 30, 60] = 30
    narration: bool = True
    music: bool = False
    captions: Literal["sidecar", "burned_in", "both", "none"] = "burned_in"


class SingleImagePostSpec(DeliverableBase):
    type: Literal["single_image_post"] = "single_image_post"
    aspect: AspectRatio = AspectRatio.r1x1
    layout: Literal["number_led", "chart_led", "headline", "quote", "comparison"] = "headline"
    caption_max_chars: int = Field(default=300, ge=1)


class CarouselSpec(DeliverableBase):
    type: Literal["carousel"] = "carousel"
    aspect: AspectRatio = AspectRatio.r4x5
    card_count: int = Field(default=6, ge=2, le=20)


class InfographicSpec(DeliverableBase):
    type: Literal["infographic"] = "infographic"
    aspect: AspectRatio = AspectRatio.r4x5
    variant: Literal["infographic", "chart_card", "quote_card", "data_card"] = "chart_card"


class TextPostSpec(DeliverableBase):
    type: Literal["text_post"] = "text_post"
    max_chars: int = Field(default=300, ge=1)


class ThreadSpec(DeliverableBase):
    type: Literal["thread"] = "thread"
    max_posts: int = Field(default=8, ge=2, le=25)
    max_chars_per_post: int = Field(default=300, ge=1)


class ArticleSpec(DeliverableBase):
    type: Literal["article"] = "article"
    target_words: tuple[int, int] = (800, 2000)
    seo_keywords: tuple[str, ...] = ()
    canonical_url: str | None = None
    inline_citations: bool = True


class NewsletterSpec(DeliverableBase):
    type: Literal["newsletter"] = "newsletter"
    provider: Literal["listmonk", "buttondown", "mailchimp", "export"] = "export"
    subject_candidates: int = Field(default=3, ge=1, le=10)


class AudioClipSpec(DeliverableBase):
    type: Literal["audio_clip"] = "audio_clip"
    target_duration_s: tuple[int, int] = (30, 180)


class AudiogramSpec(DeliverableBase):
    type: Literal["audiogram"] = "audiogram"
    aspect: AspectRatio = AspectRatio.r1x1
    target_duration_s: tuple[int, int] = (15, 60)
    fps: Literal[24, 25, 30, 60] = 30


class CoverSpec(DeliverableBase):
    type: Literal["cover"] = "cover"
    aspect: AspectRatio = AspectRatio.r16x9
    candidates: int = Field(default=3, ge=1, le=5)


class ImageSequenceSpec(DeliverableBase):
    type: Literal["image_sequence"] = "image_sequence"
    frame_count: int = Field(default=8, ge=2, le=600)
    in_between: Literal["none", "interpolate", "controlled_edits"] = "none"
    packaging: tuple[
        Literal["frames", "contact_sheet", "animated_preview", "flipbook_pdf"], ...
    ] = (
        "frames",
        "contact_sheet",
    )


ContentDeliverable = Annotated[
    LongVideoSpec
    | ShortVideoSpec
    | SingleImagePostSpec
    | CarouselSpec
    | InfographicSpec
    | TextPostSpec
    | ThreadSpec
    | ArticleSpec
    | NewsletterSpec
    | AudioClipSpec
    | AudiogramSpec
    | CoverSpec
    | ImageSequenceSpec,
    Field(discriminator="type"),
]


class DeliverableRelationship(SchemaModel):
    from_id: OpaqueId
    to_id: OpaqueId
    kind: Literal["adaptation_of", "companion_to", "cover_for", "excerpt_of"]


class StagedUpload(SchemaModel):
    """A file the operator dropped into the workspace, already sniffed and stored.

    It is carried on the campaign rather than fetched by the browser at run time because the
    project directory does not exist until the run's first activity makes it: the run materialises
    each of these into ``<project>/uploads/``, which is where the ``ingest`` stage looks. The
    ``asset_id`` is the content-addressed artifact key — the bytes cannot change under it, and
    nothing here is a path the caller chose.
    """

    asset_id: str = Field(min_length=1, max_length=300)
    filename: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._ -]*$")
    """The operator's own name for it, sanitised. Never used to decide what the file is."""
    kind: Literal["image", "video", "audio", "document", "data", "text"]
    """Sniffed from the bytes by ``ingest.uploads`` when the file arrived, never from its name.
    The sniff that decides what the run does with it happens again in the ``ingest`` stage, on
    the bytes in the project folder, so this field is a label rather than a licence."""
    size_bytes: int = Field(ge=1)
    sha256: Sha256Hex


class ContentCampaign(VersionedModel):
    campaign_id: OpaqueId
    workspace_id: WorkspaceId
    brief: ProjectBrief
    deliverables: tuple[ContentDeliverable, ...] = Field(min_length=1)
    relationships: tuple[DeliverableRelationship, ...] = ()
    channel_brain_revision: str | None = None
    execution_preset: str = "balanced"
    staged_uploads: tuple[StagedUpload, ...] = ()
    """Files dropped on the canvas that this run should start from; materialised into the
    project's uploads folder before any stage runs."""

    @model_validator(mode="after")
    def _unique_ids_and_relationships(self) -> ContentCampaign:
        ids = [d.deliverable_id for d in self.deliverables]
        if len(ids) != len(set(ids)):
            msg = "deliverable ids must be unique"
            raise ValueError(msg)
        for rel in self.relationships:
            if rel.from_id not in ids or rel.to_id not in ids:
                msg = f"relationship references unknown deliverable: {rel}"
                raise ValueError(msg)
        if self.brief.workspace_id != self.workspace_id:
            msg = "brief and campaign must belong to the same workspace"
            raise ValueError(msg)
        return self
