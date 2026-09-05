from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Opaque, stable IDs: short type prefix + underscore + 8..32 URL-safe characters.
OpaqueId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,7}_[A-Za-z0-9_-]{8,32}$")]
WorkspaceId = Annotated[str, StringConstraints(pattern=r"^ws_[A-Za-z0-9_-]{8,32}$")]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
SemVer = Annotated[
    str,
    StringConstraints(
        pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
    ),
]


# Shared 5-level finding severity — one vocabulary for QC, critique and review.
# Kept docstring-free: a class docstring would surface as `description` in the generated schema.
class Severity(StrEnum):
    blocker = "blocker"
    critical = "critical"
    major = "major"
    minor = "minor"
    advisory = "advisory"


class SchemaModel(BaseModel):
    """Strict base for every contract: unknown fields are errors, instances are immutable."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=False,
        use_enum_values=False,
        validate_default=True,
    )

    def canonical_json(self) -> str:
        """Deterministic serialization used for hashing and cross-language comparison."""
        return canonical_dumps(self.model_dump(mode="json"))

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class VersionedModel(SchemaModel):
    """A persisted contract carries its schema version for migrations."""

    schema_version: int = Field(default=1, ge=1)


def canonical_dumps(value: Any) -> str:
    """RFC 8785-style canonical JSON (sorted keys, no whitespace, UTF-8, no NaN)."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    """Streaming file digest (1 MiB chunks) — the on-disk twin of sha256_hex, so large media
    never has to be read whole into memory just to be hashed."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
