"""Research, evidence, and claims contracts (2.4, 2.14, section 10).

Four linked structures: sources and captured evidence; claims and verification status; narrative
statements (TextRef.claim_ids); scenes/datasets (DataRef.claim_id). A model never decides whether
a deterministic check passed and never silently repairs a number.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import (
    OpaqueId,
    SchemaModel,
    Sha256Hex,
    VersionedModel,
    WorkspaceId,
)


class SourceClass(StrEnum):
    official = "official"  # statistics agencies, regulators, primary bodies
    primary = "primary"  # the entity the claim is about
    reference = "reference"  # encyclopedic/reference works
    news = "news"
    analysis = "analysis"  # think tanks, research notes
    blog = "blog"
    social = "social"
    operator_upload = "operator_upload"
    unknown = "unknown"


class RightsStatus(StrEnum):
    quotable_excerpt = "quotable_excerpt"  # bounded quotation with attribution
    licensed = "licensed"
    operator_owned = "operator_owned"
    public_domain = "public_domain"
    restricted = "restricted"


class FetchStatus(StrEnum):
    captured = "captured"
    blocked_robots = "blocked_robots"
    failed = "failed"
    skipped_policy = "skipped_policy"


class SourceRecord(VersionedModel):
    source_id: OpaqueId
    workspace_id: WorkspaceId
    canonical_url: str = Field(min_length=1, max_length=2000)
    requested_url: str = Field(min_length=1, max_length=2000)
    final_url: str = Field(min_length=1, max_length=2000)
    title: str = Field(default="", max_length=500)
    publisher: str = Field(default="", max_length=200)
    author: str = Field(default="", max_length=200)
    published_at: str | None = None  # ISO date if known; never invented
    accessed_at: str = Field(min_length=4)
    capture_sha256: Sha256Hex
    content_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(ge=0)
    classification: SourceClass = SourceClass.unknown
    rights_status: RightsStatus = RightsStatus.quotable_excerpt
    fetch_status: FetchStatus = FetchStatus.captured
    injection_flags: tuple[str, ...] = ()  # suspicious instruction-like content (data, not code)


class EvidenceLocator(SchemaModel):
    kind: Literal["char_range", "pdf_page", "table_cell", "timestamp_ms"]
    start: int = Field(ge=0)
    end: int = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> EvidenceLocator:
        if self.kind in {"char_range", "timestamp_ms"} and self.end < self.start:
            msg = "locator end before start"
            raise ValueError(msg)
        return self


class EvidenceRecord(VersionedModel):
    evidence_id: OpaqueId
    source_id: OpaqueId
    excerpt: str = Field(min_length=1, max_length=800)  # bounded quotation
    locator: EvidenceLocator
    captured_at: str = Field(min_length=4)


class ClaimKind(StrEnum):
    externally_verifiable_fact = "externally_verifiable_fact"
    operator_assertion = "operator_assertion"
    attributed_quotation = "attributed_quotation"
    opinion = "opinion"
    promotional = "promotional"
    estimate = "estimate"
    illustration = "illustration"


class Criticality(StrEnum):
    high = "high"  # consequential; wrong number misleads
    medium = "medium"
    low = "low"


class VerificationStatus(StrEnum):
    supported = "supported"
    supported_with_caveat = "supported_with_caveat"
    disputed = "disputed"
    unsupported = "unsupported"
    stale = "stale"
    needs_review = "needs_review"
    not_applicable = "not_applicable"  # opinions/illustrations need no citation


class NumericValue(SchemaModel):
    value: float
    unit: str = Field(default="", max_length=40)  # "%", "TWh", "SEK", ...
    period: str = Field(default="", max_length=40)  # "2025", "2025-Q1", "2018-2025"
    basis: Literal["nominal", "real", "unspecified"] = "unspecified"


class ClaimRecord(VersionedModel):
    claim_id: OpaqueId
    workspace_id: WorkspaceId
    statement: str = Field(min_length=1, max_length=1000)
    kind: ClaimKind
    criticality: Criticality
    status: VerificationStatus
    evidence_ids: tuple[OpaqueId, ...] = ()
    dataset_id: OpaqueId | None = None
    numeric: NumericValue | None = None
    caveats: tuple[str, ...] = ()
    conflicts: tuple[OpaqueId, ...] = ()  # evidence that contradicts
    checked_at: str = Field(min_length=4)

    @model_validator(mode="after")
    def _no_fake_support(self) -> ClaimRecord:
        if (
            self.status in {VerificationStatus.supported, VerificationStatus.supported_with_caveat}
            and self.kind == ClaimKind.externally_verifiable_fact
            and not self.evidence_ids
        ):
            msg = "a supported verifiable fact must cite evidence"
            raise ValueError(msg)
        if (
            self.kind == ClaimKind.operator_assertion
            and self.status == VerificationStatus.supported
            and not self.evidence_ids
        ):
            msg = "an operator assertion is never independently 'supported' without evidence"
            raise ValueError(msg)
        return self


class EvidenceRequirement(SchemaModel):
    """Per planned statement: how much evidence its kind and criticality demand (2.14)."""

    statement: str = Field(min_length=1, max_length=1000)
    kind: ClaimKind
    criticality: Criticality
    requires_evidence: bool
    requires_independent_sources: int = Field(default=1, ge=0, le=3)
    blocks_full_auto: bool = False
    reason: str = Field(min_length=1, max_length=300)


class EvidenceRequirementPlan(VersionedModel):
    brief_id: OpaqueId
    requirements: tuple[EvidenceRequirement, ...]
    freshness_days: int = Field(ge=1)


class ResearchPack(VersionedModel):
    pack_id: OpaqueId
    workspace_id: WorkspaceId
    brief_id: OpaqueId
    queries: tuple[str, ...] = ()
    source_ids: tuple[OpaqueId, ...] = ()
    claim_ids: tuple[OpaqueId, ...] = ()
    created_at: str = Field(min_length=4)
