"""/v1/uploads: a file dropped on the canvas becomes a typed, stored, described asset.

One POST does the whole thing an operator expects from a drop: the bytes are sniffed (never the
extension), **converted if the pipeline cannot read that container** — a screen recording lands as
Matroska and leaves as MP4, usually by copying the streams rather than re-encoding them — refused
if even after that it is not on the allowlist, stored in the artifact store under a
content-addressed key, measured with ffprobe, and answered with everything the canvas needs to
put a node on the graph: which node type holds this kind of file, one line describing what
arrived, what was done to it, and the type-correct next steps worth offering.

One of those answers takes more than a sniff. A recording off a phone or a meeting tool is an
MP4 with an hour of black in it, and its bytes are indistinguishable from a film's, so the
picture is measured: a video container with sound and nothing to look at comes back as ``audio``,
lands on the Audio File node, and carries the reason it was called one.

Nothing here trusts the request beyond its bytes: the filename is sanitised and used only for
display and for the name the file gets in the run's uploads folder, the kind comes from the sniff,
and the stored key is derived from the content. Uploading is editor-level, like starting a run.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from starlette.concurrency import run_in_threadpool

from content_factory.api.deps import Principal, require_role
from content_factory.artifacts import open_store
from content_factory.db.models import Role
from content_factory.ingest.convert import (
    BlankPicture,
    ConversionError,
    Converted,
    blank_picture,
    convert_media,
    needs_conversion,
)
from content_factory.ingest.dropped import (
    SOURCE_NODE,
    SOURCE_SLOT,
    describe,
    probe_media,
    safe_name,
    suggestions_for,
)
from content_factory.ingest.uploads import (
    MAX_UPLOAD_BYTES,
    UploadRejectedError,
    ingest_upload,
    sniff_mime,
)

router = APIRouter(prefix="/v1/uploads", tags=["uploads"])
EDITOR = require_role(Role.editor)

# Read in chunks so a 2 GB drop never becomes a 2 GB string in memory.
CHUNK_BYTES = 1024 * 1024


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    p: Principal = Depends(EDITOR),
) -> dict[str, Any]:
    filename = safe_name(file.filename)
    with tempfile.TemporaryDirectory(prefix="cf-drop-") as tmp:
        staged = Path(tmp) / filename
        written = 0
        with staged.open("wb") as handle:
            while chunk := await file.read(CHUNK_BYTES):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        f"file exceeds {MAX_UPLOAD_BYTES} bytes",
                    )
                handle.write(chunk)
        if written == 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "empty file")

        sniffed = sniff_mime(staged)
        conversion: Converted | None = None
        source = staged
        if needs_conversion(sniffed):
            # A container the pipeline cannot read becomes one it can, before anything is stored:
            # what lands in the artifact store is what the stages will open.
            try:
                conversion = await run_in_threadpool(convert_media, staged, sniffed, Path(tmp))
            except ConversionError as err:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"{filename} could not be converted: {err}",
                ) from err
            source = conversion.path
            filename = source.name

        store = open_store()
        try:
            # Sniffs again (on the converted bytes), refuses anything off the allowlist, and
            # stores the immutable original of what will actually be used.
            ingested = await run_in_threadpool_ingest(store, p.workspace_id, source, filename)
        except UploadRejectedError as err:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(err)) from err
        facts = probe_media(source) if ingested.kind in ("audio", "video", "image") else {}
        # A recording wrapped in MP4 sniffs as `video/mp4` like any film, and putting it on a
        # Video node is how an operator ends up unable to wire their own interview into an audio
        # lane. The picture is measured instead: nothing to look at means this is a recording,
        # and the whole answer below — node, description, next steps — follows from that.
        picture: BlankPicture | None = (
            await run_in_threadpool(blank_picture, source) if ingested.kind == "video" else None
        )
        kind = "audio" if picture else ingested.kind
        stored_bytes = source.stat().st_size

    node_type = SOURCE_NODE.get(kind, "ingest")
    described = describe(kind, facts)
    return {
        "asset_id": ingested.artifact.key,
        "filename": filename,
        # What the file *is*, which for a video container is not always what its bytes say.
        "kind": kind,
        "mime": ingested.sniffed_mime,
        "size_bytes": stored_bytes,
        "uploaded_bytes": written,
        "sha256": ingested.artifact.sha256,
        "facts": facts,
        "description": described,
        # What was done to the file, or null when the pipeline could read it as it arrived. The
        # canvas shows this: an operator who dropped a .mkv should be told it is an .mp4 now.
        "conversion": (
            {
                "action": conversion.action,
                "from_mime": conversion.from_mime,
                "to_mime": conversion.to_mime,
                "detail": conversion.detail,
            }
            if conversion is not None
            else None
        ),
        # Why a file whose MIME says video is being offered as a recording, or null when the
        # kind is simply what the bytes said. The canvas shows it: an operator handed an audio
        # node for their MP4 is owed the measurement that decided it.
        "blank_picture": picture.reason if picture else None,
        "node_type": node_type,
        "node_slot": SOURCE_SLOT.get(node_type, ""),
        "suggestions": [s.as_dict() for s in suggestions_for(kind, facts)],
    }


async def run_in_threadpool_ingest(store: Any, workspace_id: str, path: Path, name: str) -> Any:
    """``ingest_upload`` hashes and copies the file, so it must not run on the event loop."""
    return await run_in_threadpool(ingest_upload, store, workspace_id, path, declared_name=name)
