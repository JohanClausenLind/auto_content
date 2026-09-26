"""From a URL and its quotes to a verified SourceCaptureManifest: capture, replay, resolve, OCR."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from content_factory.explainer.capture import DEFAULT_VIEWPORT, CaptureResult, capture_url
from content_factory.explainer.ocr import verify_manifest
from content_factory.explainer.passages import (
    EXTRACTOR,
    EXTRACTOR_VERSION,
    QuoteRequest,
    ResolvedPage,
    Tile,
    resolve_quotes,
)
from content_factory.explainer.replay import ReplayServer
from content_factory.schemas.base import file_sha256
from content_factory.schemas.explainer import (
    CaptureAsset,
    CaptureTile,
    SourceCaptureManifest,
    Viewport,
)


@dataclass(frozen=True)
class SourceInfo:
    """The EvidenceSource fields a manifest repeats; published_at stays None when unknown."""

    source_id: str
    publisher: str = ""
    title: str = ""
    author: str = ""
    published_at: str | None = None


def capture_id(artifact_sha256: str) -> str:
    return "cap_" + artifact_sha256[:12]


def build_manifest(
    source: SourceInfo, capture: CaptureResult, resolved: ResolvedPage
) -> SourceCaptureManifest:
    claim_ids: list[str] = []
    for quote in resolved.quotes:
        claim_ids.extend(c for c in quote.claim_ids if c not in claim_ids)
    return SourceCaptureManifest(
        capture_id=capture_id(capture.artifact_sha256),
        source_id=source.source_id,
        url=capture.url,
        publisher=source.publisher,
        title=(source.title or capture.title)[:500],
        author=source.author,
        published_at=source.published_at,
        captured_at=capture.captured_at,
        capture_kind=capture.capture_kind,
        artifact_sha256=capture.artifact_sha256,
        signed=capture.signed,
        signature_domain=capture.signature_domain,
        tls_certificate_sha256=capture.tls_certificate_sha256,
        viewport=capture.viewport,
        page_height_px=resolved.page_height_px,
        text_sha256=resolved.text_sha256,
        extractor=EXTRACTOR,
        extractor_version=EXTRACTOR_VERSION,
        sections=resolved.sections,
        quotes=resolved.quotes,
        claim_ids=tuple(claim_ids),
    )


def capture_asset(manifest: SourceCaptureManifest, tiles: Sequence[Tile]) -> CaptureAsset:
    """The bundle's view of a capture: the manifest plus each tile's size and content hash."""
    described: list[CaptureTile] = []
    for tile in tiles:
        with Image.open(tile.path) as image:
            width, height = image.size
        described.append(
            CaptureTile(
                path=str(tile.path.resolve()),
                y_px=float(tile.y_px),
                width=width,
                height=height,
                sha256=file_sha256(tile.path),
            )
        )
    return CaptureAsset(capture_id=manifest.capture_id, manifest=manifest, tiles=tuple(described))


def capture_source(
    url: str,
    quotes: Sequence[QuoteRequest],
    out_dir: Path,
    *,
    source: SourceInfo,
    viewport: Viewport = DEFAULT_VIEWPORT,
    signing_url: str | None = None,
    signing_token: str | None = None,
    allow_loopback: bool = False,
    provenance_summary: bool = True,
) -> tuple[SourceCaptureManifest, tuple[Tile, ...]]:
    """Capture, replay from the WACZ only, locate the quotes, refuse any whose pixels disagree."""
    capture = capture_url(
        url,
        out_dir,
        signing_url=signing_url,
        signing_token=signing_token,
        viewport=viewport,
        allow_loopback=allow_loopback,
        provenance_summary=provenance_summary,
    )
    return manifest_from_capture(capture, quotes, out_dir, source=source)


def manifest_from_capture(
    capture: CaptureResult, quotes: Sequence[QuoteRequest], out_dir: Path, *, source: SourceInfo
) -> tuple[SourceCaptureManifest, tuple[Tile, ...]]:
    """Replay a stored capture, locate the quotes, refuse any whose pixels disagree."""
    tiles_dir = out_dir / f"tiles-{capture.artifact_sha256[:12]}"
    with ReplayServer(capture.wacz_path) as replay:
        resolved = resolve_quotes(
            replay.replay_url(capture.url), quotes, viewport=capture.viewport, tiles_dir=tiles_dir
        )
    manifest = build_manifest(source, capture, resolved)
    return verify_manifest(manifest, resolved.tiles), resolved.tiles
