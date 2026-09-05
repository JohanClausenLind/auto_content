"""What actually ships to one destination, file by file, with a digest for each.

`compile_destination_packages` used to write this:

    {"destination": "youtube", "visibility": "private", "deliverable_id": "...",
     "status": "packaged"}

`"packaged"` as a *string*, and not one word about **what** was packaged. So the last stage before
a destination — the one whose whole job is to say "these are the bytes that go out" — recorded
neither the files nor their digests, and a package for a film that had failed to render looked
exactly like a package for one that had.

A digest per file is the point, not decoration. It is what lets a later publish confirm it is
sending the bytes that passed QC rather than whatever is at that path now, and it is what makes
two packages of the same deliverable comparable at all.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import OpaqueId, SchemaModel, Sha256Hex, VersionedModel

DeliveryRole = Literal[
    "video",
    "image",
    "audio",
    "caption",
    "thumbnail",
    "text",
    "metadata",
    "sequence",
]
"""What a file is *for* at the destination, which is not the same as its media type: a thumbnail
and a still deliverable are both PNGs and only one of them is the thing being published."""

DELIVERABLE_ROLES = frozenset({"video", "image", "audio", "text", "sequence"})
"""Roles that can be the thing published. A package of captions and metadata with nothing to
publish is the failure this distinction exists to catch."""


class DeliveryFile(SchemaModel):
    role: DeliveryRole
    path: str = Field(min_length=1, max_length=400)
    """Relative to the deliverable directory. Never absolute: a package is meant to survive the
    project being moved, and an absolute path in a manifest is a path on one machine."""
    sha256: Sha256Hex
    bytes: int = Field(ge=1)
    content_type: str = Field(min_length=3, max_length=100)

    @model_validator(mode="after")
    def _relative(self) -> DeliveryFile:
        if self.path.startswith("/") or ".." in self.path.split("/"):
            msg = f"delivery file path must be relative and inside the deliverable: {self.path}"
            raise ValueError(msg)
        return self


class DeliveryPackage(VersionedModel):
    """One destination's worth of files, with what QC said about them."""

    deliverable_id: OpaqueId
    destination: str = Field(min_length=1, max_length=64)
    visibility: str = Field(min_length=1, max_length=32)
    files: tuple[DeliveryFile, ...] = Field(min_length=1)
    qc_passed: bool | None = None
    """What the QC stage concluded, or None when it has not run. Recorded rather than enforced:
    a package is allowed to exist for a film that failed, and a publisher is allowed to refuse it.
    Refusing to *record* it would just move the ignorance one step later."""
    built_at: str = Field(min_length=4, max_length=40)

    @model_validator(mode="after")
    def _one_thing_to_publish(self) -> DeliveryPackage:
        paths = [f.path for f in self.files]
        if len(set(paths)) != len(paths):
            msg = f"duplicate file paths in package for {self.destination}"
            raise ValueError(msg)
        if not any(f.role in DELIVERABLE_ROLES for f in self.files):
            roles = sorted({f.role for f in self.files})
            msg = (
                f"package for {self.destination} has nothing to publish: it carries only {roles}."
                " A package of captions and metadata with no film in it is the case this refuses."
            )
            raise ValueError(msg)
        return self

    @property
    def total_bytes(self) -> int:
        return sum(f.bytes for f in self.files)
