"""Documentary episode contracts: editorial arc, derived shorts, publish metadata.

The documentary lane turns one brief into a long-form 16:9 episode plus N vertical shorts.
The long form follows a fixed editorial arc (mystery → model → test → failure → synthesis);
each short is an independently re-edited excerpt — a self-contained 20-60 s argument with its
own hook and a fresh vertical StoryPlan — never a centre-crop of the long timeline.
Word budgets are planning aids only; the synthesized narration remains the clock (2.2).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from content_factory.schemas.base import OpaqueId, SchemaModel, VersionedModel


class EpisodeSectionKind(StrEnum):
    cold_open = "cold_open"  # concrete surprise/paradox/consequence, never a definition
    question_stakes = "question_stakes"  # what the viewer will understand and why it matters
    build_model = "build_model"  # minimum concepts and visual vocabulary
    run_system = "run_system"  # animate the mechanism/algorithm/causal chain
    change_variable = "change_variable"  # compare scenarios / counterfactual
    show_limits = "show_limits"  # limits, uncertainty, edge case, misconception
    synthesis = "synthesis"  # resolve the opening question, one memorable implication


SECTION_ORDER: tuple[EpisodeSectionKind, ...] = tuple(EpisodeSectionKind)


class EpisodeSection(SchemaModel):
    kind: EpisodeSectionKind
    purpose: str = Field(min_length=1, max_length=300)
    start_s: int = Field(ge=0)
    end_s: int = Field(gt=0)
    word_budget: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> EpisodeSection:
        if self.end_s <= self.start_s:
            msg = f"section {self.kind}: end_s must be after start_s"
            raise ValueError(msg)
        return self


class EpisodeOutline(VersionedModel):
    """Planning skeleton for a long-form episode. Sections tile the target duration exactly."""

    outline_id: OpaqueId
    deliverable_id: OpaqueId
    target_duration_s: int = Field(ge=60, le=3600)
    words_per_minute: int = Field(default=145, ge=100, le=200)
    sections: tuple[EpisodeSection, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _contiguous_arc(self) -> EpisodeOutline:
        kinds = tuple(s.kind for s in self.sections)
        expected = SECTION_ORDER[: len(kinds)]
        if kinds != expected and kinds != SECTION_ORDER:
            msg = f"sections must follow the editorial arc {[k.value for k in SECTION_ORDER]}"
            raise ValueError(msg)
        cursor = 0
        for s in self.sections:
            if s.start_s != cursor:
                msg = f"section {s.kind} starts at {s.start_s}s, expected {cursor}s (no gaps)"
                raise ValueError(msg)
            cursor = s.end_s
        if cursor != self.target_duration_s:
            msg = f"sections cover {cursor}s but target duration is {self.target_duration_s}s"
            raise ValueError(msg)
        return self

    def total_word_budget(self) -> int:
        return sum(s.word_budget for s in self.sections)


class ShortExcerpt(SchemaModel):
    """One derived short: a contiguous, self-contained run of long-form beats plus its hook.

    ``hook_text`` equal to the first beat's display text means "verbatim" — the claims on that
    beat survive into the short. A rewritten hook is a new statement: the derived plan drops the
    claim links on that beat rather than pretending the citation still holds.
    """

    short_deliverable_id: OpaqueId
    source_beat_ids: tuple[OpaqueId, ...] = Field(min_length=1)
    hook_text: str = Field(min_length=1, max_length=300)
    duration_ms: int = Field(ge=1000)
    claim_ids: tuple[OpaqueId, ...] = ()


class ShortsPlan(VersionedModel):
    plan_id: OpaqueId
    long_deliverable_id: OpaqueId
    excerpts: tuple[ShortExcerpt, ...] = ()

    @model_validator(mode="after")
    def _unique_targets(self) -> ShortsPlan:
        ids = [e.short_deliverable_id for e in self.excerpts]
        if len(ids) != len(set(ids)):
            msg = "each short deliverable gets exactly one excerpt"
            raise ValueError(msg)
        return self


class ChapterMarker(SchemaModel):
    at_s: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=100)


class EpisodeMetadata(VersionedModel):
    """Publishing metadata for a long-form episode. Generating this never publishes anything:
    upload stays behind the existing distribution gates and explicit operator approval."""

    deliverable_id: OpaqueId
    title_candidates: tuple[str, ...] = Field(min_length=1, max_length=5)
    description: str = Field(min_length=1, max_length=5000)
    chapters: tuple[ChapterMarker, ...] = ()
    tags: tuple[str, ...] = Field(default=(), max_length=20)
    attribution: tuple[str, ...] = ()  # one line per source: title — publisher — url (accessed)
    synthetic_media_disclosure: bool = True
    disclosure_text: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def _chapters_valid(self) -> EpisodeMetadata:
        if self.chapters:
            if self.chapters[0].at_s != 0:
                msg = "the first chapter must start at 0s"
                raise ValueError(msg)
            starts = [c.at_s for c in self.chapters]
            if starts != sorted(set(starts)):
                msg = "chapter markers must be strictly increasing"
                raise ValueError(msg)
        if self.synthetic_media_disclosure and not self.disclosure_text:
            msg = "a required synthetic-media disclosure needs its disclosure text"
            raise ValueError(msg)
        return self
