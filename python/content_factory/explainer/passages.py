"""Resolve quotes against a replayed page: DOM ranges, line rects, headings and PNG tiles."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from content_factory.explainer.errors import ContractIssue, EpisodeInvalidError
from content_factory.schemas.explainer import (
    CaptureQuote,
    CaptureSection,
    DomRangeLocator,
    PageRect,
    Viewport,
)

REPO = Path(__file__).resolve().parents[3]
RESOLVER = REPO / "apps" / "renderer" / "scripts" / "resolve-passages.mjs"
EXTRACTOR = RESOLVER.name
EXTRACTOR_VERSION = "0.1.0"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


class PassageResolveError(RuntimeError):
    """The resolver process failed for a reason other than a missing or ambiguous quote."""


@dataclass(frozen=True)
class QuoteRequest:
    text: str
    occurrence_index: int | None = None
    claim_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Tile:
    path: Path
    y_px: int


@dataclass(frozen=True)
class ResolvedPage:
    url: str
    viewport: Viewport
    sections: tuple[CaptureSection, ...]
    quotes: tuple[CaptureQuote, ...]
    page_height_px: float
    tiles: tuple[Tile, ...]
    text_sha256: str


def section_id(heading: str, order: int) -> str:
    return "sec_" + _short_hash(f"{order}:{heading}")


def quote_id(text: str, occurrence_index: int) -> str:
    return "qt_" + _short_hash(f"{occurrence_index}:{text}")


def text_sha256(text: str) -> str:
    """Identity of the page's rendered text with whitespace runs collapsed."""
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()


def closest_text(needle: str, page_text: str) -> str:
    """The page sentence most like the needle, so a not-found message shows what is there."""
    candidates = [c.strip() for c in _SENTENCE_END.split(page_text) if c.strip()]
    if not candidates:
        return ""
    matcher = difflib.SequenceMatcher(None, needle)
    best, best_ratio = "", -1.0
    for candidate in candidates:
        matcher.set_seq2(candidate)
        if matcher.real_quick_ratio() <= best_ratio or matcher.quick_ratio() <= best_ratio:
            continue
        ratio = matcher.ratio()
        if ratio > best_ratio:
            best, best_ratio = candidate, ratio
    return best


def not_found_issue(request: QuoteRequest, index: int, count: int, page_text: str) -> ContractIssue:
    where = f"quote[{index}] {request.text[:60]!r}"
    ids = (quote_id(request.text, request.occurrence_index or 0),)
    if count and request.occurrence_index is not None:
        message = (
            f"passage_not_found: occurrence_index {request.occurrence_index} is out of range; "
            f"the text occurs {count} time(s)."
        )
        return ContractIssue(
            "invalid_reference", where, message, "use an index below the count.", ids
        )
    closest = closest_text(request.text, page_text)
    message = f"passage_not_found: the text is not in the page; the closest text is {closest!r}."
    fix = "quote the page's text exactly (copy it from the capture) or re-capture the page."
    return ContractIssue("invalid_reference", where, message, fix, ids)


def ambiguous_issue(request: QuoteRequest, index: int, count: int) -> ContractIssue:
    where = f"quote[{index}] {request.text[:60]!r}"
    message = f"passage_ambiguous: the text occurs {count} times."
    fix = f"add occurrence_index (0..{count - 1}) to pick one."
    return ContractIssue("invalid_reference", where, message, fix, (quote_id(request.text, 0),))


def resolve_quotes(
    replay_url: str,
    quotes: Sequence[QuoteRequest],
    *,
    viewport: Viewport,
    tiles_dir: Path,
    timeout_s: float = 240,
) -> ResolvedPage:
    """Run the Playwright resolver; missing or ambiguous quotes become issues, raised together."""
    payload = _run_resolver(replay_url, quotes, viewport, tiles_dir, timeout_s)
    issues: list[ContractIssue] = []
    for error in payload["errors"]:
        index = int(error["quote_index"])
        request = quotes[index]
        count = int(error.get("count", 0))
        if error["code"] == "passage_ambiguous":
            issues.append(ambiguous_issue(request, index, count))
        else:
            issues.append(not_found_issue(request, index, count, payload["text"]))
    if issues:
        raise EpisodeInvalidError(issues)
    sections, section_of = _sections(payload)
    located = tuple(
        _quote(found, quotes[int(found["index"])], section_of[found["section_index"]])
        for found in payload["quotes"]
    )
    return ResolvedPage(
        url=replay_url,
        viewport=viewport,
        sections=sections,
        quotes=located,
        page_height_px=float(payload["page_height_px"]),
        tiles=tuple(Tile(Path(t["path"]), int(t["y_px"])) for t in payload["tiles"]),
        text_sha256=text_sha256(payload["text"]),
    )


def _run_resolver(
    url: str, quotes: Sequence[QuoteRequest], viewport: Viewport, tiles_dir: Path, timeout_s: float
) -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        msg = "node is not on PATH"
        raise PassageResolveError(msg)
    tiles_dir.mkdir(parents=True, exist_ok=True)
    requests = [{"text": q.text, "occurrence_index": q.occurrence_index} for q in quotes]
    with tempfile.TemporaryDirectory(prefix="resolve-") as tmp:
        requests_path = Path(tmp) / "requests.json"
        requests_path.write_text(json.dumps(requests))
        argv = [
            node,
            str(RESOLVER),
            *("--url", url),
            *("--quotes", str(requests_path)),
            *("--tiles-dir", str(tiles_dir)),
            *("--width", str(viewport.width)),
            *("--height", str(viewport.height)),
            *("--timeout-ms", str(int(timeout_s * 1000 / 2))),
        ]
        proc = subprocess.run(  # noqa: S603 — fixed argv; the script is ours
            argv,
            cwd=RESOLVER.parent,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    if proc.returncode not in (0, 2) or not proc.stdout.strip():
        msg = f"{EXTRACTOR} failed ({proc.returncode}): {proc.stderr.strip()[-2000:]}"
        raise PassageResolveError(msg)
    return json.loads(proc.stdout)


def _sections(payload: dict[str, Any]) -> tuple[tuple[CaptureSection, ...], dict[Any, str]]:
    """Sections in page order; a quote before any heading gets a synthetic empty one at the top."""
    headings: list[tuple[str, float]] = [
        (str(s["heading"])[:300], max(0.0, float(s["y_px"]))) for s in payload["sections"]
    ]
    orphaned = not headings or any(q["section_index"] is None for q in payload["quotes"])
    if orphaned:
        headings.insert(0, ("", 0.0))
    sections = tuple(
        CaptureSection(section_id=section_id(h, i), heading=h, order=i, scroll_y_px=round(y, 2))
        for i, (h, y) in enumerate(headings)
    )
    shift = 1 if orphaned else 0
    section_of: dict[Any, str] = {
        index: sections[index + shift].section_id for index in range(len(payload["sections"]))
    }
    if orphaned:
        section_of[None] = sections[0].section_id
    return sections, section_of


def _quote(found: dict[str, Any], request: QuoteRequest, section: str) -> CaptureQuote:
    text = str(found["text"])
    occurrence = int(found["occurrence_index"])
    return CaptureQuote(
        quote_id=quote_id(text, occurrence),
        section_id=section,
        text=text,
        context_before=str(found["context_before"])[-400:],
        context_after=str(found["context_after"])[:400],
        locator=DomRangeLocator(
            kind="dom_range",
            start_path=str(found["start_path"]),
            start_offset=int(found["start_offset"]),
            end_path=str(found["end_path"]),
            end_offset=int(found["end_offset"]),
        ),
        occurrence_index=occurrence,
        line_rects=tuple(_rect(r) for r in found["line_rects"]),
        claim_ids=request.claim_ids,
    )


def _rect(raw: dict[str, Any]) -> PageRect:
    x, y = max(0.0, float(raw["x"])), max(0.0, float(raw["y"]))
    width = float(raw["width"]) - (x - float(raw["x"]))
    height = float(raw["height"]) - (y - float(raw["y"]))
    return PageRect(x=round(x, 2), y=round(y, 2), width=round(width, 2), height=round(height, 2))


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]
