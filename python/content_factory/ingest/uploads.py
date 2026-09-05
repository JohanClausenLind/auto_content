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


def sniff_mime(path: Path) -> str:
    """What the bytes say this file is. The only answer anything here trusts.

    Defined once because two callers need it *before* ingesting: the upload endpoint and the
    ingest stage both have to know whether a file needs converting first, and a second opinion
    from a filename would be exactly the mistake this repo refuses to make.
    """
    return magic.from_file(str(path), mime=True)


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
    sniffed = sniff_mime(path)
    if sniffed not in ALLOWED:
        from content_factory.ingest.convert import CONVERTIBLE

        if sniffed in CONVERTIBLE:
            # Reached only by a caller that skipped the conversion step, so say which step.
            raise UploadRejectedError(
                f"file type {sniffed!r} has to be converted before it can be ingested"
                " (ingest.convert.convert_media)"
            )
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
