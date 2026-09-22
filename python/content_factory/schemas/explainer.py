"""Explainer contracts: evidence, captures, script, narration, visuals, timeline, review."""

from __future__ import annotations

import hashlib
from itertools import pairwise
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import (
    OpaqueId,
    SchemaModel,
    Sha256Hex,
    VersionedModel,
    canonical_dumps,
)
from content_factory.schemas.research import EvidenceLocator, RightsStatus, SourceClass

# Provenance fields say when we looked; they never change what a source says, so no hash uses them.
PROVENANCE_FIELDS = frozenset(
    {
        "accessed_at",
        "quoted_at",
        "checked_at",
        "captured_at",
        "frozen_at",
        "locked_at",
        "created_at",
    }
)

EpistemicClass = Literal["observation", "interpretation", "forecast", "illustrative_assumption"]
UncertaintyKind = Literal["exact", "rounded", "estimate", "interval"]
Section = Literal[
    "cold_open",
    "question_stakes",
    "build_model",
    "run_system",
    "change_variable",
    "show_limits",
    "synthesis",
    "sponsor",
]
SECTION_ORDER: tuple[str, ...] = (
    "cold_open",
    "question_stakes",
    "build_model",
    "run_system",
    "change_variable",
    "show_limits",
    "synthesis",
    "sponsor",
)


def hash_without_provenance(payload: Any) -> str:
    """Content identity of a contract: canonical JSON with provenance timestamps removed."""
    return hashlib.sha256(canonical_dumps(strip_provenance(payload)).encode()).hexdigest()


def strip_provenance(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: strip_provenance(v) for k, v in value.items() if k not in PROVENANCE_FIELDS}
    if isinstance(value, list):
        return [strip_provenance(v) for v in value]
    return value


def tokenize(text: str) -> tuple[str, ...]:
    """The one tokenizer cues and alignments share: whitespace words, punctuation attached."""
    return tuple(text.split())


class Quantity(SchemaModel):
    """A finite magnitude with a unit; comparisons convert units, never compare unit strings."""

    magnitude: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=40)
    uncertainty: UncertaintyKind = "exact"
    low: float | None = Field(default=None, allow_inf_nan=False)
    high: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _interval_brackets_magnitude(self) -> Quantity:
        if self.uncertainty == "interval":
            if self.low is None or self.high is None:
                msg = "interval uncertainty needs both low and high"
                raise ValueError(msg)
            if not self.low <= self.magnitude <= self.high:
                msg = f"interval [{self.low}, {self.high}] does not bracket {self.magnitude}"
                raise ValueError(msg)
        elif self.low is not None or self.high is not None:
            msg = f"low/high only apply to interval uncertainty, not {self.uncertainty!r}"
            raise ValueError(msg)
        return self


class ChannelEpisodeFormat(SchemaModel):
    min_length_s: int = Field(ge=30)
    max_length_s: int = Field(ge=30)
    width: int = Field(ge=320)
    height: int = Field(ge=180)
    fps: int = Field(ge=24, le=60)
    language: str = Field(default="en", min_length=2, max_length=8)

    @model_validator(mode="after")
    def _ordered(self) -> ChannelEpisodeFormat:
        if self.max_length_s < self.min_length_s:
            msg = "max_length_s below min_length_s"
            raise ValueError(msg)
        return self


class ChannelNarrationPolicy(SchemaModel):
    recorded_seconds_min: int = Field(ge=0)
    recorded_seconds_max: int = Field(ge=0)
    voice_kind: Literal["creator_recorded_plus_clone", "creator_recorded", "clone_only"]

    @model_validator(mode="after")
    def _ordered(self) -> ChannelNarrationPolicy:
        if self.recorded_seconds_max < self.recorded_seconds_min:
            msg = "recorded_seconds_max below recorded_seconds_min"
            raise ValueError(msg)
        return self


class ChannelProfile(VersionedModel):
    """Who the channel is for and what an episode is; the planner and compiler read it."""

    channel_id: OpaqueId
    name: str = Field(min_length=1, max_length=120)
    audience: str = Field(min_length=1, max_length=500)
    topic_domain: tuple[str, ...] = Field(min_length=1)
    excluded_topics: tuple[str, ...] = ()
    episode_format: ChannelEpisodeFormat
    narration: ChannelNarrationPolicy
    commercial_stance: str = Field(default="", max_length=200)
    sponsors_allowed: bool = False


# --- evidence ---


class EvidenceSource(SchemaModel):
    """One publication we read; content_sha256 is what it said, accessed_at is when we looked."""

    source_id: OpaqueId
    url: str = Field(min_length=1, max_length=2000)
    canonical_url: str = Field(min_length=1, max_length=2000)
    publisher: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=500)
    author: str = Field(default="", max_length=200)
    published_at: str | None = None
    accessed_at: str = Field(min_length=4)
    content_sha256: Sha256Hex
    capture_id: OpaqueId | None = None
    source_class: SourceClass = SourceClass.unknown
    rights_status: RightsStatus = RightsStatus.quotable_excerpt


class EvidenceItem(SchemaModel):
    """The exact passage or cell a claim rests on, with enough context to keep its qualifiers."""

    item_id: OpaqueId
    source_id: OpaqueId
    passage: str = Field(min_length=1, max_length=1200)
    context_before: str = Field(default="", max_length=400)
    context_after: str = Field(default="", max_length=400)
    locator: EvidenceLocator
    unit: str = Field(default="", max_length=40)
    geography: str = Field(default="", max_length=120)
    time_basis: str = Field(default="", max_length=80)
    quoted_at: str = Field(min_length=4)


class Corroboration(SchemaModel):
    status: Literal["corroborated", "single_source", "conflicting", "not_required"]
    independent_source_ids: tuple[OpaqueId, ...] = ()
    note: str = Field(default="", max_length=300)


class ClaimOperand(SchemaModel):
    kind: Literal["claim"]
    claim_id: OpaqueId


class ConstantOperand(SchemaModel):
    kind: Literal["constant"]
    value: Quantity


Operand = Annotated[ClaimOperand | ConstantOperand, Field(discriminator="kind")]


class Calculation(SchemaModel):
    """A derived value the compiler recomputes; the script may not carry one it cannot reproduce."""

    calc_id: OpaqueId
    op: Literal["add", "sub", "mul", "div"]
    operands: tuple[Operand, ...] = Field(min_length=2)
    result_claim_id: OpaqueId

    @model_validator(mode="after")
    def _binary_ops_take_two(self) -> Calculation:
        if self.op in {"sub", "div"} and len(self.operands) != 2:
            msg = f"{self.op} takes exactly two operands, got {len(self.operands)}"
            raise ValueError(msg)
        return self


class Claim(SchemaModel):
    """One statement the episode may make, classified, valued, and tied to its evidence."""

    claim_id: OpaqueId
    statement: str = Field(min_length=1, max_length=300)
    short_label: str = Field(default="", max_length=40)
    epistemic_class: EpistemicClass
    evidence_ids: tuple[OpaqueId, ...] = ()
    value: Quantity | None = None
    geography: str = Field(default="", max_length=120)
    time_basis: str = Field(default="", max_length=80)
    calculation_id: OpaqueId | None = None
    consequential: bool = False
    disputed: bool = False
    corroboration: Corroboration = Corroboration(status="not_required")
    stable: bool = False
    rationale: str = Field(min_length=1, max_length=200)
    checked_at: str = Field(min_length=4)

    @model_validator(mode="after")
    def _evidence_or_derivation(self) -> Claim:
        derived = self.calculation_id is not None
        illustrative = self.epistemic_class == "illustrative_assumption"
        if not self.evidence_ids and not derived and not illustrative:
            msg = (
                f"claim {self.claim_id} has no evidence_ids; add evidence, a calculation_id, "
                "or classify it illustrative_assumption"
            )
            raise ValueError(msg)
        if derived and self.value is None:
            msg = f"claim {self.claim_id} is derived by a calculation but has no value"
            raise ValueError(msg)
        return self


class DatasetColumn(SchemaModel):
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["quantitative", "temporal", "ordinal", "nominal"]
    unit: str = Field(default="", max_length=40)


class DatasetRow(SchemaModel):
    """One row; every quantitative cell must equal a claim value listed in claim_ids."""

    key: str = Field(min_length=1, max_length=80)
    values: tuple[float | str | None, ...]
    claim_ids: tuple[OpaqueId, ...] = ()


class EvidenceDataset(SchemaModel):
    dataset_id: OpaqueId
    title: str = Field(default="", max_length=120)
    columns: tuple[DatasetColumn, ...] = Field(min_length=1)
    rows: tuple[DatasetRow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _rectangular(self) -> EvidenceDataset:
        width = len(self.columns)
        for row in self.rows:
            if len(row.values) != width:
                msg = (
                    f"dataset {self.dataset_id} row {row.key!r} has {len(row.values)} cells, "
                    f"expected {width}"
                )
                raise ValueError(msg)
        keys = [r.key for r in self.rows]
        if len(set(keys)) != len(keys):
            msg = f"dataset {self.dataset_id} has duplicate row keys"
            raise ValueError(msg)
        return self


class EvidencePack(VersionedModel):
    """Every source, passage, claim, dataset and calculation an episode may use, frozen first."""

    pack_id: OpaqueId
    topic: str = Field(min_length=1, max_length=500)
    language: str = Field(default="en", min_length=2, max_length=8)
    frozen_at: str | None = None
    sources: tuple[EvidenceSource, ...] = Field(min_length=1)
    items: tuple[EvidenceItem, ...] = ()
    claims: tuple[Claim, ...] = Field(min_length=1)
    datasets: tuple[EvidenceDataset, ...] = ()
    calculations: tuple[Calculation, ...] = ()

    @model_validator(mode="after")
    def _cross_references(self) -> EvidencePack:
        source_ids = _unique_ids("source", [s.source_id for s in self.sources])
        item_ids = _unique_ids("evidence item", [i.item_id for i in self.items])
        claim_ids = _unique_ids("claim", [c.claim_id for c in self.claims])
        calc_ids = _unique_ids("calculation", [c.calc_id for c in self.calculations])
        _unique_ids("dataset", [d.dataset_id for d in self.datasets])
        publisher_of = {s.source_id: s.publisher for s in self.sources}
        for item in self.items:
            _must_exist(item.source_id, source_ids, f"evidence item {item.item_id}", "source")
        results: dict[str, str] = {}
        for calc in self.calculations:
            if calc.result_claim_id in results:
                msg = (
                    f"claim {calc.result_claim_id} is the result of calculations "
                    f"{results[calc.result_claim_id]} and {calc.calc_id}; keep one"
                )
                raise ValueError(msg)
            results[calc.result_claim_id] = calc.calc_id
        derived = {c.claim_id: c.calculation_id for c in self.claims}
        for calc in self.calculations:
            if derived.get(calc.result_claim_id) != calc.calc_id:
                msg = (
                    f"calculation {calc.calc_id} produces claim {calc.result_claim_id}, "
                    f"but that claim does not name it as calculation_id"
                )
                raise ValueError(msg)
        for claim in self.claims:
            for evidence_id in claim.evidence_ids:
                _must_exist(evidence_id, item_ids, f"claim {claim.claim_id}", "evidence item")
            if claim.calculation_id is not None:
                _must_exist(
                    claim.calculation_id, calc_ids, f"claim {claim.claim_id}", "calculation"
                )
                if results.get(claim.claim_id) != claim.calculation_id:
                    msg = (
                        f"claim {claim.claim_id} names calculation {claim.calculation_id}, "
                        f"but that calculation's result_claim_id is not {claim.claim_id}"
                    )
                    raise ValueError(msg)
            for sid in claim.corroboration.independent_source_ids:
                _must_exist(sid, source_ids, f"claim {claim.claim_id} corroboration", "source")
            if claim.consequential and claim.disputed:
                publishers = {publisher_of[s] for s in claim.corroboration.independent_source_ids}
                if claim.corroboration.status != "corroborated" or len(publishers) < 2:
                    msg = (
                        f"claim {claim.claim_id} is consequential and disputed: it needs "
                        "corroboration.status='corroborated' with sources from two publishers, "
                        "or downgrade it to the source's attributed assertion"
                    )
                    raise ValueError(msg)
        for calc in self.calculations:
            _must_exist(
                calc.result_claim_id, claim_ids, f"calculation {calc.calc_id}", "result claim"
            )
            for operand in calc.operands:
                if isinstance(operand, ClaimOperand):
                    _must_exist(operand.claim_id, claim_ids, f"calculation {calc.calc_id}", "claim")
        for dataset in self.datasets:
            for row in dataset.rows:
                for cid in row.claim_ids:
                    _must_exist(
                        cid, claim_ids, f"dataset {dataset.dataset_id} row {row.key!r}", "claim"
                    )
        return self

    def pack_hash(self) -> str:
        """Identity of the evidence content; re-fetching an unchanged source leaves it unchanged."""
        return hash_without_provenance(self.model_dump(mode="json"))

    def provenance_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()

    def frozen(self, at: str) -> EvidencePack:
        return self.model_copy(update={"frozen_at": at})

    def claim(self, claim_id: str) -> Claim:
        for claim in self.claims:
            if claim.claim_id == claim_id:
                return claim
        msg = f"unknown claim {claim_id}"
        raise KeyError(msg)


# --- source captures ---


class PageRect(SchemaModel):
    x: float = Field(ge=0, allow_inf_nan=False)
    y: float = Field(ge=0, allow_inf_nan=False)
    width: float = Field(gt=0, allow_inf_nan=False)
    height: float = Field(gt=0, allow_inf_nan=False)


class DomRangeLocator(SchemaModel):
    kind: Literal["dom_range"]
    start_path: str = Field(min_length=1, max_length=1000)
    start_offset: int = Field(ge=0)
    end_path: str = Field(min_length=1, max_length=1000)
    end_offset: int = Field(ge=0)


class PdfLocator(SchemaModel):
    kind: Literal["pdf"]
    page: int = Field(ge=1)


QuoteLocator = Annotated[DomRangeLocator | PdfLocator, Field(discriminator="kind")]


class CaptureSection(SchemaModel):
    section_id: OpaqueId
    heading: str = Field(default="", max_length=300)
    order: int = Field(ge=0)
    scroll_y_px: float = Field(ge=0, allow_inf_nan=False)


class CaptureQuote(SchemaModel):
    """A passage located in the capture; line_rects are page pixels, occurrence picks a repeat."""

    quote_id: OpaqueId
    section_id: OpaqueId
    text: str = Field(min_length=1, max_length=1200)
    context_before: str = Field(default="", max_length=400)
    context_after: str = Field(default="", max_length=400)
    locator: QuoteLocator
    occurrence_index: int = Field(ge=0)
    line_rects: tuple[PageRect, ...] = Field(min_length=1)
    ocr_verified: bool = False
    ocr_similarity: float | None = Field(default=None, ge=0, le=1)
    claim_ids: tuple[OpaqueId, ...] = ()

    @model_validator(mode="after")
    def _verified_has_score(self) -> CaptureQuote:
        if self.ocr_verified and self.ocr_similarity is None:
            msg = f"quote {self.quote_id} is ocr_verified without an ocr_similarity"
            raise ValueError(msg)
        return self


class Viewport(SchemaModel):
    width: int = Field(ge=320)
    height: int = Field(ge=240)


class SourceCaptureManifest(VersionedModel):
    """A signed capture of one source with its sections and located quotes; rendering reads this."""

    capture_id: OpaqueId
    source_id: OpaqueId
    url: str = Field(min_length=1, max_length=2000)
    publisher: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=500)
    author: str = Field(default="", max_length=200)
    published_at: str | None = None
    captured_at: str = Field(min_length=4)
    capture_kind: Literal["wacz", "pdf"]
    artifact_sha256: Sha256Hex
    signed: bool = False
    signature_domain: str = Field(default="", max_length=253)
    tls_certificate_sha256: str | None = None
    viewport: Viewport
    page_height_px: float = Field(gt=0, allow_inf_nan=False)
    text_sha256: Sha256Hex
    extractor: str = Field(min_length=1, max_length=80)
    extractor_version: str = Field(min_length=1, max_length=40)
    sections: tuple[CaptureSection, ...] = Field(min_length=1)
    quotes: tuple[CaptureQuote, ...] = ()
    claim_ids: tuple[OpaqueId, ...] = ()

    @model_validator(mode="after")
    def _quotes_in_sections(self) -> SourceCaptureManifest:
        section_ids = _unique_ids("section", [s.section_id for s in self.sections])
        _unique_ids("quote", [q.quote_id for q in self.quotes])
        for quote in self.quotes:
            _must_exist(quote.section_id, section_ids, f"quote {quote.quote_id}", "section")
        return self


# --- script ---


class TitlePromise(SchemaModel):
    title: str = Field(min_length=1, max_length=100)
    thumbnail_promise: str = Field(min_length=1, max_length=140)
    claim_ids: tuple[OpaqueId, ...] = Field(min_length=1)


class ScriptSegment(SchemaModel):
    """One spoken unit; tokens are the stable cue coordinates, derived by ``tokenize``."""

    segment_id: OpaqueId
    section: Section
    spoken_text: str = Field(min_length=1, max_length=1200)
    display_text: str | None = Field(default=None, max_length=400)
    tokens: tuple[str, ...] = Field(min_length=1)
    claim_ids: tuple[OpaqueId, ...] = ()
    delivery: Literal["recorded", "synthesized", "either"] = "either"

    @model_validator(mode="after")
    def _tokens_match_text(self) -> ScriptSegment:
        if self.tokens != tokenize(self.spoken_text):
            msg = f"segment {self.segment_id}: tokens must equal tokenize(spoken_text)"
            raise ValueError(msg)
        return self


class SponsorSegment(SchemaModel):
    segment_id: OpaqueId
    sponsor_name: str = Field(min_length=1, max_length=120)
    spoken_text: str = Field(min_length=1, max_length=1200)
    disclosure_text: str = Field(min_length=1, max_length=200)


class ScriptPlan(VersionedModel):
    """The locked script: segments in arc order, bound to one frozen evidence pack."""

    script_id: OpaqueId
    channel_id: OpaqueId
    pack_id: OpaqueId
    pack_hash: Sha256Hex
    question: str = Field(min_length=1, max_length=300)
    contribution: str = Field(min_length=1, max_length=500)
    promises: tuple[TitlePromise, ...] = Field(min_length=1, max_length=3)
    segments: tuple[ScriptSegment, ...] = Field(min_length=1)
    sponsor: SponsorSegment | None = None
    locked_at: str | None = None

    @model_validator(mode="after")
    def _arc_order(self) -> ScriptPlan:
        ids = [s.segment_id for s in self.segments]
        if self.sponsor is not None:
            ids.append(self.sponsor.segment_id)
        _unique_ids("segment", ids)
        last = -1
        for segment in self.segments:
            if segment.section == "sponsor":
                msg = f"segment {segment.segment_id}: sponsor copy goes in ScriptPlan.sponsor"
                raise ValueError(msg)
            index = SECTION_ORDER.index(segment.section)
            if index < last:
                msg = (
                    f"segment {segment.segment_id} ({segment.section}) comes after a later "
                    "section; segments must follow the arc order"
                )
                raise ValueError(msg)
            last = index
        return self

    def script_hash(self) -> str:
        return hash_without_provenance(self.model_dump(mode="json"))

    def segment(self, segment_id: str) -> ScriptSegment:
        for segment in self.segments:
            if segment.segment_id == segment_id:
                return segment
        msg = f"unknown segment {segment_id}"
        raise KeyError(msg)


# --- narration ---


class TokenRef(SchemaModel):
    segment_id: OpaqueId
    token_index: int = Field(ge=0)


class AlignedWord(SchemaModel):
    segment_id: OpaqueId
    token_index: int = Field(ge=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> AlignedWord:
        if self.end_ms < self.start_ms:
            msg = "word end before start"
            raise ValueError(msg)
        return self


class Alignment(SchemaModel):
    """Forced alignment over verified text; unmatched and low-confidence spans stay explicit."""

    aligner: str = Field(min_length=1, max_length=60)
    aligner_version: str = Field(min_length=1, max_length=40)
    model_id: str = Field(default="", max_length=120)
    words: tuple[AlignedWord, ...] = ()
    unmatched: tuple[TokenRef, ...] = ()
    low_confidence: tuple[TokenRef, ...] = ()


class VoiceSpec(SchemaModel):
    kind: Literal["creator_recorded", "creator_clone", "designed", "preset"]
    voice_id: str = Field(min_length=1, max_length=80)
    model_id: str = Field(default="", max_length=120)
    model_revision: str = Field(default="", max_length=80)


class NarrationTake(SchemaModel):
    """Accepted audio for one or more whole segments; the hash is the audio, not the text."""

    take_id: OpaqueId
    segment_ids: tuple[OpaqueId, ...] = Field(min_length=1)
    kind: Literal["recorded", "synthesized"]
    audio_sha256: Sha256Hex
    start_ms: int = Field(default=0, ge=0)
    duration_ms: int = Field(ge=1)
    sample_rate_hz: int = Field(ge=8000)
    transcript_similarity: float | None = Field(default=None, ge=0, le=1)
    alignment: Alignment | None = None

    @model_validator(mode="after")
    def _words_inside_take(self) -> NarrationTake:
        if self.alignment is None:
            return self
        segment_ids = set(self.segment_ids)
        for word in self.alignment.words:
            if word.segment_id not in segment_ids:
                msg = (
                    f"take {self.take_id}: word timing for segment {word.segment_id} is outside it"
                )
                raise ValueError(msg)
            if word.end_ms > self.duration_ms:
                msg = (
                    f"take {self.take_id}: word ends at {word.end_ms} ms past {self.duration_ms} ms"
                )
                raise ValueError(msg)
        return self


class NarrationManifest(VersionedModel):
    """Which take speaks which segments, with alignment, for one locked script."""

    manifest_id: OpaqueId
    script_id: OpaqueId
    script_hash: Sha256Hex
    voice: VoiceSpec
    takes: tuple[NarrationTake, ...] = Field(min_length=1)
    stem_sha256: Sha256Hex | None = None
    total_duration_ms: int = Field(ge=1)
    created_at: str = Field(min_length=4)

    @model_validator(mode="after")
    def _one_take_per_segment(self) -> NarrationManifest:
        _unique_ids("take", [t.take_id for t in self.takes])
        seen: dict[str, str] = {}
        for take in self.takes:
            for sid in take.segment_ids:
                if sid in seen:
                    msg = (
                        f"segment {sid} is spoken by takes {seen[sid]} and {take.take_id}; keep one"
                    )
                    raise ValueError(msg)
                seen[sid] = take.take_id
        return self


# --- visual spec ---

CueRelation = Literal["before", "on", "after"]
DurationClass = Literal["beat", "short", "medium", "long"]
TemplateName = Literal["chart", "diagram", "text", "source_document"]


class Cue(SchemaModel):
    """A narration anchor: a token span of one segment, never a quoted phrase."""

    segment_id: OpaqueId
    token_start: int = Field(ge=0)
    token_end: int = Field(ge=0)
    relation: CueRelation = "on"
    duration_class: DurationClass = "short"

    @model_validator(mode="after")
    def _ordered(self) -> Cue:
        if self.token_end < self.token_start:
            msg = "cue token_end before token_start"
            raise ValueError(msg)
        return self


class Entity(SchemaModel):
    """A thing with persistent identity across scenes: same colour, label and place everywhere."""

    entity_id: OpaqueId
    label: str = Field(min_length=1, max_length=80)
    short_label: str = Field(default="", max_length=40)
    kind: Literal["series", "node", "edge", "value", "text", "quote", "region", "annotation"]
    claim_ids: tuple[OpaqueId, ...] = ()


class AssetRef(SchemaModel):
    asset_id: OpaqueId
    kind: Literal["dataset", "capture", "image", "formula"]
    sha256: Sha256Hex
    dataset_id: OpaqueId | None = None
    capture_id: OpaqueId | None = None

    @model_validator(mode="after")
    def _kind_reference(self) -> AssetRef:
        if self.kind == "dataset" and self.dataset_id is None:
            msg = f"asset {self.asset_id}: a dataset asset needs dataset_id"
            raise ValueError(msg)
        if self.kind == "capture" and self.capture_id is None:
            msg = f"asset {self.asset_id}: a capture asset needs capture_id"
            raise ValueError(msg)
        if self.kind != "dataset" and self.dataset_id is not None:
            msg = f"asset {self.asset_id}: only a dataset asset carries dataset_id"
            raise ValueError(msg)
        if self.kind != "capture" and self.capture_id is not None:
            msg = f"asset {self.asset_id}: only a capture asset carries capture_id"
            raise ValueError(msg)
        return self


class FieldEncoding(SchemaModel):
    field: str = Field(min_length=1, max_length=60)
    kind: Literal["quantitative", "temporal", "ordinal", "nominal"]
    unit: str = Field(default="", max_length=40)
    title: str = Field(default="", max_length=40)


class SeriesBinding(SchemaModel):
    """A series entity; value is the series_field value it draws, empty for a single series."""

    value: str = Field(default="", max_length=80)
    entity_id: OpaqueId


class ChartTemplate(SchemaModel):
    template: Literal["chart"]
    chart_kind: Literal["bar", "line", "area", "stacked_bar", "scatter", "slope"]
    title: str = Field(default="", max_length=60)
    dataset_asset_id: OpaqueId
    x: FieldEncoding
    y: FieldEncoding
    series_field: str | None = Field(default=None, max_length=60)
    series: tuple[SeriesBinding, ...] = Field(min_length=1)
    baseline_zero: bool = True

    @model_validator(mode="after")
    def _bars_start_at_zero(self) -> ChartTemplate:
        if self.chart_kind in {"bar", "stacked_bar"} and not self.baseline_zero:
            msg = "bar charts keep a zero baseline; use a line or slope chart for a truncated axis"
            raise ValueError(msg)
        # Long format: value is a series_field value. Wide format: value names the column; a
        # single wide series may leave it empty and draw y.field.
        unnamed = [s.entity_id for s in self.series if not s.value]
        if unnamed and (self.series_field is not None or len(self.series) > 1):
            msg = f"series {', '.join(unnamed)} need the value or column they draw"
            raise ValueError(msg)
        return self


class DiagramNodeSpec(SchemaModel):
    entity_id: OpaqueId
    label: str = Field(min_length=1, max_length=40)


class DiagramEdgeSpec(SchemaModel):
    entity_id: OpaqueId
    source_entity_id: OpaqueId
    target_entity_id: OpaqueId
    label: str = Field(default="", max_length=40)


class DiagramTemplate(SchemaModel):
    template: Literal["diagram"]
    direction: Literal["LR", "TB"] = "LR"
    nodes: tuple[DiagramNodeSpec, ...] = Field(min_length=1)
    edges: tuple[DiagramEdgeSpec, ...] = ()

    @model_validator(mode="after")
    def _edges_join_nodes(self) -> DiagramTemplate:
        node_ids = _unique_ids("diagram node", [n.entity_id for n in self.nodes])
        _unique_ids("diagram edge", [e.entity_id for e in self.edges])
        for edge in self.edges:
            _must_exist(edge.source_entity_id, node_ids, f"edge {edge.entity_id}", "node")
            _must_exist(edge.target_entity_id, node_ids, f"edge {edge.entity_id}", "node")
        return self


class TextItem(SchemaModel):
    entity_id: OpaqueId
    text: str = Field(min_length=1, max_length=160)
    claim_id: OpaqueId | None = None


class TextTemplate(SchemaModel):
    template: Literal["text"]
    variant: Literal["statement", "big_number", "list", "quotation_card", "formula"]
    items: tuple[TextItem, ...] = Field(min_length=1, max_length=6)


class SourceDocumentTemplate(SchemaModel):
    template: Literal["source_document"]
    capture_asset_id: OpaqueId
    initial_section_id: OpaqueId


Template = Annotated[
    ChartTemplate | DiagramTemplate | TextTemplate | SourceDocumentTemplate,
    Field(discriminator="template"),
]


class TargetAction(SchemaModel):
    action: Literal[
        "reveal",
        "hide",
        "highlight",
        "clear_highlight",
        "focus",
        "trace",
        "draw",
        "flow",
        "isolate",
        "zoom_to",
        "pan_to",
    ]
    targets: tuple[OpaqueId, ...] = ()

    @model_validator(mode="after")
    def _arity(self) -> TargetAction:
        single = {"focus", "zoom_to", "pan_to"}
        if self.action in single and len(self.targets) != 1:
            msg = f"{self.action} takes exactly one target, got {len(self.targets)}"
            raise ValueError(msg)
        if self.action != "clear_highlight" and not self.targets:
            msg = f"{self.action} needs at least one target"
            raise ValueError(msg)
        return self


class CompareAction(SchemaModel):
    action: Literal["compare"]
    targets: tuple[OpaqueId, OpaqueId]


class AnnotateAction(SchemaModel):
    action: Literal["annotate"]
    targets: tuple[OpaqueId]
    text: str = Field(min_length=1, max_length=60)
    claim_id: OpaqueId | None = None


class SortAction(SchemaModel):
    action: Literal["sort"]
    by: Literal["value_desc", "value_asc", "label"]


class FilterAction(SchemaModel):
    action: Literal["filter"]
    field: str = Field(min_length=1, max_length=60)
    op: Literal["eq", "neq", "gt", "lt", "gte", "lte"]
    value: float | str


class HoldAction(SchemaModel):
    action: Literal["hold"]


class ShowSourceAction(SchemaModel):
    action: Literal["show_source"]


class ScrollToAction(SchemaModel):
    action: Literal["scroll_to"]
    section_id: OpaqueId


class QuoteAction(SchemaModel):
    action: Literal["focus_passage", "highlight_quote"]
    quote_id: OpaqueId


Action = Annotated[
    TargetAction
    | CompareAction
    | AnnotateAction
    | SortAction
    | FilterAction
    | HoldAction
    | ShowSourceAction
    | ScrollToAction
    | QuoteAction,
    Field(discriminator="action"),
]

# The published action x template compatibility table; the validator and the docs both read it.
ACTION_TEMPLATES: dict[str, frozenset[str]] = {
    "reveal": frozenset({"chart", "diagram", "text"}),
    "hide": frozenset({"chart", "diagram", "text"}),
    "highlight": frozenset({"chart", "diagram", "text"}),
    "clear_highlight": frozenset({"chart", "diagram", "text", "source_document"}),
    "compare": frozenset({"chart", "text"}),
    "focus": frozenset({"chart", "diagram"}),
    "trace": frozenset({"diagram", "chart"}),
    "draw": frozenset({"chart", "diagram"}),
    "annotate": frozenset({"chart", "diagram"}),
    "flow": frozenset({"diagram"}),
    "isolate": frozenset({"chart", "diagram"}),
    "sort": frozenset({"chart"}),
    "filter": frozenset({"chart"}),
    "hold": frozenset({"chart", "diagram", "text", "source_document"}),
    "zoom_to": frozenset({"chart", "diagram", "source_document"}),
    "pan_to": frozenset({"chart", "diagram", "source_document"}),
    "show_source": frozenset({"source_document"}),
    "scroll_to": frozenset({"source_document"}),
    "focus_passage": frozenset({"source_document"}),
    "highlight_quote": frozenset({"source_document"}),
}


class Beat(SchemaModel):
    beat_id: OpaqueId
    cue: Cue
    actions: tuple[Action, ...] = Field(min_length=1)


class Scene(SchemaModel):
    """One composition with one template, mutated by beats; a new question gets a new scene."""

    scene_id: OpaqueId
    section: Section
    purpose: str = Field(min_length=1, max_length=200)
    template: Template
    layout: Literal["primary", "split", "overlay"] = "primary"
    initial_visible: tuple[OpaqueId, ...] = ()
    beats: tuple[Beat, ...] = Field(min_length=1)
    claim_ids: tuple[OpaqueId, ...] = ()
    source_ids: tuple[OpaqueId, ...] = ()
    sponsored: bool = False

    @model_validator(mode="after")
    def _actions_fit_template(self) -> Scene:
        _unique_ids("beat", [b.beat_id for b in self.beats])
        name = self.template.template
        for beat in self.beats:
            for action in beat.actions:
                if name not in ACTION_TEMPLATES[action.action]:
                    allowed = ", ".join(sorted(ACTION_TEMPLATES[action.action]))
                    msg = (
                        f"scene {self.scene_id} beat {beat.beat_id}: action {action.action!r} "
                        f"is not supported by the {name} template (supported: {allowed})"
                    )
                    raise ValueError(msg)
        if self.sponsored and name != "text":
            msg = f"scene {self.scene_id}: a sponsored scene uses the text template only"
            raise ValueError(msg)
        return self


class CapabilityRequest(SchemaModel):
    scene_id: OpaqueId | None = None
    intent: str = Field(min_length=1, max_length=300)
    fallback: str = Field(min_length=1, max_length=300)


class VisualSpec(VersionedModel):
    """Typed semantic intent for every scene of one episode; nothing in it is renderer code."""

    spec_id: OpaqueId
    script_id: OpaqueId
    script_hash: Sha256Hex
    pack_hash: Sha256Hex
    design_system_version: int = Field(ge=1)
    entities: tuple[Entity, ...] = Field(min_length=1)
    assets: tuple[AssetRef, ...] = ()
    scenes: tuple[Scene, ...] = Field(min_length=1)
    capability_requests: tuple[CapabilityRequest, ...] = ()

    @model_validator(mode="after")
    def _references_resolve(self) -> VisualSpec:
        entity_ids = _unique_ids("entity", [e.entity_id for e in self.entities])
        asset_ids = _unique_ids("asset", [a.asset_id for a in self.assets])
        scene_ids = _unique_ids("scene", [s.scene_id for s in self.scenes])
        for scene in self.scenes:
            where = f"scene {scene.scene_id}"
            for eid in scene.initial_visible:
                _must_exist(eid, entity_ids, where, "entity")
            for eid in _template_entities(scene.template):
                _must_exist(eid, entity_ids, where, "entity")
            for aid in _template_assets(scene.template):
                _must_exist(aid, asset_ids, where, "asset")
            for beat in scene.beats:
                for action in beat.actions:
                    for eid in getattr(action, "targets", ()):
                        _must_exist(eid, entity_ids, f"{where} beat {beat.beat_id}", "entity")
        for request in self.capability_requests:
            if request.scene_id is not None:
                _must_exist(request.scene_id, scene_ids, "capability request", "scene")
        return self

    def spec_hash(self) -> str:
        return hash_without_provenance(self.model_dump(mode="json"))


def _template_entities(template: Any) -> list[str]:
    if isinstance(template, ChartTemplate):
        return [s.entity_id for s in template.series]
    if isinstance(template, DiagramTemplate):
        return [n.entity_id for n in template.nodes] + [e.entity_id for e in template.edges]
    if isinstance(template, TextTemplate):
        return [i.entity_id for i in template.items]
    return []


def _template_assets(template: Any) -> list[str]:
    if isinstance(template, ChartTemplate):
        return [template.dataset_asset_id]
    if isinstance(template, SourceDocumentTemplate):
        return [template.capture_asset_id]
    return []


# --- compiled timeline ---


class PixelBox(SchemaModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class EntityColor(SchemaModel):
    entity_id: OpaqueId
    token_id: str = Field(min_length=1, max_length=60)
    srgb_hex: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class EntityBox(SchemaModel):
    """Where a text sits; text is what the compiler verified fits (a short_label when needed)."""

    entity_id: OpaqueId
    box: PixelBox
    font_px: int | None = Field(default=None, ge=1)
    lines: int = Field(default=1, ge=1)
    text: str | None = Field(default=None, max_length=400)


class NamedRegion(SchemaModel):
    name: Literal["content", "plot", "title", "legend", "axis_x", "axis_y", "caption", "page"]
    box: PixelBox


class ResolvedAction(SchemaModel):
    beat_id: OpaqueId
    index: int = Field(ge=0)
    action: str = Field(min_length=1, max_length=40)
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    targets: tuple[OpaqueId, ...] = ()

    @model_validator(mode="after")
    def _ordered(self) -> ResolvedAction:
        if self.end_frame < self.start_frame:
            msg = "action ends before it starts"
            raise ValueError(msg)
        return self


CameraEasing = Literal["move", "standard", "hold"]
MAX_ZOOM = 2.5


class CameraKey(SchemaModel):
    """Page viewport at a timeline frame: scroll in page px, zoom 1 = page width fits the region."""

    frame: int = Field(ge=0)
    scroll_x: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    scroll_y: float = Field(ge=0, allow_inf_nan=False)
    zoom: float = Field(ge=1, le=MAX_ZOOM, allow_inf_nan=False)
    easing: CameraEasing = "hold"


class HighlightKey(SchemaModel):
    """A quote's overlay: lines sweep in over start..end, then stay until the clear fade, if any."""

    quote_id: OpaqueId
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    rects: tuple[PageRect, ...] = Field(min_length=1)
    rgba: str = Field(pattern=r"^rgba\(\d{1,3}, \d{1,3}, \d{1,3}, (0|0?\.\d{1,3}|1)\)$")
    clear_start_frame: int | None = Field(default=None, ge=0)
    clear_end_frame: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> HighlightKey:
        if self.end_frame < self.start_frame:
            msg = f"highlight {self.quote_id} ends before it starts"
            raise ValueError(msg)
        if (self.clear_start_frame is None) != (self.clear_end_frame is None):
            msg = f"highlight {self.quote_id} needs both clear frames or neither"
            raise ValueError(msg)
        if self.clear_start_frame is not None and self.clear_end_frame is not None:
            if (
                self.clear_start_frame < self.end_frame
                or self.clear_end_frame < self.clear_start_frame
            ):
                msg = f"highlight {self.quote_id} clears before its sweep ends"
                raise ValueError(msg)
        return self


class CompiledExplainerScene(SchemaModel):
    scene_id: OpaqueId
    start_frame: int = Field(ge=0)
    duration_frames: int = Field(ge=1)
    regions: tuple[NamedRegion, ...] = ()
    colors: tuple[EntityColor, ...] = ()
    boxes: tuple[EntityBox, ...] = ()
    actions: tuple[ResolvedAction, ...] = ()
    camera: tuple[CameraKey, ...] = ()
    highlights: tuple[HighlightKey, ...] = ()

    @model_validator(mode="after")
    def _camera_monotonic(self) -> CompiledExplainerScene:
        frames = [k.frame for k in self.camera]
        if any(b <= a for a, b in pairwise(frames)):
            msg = f"scene {self.scene_id}: camera keys must be strictly increasing in frame"
            raise ValueError(msg)
        return self


class InputHash(SchemaModel):
    name: str = Field(min_length=1, max_length=60)
    sha256: Sha256Hex


class ExplainerTimeline(VersionedModel):
    """The explainer's compiled timeline: frames, geometry and colours resolved from the spec."""

    timeline_id: OpaqueId
    spec_id: OpaqueId
    spec_hash: Sha256Hex
    script_hash: Sha256Hex
    pack_hash: Sha256Hex
    narration_manifest_id: OpaqueId | None = None
    fps: int = Field(ge=24, le=60)
    width: int = Field(ge=320)
    height: int = Field(ge=180)
    total_frames: int = Field(ge=1)
    scenes: tuple[CompiledExplainerScene, ...] = Field(min_length=1)
    inputs: tuple[InputHash, ...] = ()
    compiler_version: str = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _contiguous(self) -> ExplainerTimeline:
        expected = 0
        for scene in self.scenes:
            if scene.start_frame != expected:
                msg = f"scene {scene.scene_id} starts at {scene.start_frame}, expected {expected}"
                raise ValueError(msg)
            expected += scene.duration_frames
        if expected != self.total_frames:
            msg = f"scenes cover {expected} frames, total_frames is {self.total_frames}"
            raise ValueError(msg)
        return self


# --- render bundle ---


class LayoutPoint(SchemaModel):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)


class LayoutNode(SchemaModel):
    entity_id: OpaqueId
    box: PixelBox


class LayoutEdge(SchemaModel):
    entity_id: OpaqueId
    points: tuple[LayoutPoint, ...] = Field(min_length=2)
    label_anchor: LayoutPoint | None = None


class DiagramLayout(SchemaModel):
    """ELK output for one diagram scene, computed before rendering so every frame is pure."""

    scene_id: OpaqueId
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    nodes: tuple[LayoutNode, ...] = Field(min_length=1)
    edges: tuple[LayoutEdge, ...] = ()


class CaptureTile(SchemaModel):
    """One viewport-high PNG of the page at y_px; path is local until the renderer stages it."""

    path: str = Field(min_length=1, max_length=2000)
    y_px: float = Field(ge=0, allow_inf_nan=False)
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    sha256: Sha256Hex


class CaptureAsset(SchemaModel):
    """A capture the bundle carries: its manifest plus the page tiles the renderer draws."""

    capture_id: OpaqueId
    manifest: SourceCaptureManifest
    tiles: tuple[CaptureTile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _manifest_matches(self) -> CaptureAsset:
        if self.manifest.capture_id != self.capture_id:
            msg = f"capture {self.capture_id} carries manifest {self.manifest.capture_id}"
            raise ValueError(msg)
        return self


class ExplainerRenderBundle(VersionedModel):
    """Everything the renderer reads: spec, compiled timeline, data, layouts and captures."""

    bundle_id: OpaqueId
    spec: VisualSpec
    timeline: ExplainerTimeline
    datasets: tuple[EvidenceDataset, ...] = ()
    layouts: tuple[DiagramLayout, ...] = ()
    captures: tuple[CaptureAsset, ...] = ()
    design_system_version: int = Field(ge=1)
    fonts_version: str = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def _consistent(self) -> ExplainerRenderBundle:
        if self.timeline.spec_id != self.spec.spec_id:
            msg = (
                f"timeline is for spec {self.timeline.spec_id}, bundle carries {self.spec.spec_id}"
            )
            raise ValueError(msg)
        if self.timeline.spec_hash != self.spec.spec_hash():
            msg = "timeline.spec_hash does not match the spec in the bundle; recompile"
            raise ValueError(msg)
        compiled = {s.scene_id for s in self.timeline.scenes}
        for scene in self.spec.scenes:
            _must_exist(scene.scene_id, compiled, "bundle", "compiled scene")
        datasets = {d.dataset_id for d in self.datasets}
        layouts = {layout.scene_id for layout in self.layouts}
        captures = _unique_ids("capture", [c.capture_id for c in self.captures])
        assets = {a.asset_id: a for a in self.spec.assets}
        for scene in self.spec.scenes:
            template = scene.template
            if isinstance(template, ChartTemplate):
                asset = assets[template.dataset_asset_id]
                if asset.dataset_id not in datasets:
                    msg = f"scene {scene.scene_id} charts dataset {asset.dataset_id}: not in bundle"
                    raise ValueError(msg)
            if isinstance(template, DiagramTemplate) and scene.scene_id not in layouts:
                msg = f"diagram scene {scene.scene_id} has no precomputed layout"
                raise ValueError(msg)
            if isinstance(template, SourceDocumentTemplate):
                capture_id = assets[template.capture_asset_id].capture_id
                if capture_id not in captures:
                    msg = f"scene {scene.scene_id} shows capture {capture_id}: not in bundle"
                    raise ValueError(msg)
        return self


# --- review ---

ReviewCategory = Literal[
    "readability",
    "composition",
    "continuity",
    "animation_completion",
    "narration_alignment",
    "misleading_comparison",
    "source_correctness",
    "highlight_correctness",
    "communication",
    "technical",
    "audio",
]
ReviewSeverity = Literal["blocker", "major", "minor", "note"]
Disposition = Literal["pass", "fail", "uncertain"]


class TimeInterval(SchemaModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> TimeInterval:
        if self.end_ms < self.start_ms:
            msg = "interval ends before it starts"
            raise ValueError(msg)
        return self


class ReviewedArtifact(SchemaModel):
    kind: Literal["mp4", "frame_set", "animatic", "audio"]
    sha256: Sha256Hex


class ReviewerIdentity(SchemaModel):
    model_id: str = Field(min_length=1, max_length=160)
    model_revision: str = Field(default="", max_length=80)
    quantization: str = Field(default="", max_length=40)
    prompt_sha256: Sha256Hex
    rubric_version: str = Field(min_length=1, max_length=40)
    modalities: tuple[Literal["image", "video", "audio", "text"], ...] = Field(min_length=1)


class CoverageSpan(SchemaModel):
    scene_id: OpaqueId
    beat_ids: tuple[OpaqueId, ...] = ()
    interval: TimeInterval
    sampled_ms: tuple[int, ...] = Field(min_length=1)
    frame_sha256s: tuple[Sha256Hex, ...] = ()


class CueOffsetRepair(SchemaModel):
    repair: Literal["cue_offset"]
    beat_id: OpaqueId
    offset_ms: int


class LabelWordingRepair(SchemaModel):
    repair: Literal["label_wording"]
    entity_id: OpaqueId
    short_label: str = Field(min_length=1, max_length=40)


class LayoutChoiceRepair(SchemaModel):
    repair: Literal["layout_choice"]
    scene_id: OpaqueId
    layout: Literal["primary", "split", "overlay"]


class HoldRepair(SchemaModel):
    repair: Literal["hold"]
    beat_id: OpaqueId
    duration_class: DurationClass


class TakeSelectionRepair(SchemaModel):
    repair: Literal["take_selection"]
    segment_id: OpaqueId
    take_id: OpaqueId


class SourcePassageRepair(SchemaModel):
    repair: Literal["source_passage"]
    scene_id: OpaqueId
    quote_id: OpaqueId


class SplitSceneRepair(SchemaModel):
    repair: Literal["split_scene"]
    scene_id: OpaqueId
    after_beat_id: OpaqueId


TypedRepair = Annotated[
    CueOffsetRepair
    | LabelWordingRepair
    | LayoutChoiceRepair
    | HoldRepair
    | TakeSelectionRepair
    | SourcePassageRepair
    | SplitSceneRepair,
    Field(discriminator="repair"),
]


class ReviewFinding(SchemaModel):
    """One observed problem, where and when, with evidence and a typed repair if one is known."""

    finding_id: OpaqueId
    scene_id: OpaqueId
    beat_id: OpaqueId | None = None
    interval: TimeInterval
    frame_sha256s: tuple[Sha256Hex, ...] = ()
    category: ReviewCategory
    severity: ReviewSeverity
    observed: str = Field(min_length=1, max_length=600)
    evidence: str = Field(min_length=1, max_length=600)
    proposed_repair: TypedRepair | None = None
    disposition: Disposition
    confidence: float | None = Field(default=None, ge=0, le=1)


class CategoryCoverage(SchemaModel):
    category: ReviewCategory
    covered_by: str = Field(min_length=1, max_length=160)


class ReviewReport(VersionedModel):
    """A reviewer's verdict on one artifact: who looked, at what, where, and what they found."""

    report_id: OpaqueId
    artifact: ReviewedArtifact
    timeline_id: OpaqueId
    reviewer: ReviewerIdentity
    coverage: tuple[CoverageSpan, ...] = Field(min_length=1)
    findings: tuple[ReviewFinding, ...] = ()
    categories: tuple[CategoryCoverage, ...] = Field(min_length=1)
    disposition: Disposition
    authority: Literal["advisory", "blocking"] = "advisory"
    created_at: str = Field(min_length=4)

    @model_validator(mode="after")
    def _consistent(self) -> ReviewReport:
        _unique_ids("finding", [f.finding_id for f in self.findings])
        covered = {c.scene_id for c in self.coverage}
        for finding in self.findings:
            _must_exist(finding.scene_id, covered, f"finding {finding.finding_id}", "covered scene")
        if self.disposition == "pass" and any(
            f.severity == "blocker" and f.disposition == "fail" for f in self.findings
        ):
            msg = "report disposition is pass but a blocker finding failed"
            raise ValueError(msg)
        if (
            "audio" in {c.category for c in self.categories}
            and "audio" not in self.reviewer.modalities
        ):
            msg = "audio category claimed by a reviewer without the audio modality"
            raise ValueError(msg)
        return self


def _unique_ids(kind: str, ids: list[str]) -> set[str]:
    seen: set[str] = set()
    for value in ids:
        if value in seen:
            msg = f"duplicate {kind} id {value}"
            raise ValueError(msg)
        seen.add(value)
    return seen


def _must_exist(value: str, known: set[str], where: str, kind: str) -> None:
    if value not in known:
        sample = ", ".join(sorted(known)[:6]) or "none"
        msg = f"{where} references unknown {kind} {value}; known {kind} ids: {sample}"
        raise ValueError(msg)
