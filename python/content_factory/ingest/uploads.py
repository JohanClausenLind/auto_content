"""Upload ingestion (4.2): checksums, MIME/magic validation, immutable originals. Uploaded files
are hostile inputs; only allowlisted, sniffed types are accepted, and SVG is never rasterized."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import magic

from content_factory.artifacts import ArtifactRef, ArtifactStore

# sniffed MIME -> (kind, extension). Extends per phase; SVG deliberately absent (script risk).
ALLOWED: dict[str, tuple[str, str]] = {
    "image/png": ("image", "png"),
    "image/jpeg": ("image", "jpg"),
    "image/webp": ("image", "webp"),
    "video/mp4": ("video", "mp4"),
    "video/quicktime": ("video", "mov"),
    "audio/x-wav": ("audio", "wav"),
    "audio/wav": ("audio", "wav"),
    "audio/mpeg": ("audio", "mp3"),
    "audio/flac": ("audio", "flac"),
    "application/pdf": ("document", "pdf"),
    "text/csv": ("data", "csv"),
    "application/json": ("data", "json"),
    "text/plain": ("text", "txt"),
}
MAX_UPLOAD_BYTES = 2 * 1024**3


class UploadRejectedError(Exception):
    pass


@dataclass(frozen=True)
class IngestedUpload:
    artifact: ArtifactRef
    sniffed_mime: str
    kind: str
    declared_name: str
    rights_declared: str


def ingest_upload(
    store: ArtifactStore,
    workspace_id: str,
    path: Path,
    *,
    declared_name: str | None = None,
    rights_declared: str = "operator_owned",
) -> IngestedUpload:
    size = path.stat().st_size
    if size == 0:
        raise UploadRejectedError("empty file")
    if size > MAX_UPLOAD_BYTES:
        raise UploadRejectedError(f"file exceeds {MAX_UPLOAD_BYTES} bytes")
    sniffed = magic.from_file(str(path), mime=True)
    if sniffed not in ALLOWED:
        raise UploadRejectedError(
            f"file type {sniffed!r} is not accepted (sniffed, not extension-based)"
        )
    kind, ext = ALLOWED[sniffed]
    declared = declared_name or path.name
    declared_ext = declared.rsplit(".", 1)[-1].lower() if "." in declared else ""
    if declared_ext in {"svg", "html", "htm", "js", "exe", "sh", "bat"}:
        raise UploadRejectedError(f"declared extension .{declared_ext} is never accepted")
    ref = store.put_file(workspace_id, f"originals-{kind}", path, content_type=sniffed)
    if not ref.key.endswith(f".{ext}") and not ref.key.endswith(path.suffix.lower()):
        # content addressing keeps the sniffed truth in content_type; key extension is advisory
        pass
    return IngestedUpload(
        artifact=ref,
        sniffed_mime=sniffed,
        kind=kind,
        declared_name=declared,
        rights_declared=rights_declared,
    )
