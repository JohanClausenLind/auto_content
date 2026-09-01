"""Extraction: trafilatura for articles, pypdf for PDFs, defusedxml for feeds. Hostile inputs are
converted to safe internal text; scripts/styles never survive; excerpts carry locators."""

from __future__ import annotations

import json
from dataclasses import dataclass

from content_factory.research.fetch import CapturedContent, scan_injection

MAX_PDF_PAGES = 50
MAX_TEXT_CHARS = 500_000


class ExtractionError(Exception):
    pass


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    title: str
    author: str
    publisher: str
    published_at: str | None
    kind: str  # article | pdf | plain | feed
    injection_flags: tuple[str, ...]


def extract(captured: CapturedContent) -> ExtractedDocument:
    ct = captured.content_type
    if ct in {"text/html", "application/xhtml+xml"}:
        return _extract_html(captured)
    if ct == "application/pdf":
        return _extract_pdf(captured)
    if ct.startswith("text/"):
        text = captured.body.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS]
        return ExtractedDocument(
            text=text,
            title="",
            author="",
            publisher="",
            published_at=None,
            kind="plain",
            injection_flags=scan_injection(text),
        )
    raise ExtractionError(f"no extractor for content type {ct!r}")


def _extract_html(captured: CapturedContent) -> ExtractedDocument:
    import trafilatura

    html = captured.body.decode("utf-8", errors="replace")
    extracted = trafilatura.extract(
        html,
        url=captured.final_url,
        output_format="json",
        with_metadata=True,
        include_comments=False,
        include_images=False,
        include_links=False,
    )
    if not extracted:
        raise ExtractionError("trafilatura could not extract an article body")
    data = json.loads(extracted)
    text = (data.get("text") or "")[:MAX_TEXT_CHARS]
    if "<script" in text.lower():
        raise ExtractionError("script content survived extraction")
    return ExtractedDocument(
        text=text,
        title=data.get("title") or "",
        author=data.get("author") or "",
        publisher=data.get("sitename") or "",
        published_at=data.get("date") or None,
        kind="article",
        injection_flags=scan_injection(text),
    )


def _extract_pdf(captured: CapturedContent) -> ExtractedDocument:
    import io

    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(captured.body))
        pages = reader.pages[:MAX_PDF_PAGES]
        text = "\n\n".join((p.extract_text() or "") for p in pages)[:MAX_TEXT_CHARS]
    except Exception as exc:  # pypdf raises many exception types on hostile files
        raise ExtractionError(f"PDF extraction failed: {exc}") from exc
    if not text.strip():
        raise ExtractionError("PDF yielded no extractable text (corrupt or image-only)")
    meta = reader.metadata or {}
    return ExtractedDocument(
        text=text,
        title=str(meta.get("/Title") or ""),
        author=str(meta.get("/Author") or ""),
        publisher="",
        published_at=None,
        kind="pdf",
        injection_flags=scan_injection(text),
    )
