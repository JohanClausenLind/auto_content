"""Character-asset review: a built sculpture is not usable until its renders have been checked."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from content_factory.schemas.base import Sha256Hex, VersionedModel
from content_factory.schemas.shots import AssetRef

CheckSeverity = Literal["blocker", "advisory"]


class AssetCheck(VersionedModel):
    """One deterministic finding about a built asset."""

    check: str = Field(min_length=1, max_length=60)
    passed: bool
    severity: CheckSeverity
    detail: str = Field(min_length=1, max_length=400)
    measured: float | None = None
    threshold: float | None = None


class IdentitySheet(VersionedModel):
    """A styled character sheet: the same person, drawn in the film's own style, clothed."""

    style: str = Field(min_length=1, max_length=400)
    """The style prompt the sheet was drawn in, written out. A film's anchors and its identity
    sheets have to be in ONE style or the reference fights the prompt."""
    style_slug: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    """The filename stem under ``characters/<asset>/sheets/``."""
    png_sha256: Sha256Hex
    blend_sha256: Sha256Hex
    seed: int = Field(ge=0)
    backend: str = Field(min_length=1, max_length=40)
    prompt_version: str = Field(min_length=1, max_length=16)
    views: tuple[str, ...] = Field(min_length=1, max_length=8)
    """Which turnaround views conditioned it, in order. Front plus three-quarter by default: one
    view gives the model no way to know the body's depth."""


class CharacterAssetReview(VersionedModel):
    """The verdict on one built character asset."""

    asset: AssetRef
    blend_sha256: Sha256Hex
    """The built mesh this verdict is about. A rebuild invalidates the review."""
    reviewed_at: datetime
    checks: tuple[AssetCheck, ...] = Field(min_length=1)
    contact_sheet_sha256: Sha256Hex | None = None
    sheets: tuple[IdentitySheet, ...] = ()
    """The styled identity sheets on disk for this asset when it was reviewed. An approval covers
    the mesh AND these images: a sheet regenerated in a new style is unapproved until someone has
    looked at it, the same discipline a regenerated frame gets."""
    approved_by: str | None = Field(default=None, max_length=120)
    approved_at: datetime | None = None
    notes: str = Field(default="", max_length=1000)

    @property
    def blockers(self) -> tuple[AssetCheck, ...]:
        return tuple(c for c in self.checks if c.severity == "blocker" and not c.passed)

    @property
    def checks_passed(self) -> bool:
        return not self.blockers

    @property
    def usable(self) -> bool:
        """Passing checks *and* a person's approval. Both, or the asset does not go in a film."""
        return self.checks_passed and self.approved_by is not None

    def sheet_for(self, style_slug: str) -> IdentitySheet | None:
        return next((s for s in self.sheets if s.style_slug == style_slug), None)
