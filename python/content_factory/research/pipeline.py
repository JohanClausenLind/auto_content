"""Search, fetch, extract, claim: the orchestration the research library never had.

Every part of this already existed and was tested. `search` has a SearXNG provider and a
deterministic fixture one; `fetch.safe_fetch` re-validates every redirect hop against SSRF, caps
the body, allowlists content types and flags injection markers as data; `extract` turns HTML and
PDF into text with an author and a publisher; `claims.build_claim` verifies a number against
evidence and a dataset. Nothing put them in a line, so `stage_research` returned the committed
fixture and the whole library was reachable only from its own tests.

**This is behind an execution flag and it stays there.** It is the one thing in the repo that
reaches the public internet, and `just test` must not. `execution.live_research` is off by default;
with it off `stage_research` uses the fixture exactly as before. The flag is read at the stage, not
here, so this module is honest to import.

What it deliberately does not do: decide anything. No model chooses which result to trust, which
sentence is evidence, or whether a claim is supported — the sentence selection is a token-overlap
match and the verdict is `build_claim`'s arithmetic. A pipeline that asked a model "is this
supported?" would be a pipeline whose citations mean nothing.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

from content_factory.research.claims import build_claim
from content_factory.research.extract import ExtractionError, extract
from content_factory.research.fetch import FetchError, canonical_url, safe_fetch
from content_factory.research.search import (
    FixtureSearchProvider,
    SearchProvider,
    SearchResult,
    SearxngSearchProvider,
)
from content_factory.schemas.base import sha256_hex
from content_factory.schemas.render import DatasetTable
from content_factory.schemas.research import (
    ClaimRecord,
    EvidenceLocator,
    EvidenceRecord,
    SourceClass,
    SourceRecord,
)

MAX_EXCERPT_CHARS = 800
"""``EvidenceRecord.excerpt`` is a bounded quotation, and the bound is the contract's. An excerpt
is a citation, not a copy of the page."""

MIN_EXCERPT_CHARS = 40
"""Below this a "sentence" is a heading, a caption or a nav item. Quoting one as evidence is how a
claim ends up cited to the words "Read more"."""


@dataclass(frozen=True)
class ResearchResult:
    sources: list[SourceRecord] = field(default_factory=list)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    claims: list[ClaimRecord] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    """One line per result that could not be captured, with the reason. Kept rather than dropped:
    "three of eight sources were unreachable" is something an operator has to know before deciding
    whether the film has enough behind it."""


def default_provider(*, repo_root: Path | None = None) -> SearchProvider:
    """The configured search provider, falling back to the committed fixture.

    ``search.offline_fixture_fallback`` is honoured because a machine with no SearXNG must still
    produce a run rather than an exception — and because the fixture provider is what makes this
    module testable at all.
    """
    from content_factory.config import get_settings

    cfg = get_settings().search
    root = repo_root or Path(__file__).resolve().parents[3]
    fixture = root / "fixtures" / "research" / "search.json"
    if cfg.provider == "fixture" or not cfg.endpoint:
        return FixtureSearchProvider(fixture)
    return SearxngSearchProvider(cfg.endpoint)


def _sentences(text: str) -> list[tuple[int, int, str]]:
    """``(start, end, sentence)`` over the extracted text, by offset.

    The offsets are the point: ``EvidenceLocator(kind="char_range")`` records where in the captured
    document the excerpt came from, so a reader can find it again in the bytes that were hashed.
    """
    out: list[tuple[int, int, str]] = []
    start = 0
    for index, char in enumerate(text):
        if char in ".!?\n" and index - start >= MIN_EXCERPT_CHARS:
            sentence = text[start : index + 1].strip()
            if sentence:
                out.append((start, index + 1, sentence[:MAX_EXCERPT_CHARS]))
            start = index + 1
    tail = text[start:].strip()
    if len(tail) >= MIN_EXCERPT_CHARS:
        out.append((start, len(text), tail[:MAX_EXCERPT_CHARS]))
    return out


def _best_sentences(text: str, statement: str, *, limit: int = 2) -> list[tuple[int, int, str]]:
    """The sentences most likely to be about ``statement``, by token overlap and shared numbers.

    Deliberately dumb. A model asked to pick the supporting sentence would be a model deciding
    whether a claim is supported, one step removed — and ``build_claim`` is then verifying the
    number against a sentence chosen *because* it contained that number, which proves nothing.
    Overlap is a retrieval heuristic whose failure mode is an unsupported verdict, not a false one.
    """
    wanted = {w for w in statement.casefold().split() if len(w) > 3}
    scored: list[tuple[int, tuple[int, int, str]]] = []
    for start, end, sentence in _sentences(text):
        tokens = {w for w in sentence.casefold().split() if len(w) > 3}
        scored.append((len(wanted & tokens), (start, end, sentence)))
    scored.sort(key=lambda item: (-item[0], item[1][0]))
    return [payload for score, payload in scored[:limit] if score > 0]


def research_topic(
    topic: str,
    *,
    workspace_id: str,
    objective: str = "",
    statements: list[str] | None = None,
    provider: SearchProvider | None = None,
    max_sources: int = 5,
    dataset: DatasetTable | None = None,
    today: dt.date | None = None,
) -> ResearchResult:
    """One topic in, sources plus evidence plus verified claims out.

    ``statements`` are the sentences whose truth matters — a script's claim-bearing lines. Without
    them the topic itself is the only statement, which is enough to gather sources and not enough
    to verify anything, and the result says so by returning claims with no evidence rather than
    claims with invented evidence.
    """
    provider = provider or default_provider()
    accessed = (today or dt.datetime.now(dt.UTC).date()).isoformat()
    query = f"{topic} {objective}".strip()
    try:
        results: list[SearchResult] = provider.search(query, max_results=max_sources * 2)
    except Exception as exc:  # a search outage is a reportable outcome, not a crash
        return ResearchResult(rejected=[f"search failed: {type(exc).__name__}: {exc}"])

    sources: list[SourceRecord] = []
    evidence: list[EvidenceRecord] = []
    rejected: list[str] = []
    texts: list[tuple[SourceRecord, str]] = []
    for result in results:
        if len(sources) >= max_sources:
            break
        try:
            captured = safe_fetch(result.url)
            document = extract(captured)
        except (FetchError, ExtractionError) as exc:
            rejected.append(f"{result.url}: {type(exc).__name__}: {exc}")
            continue
        if not document.text.strip():
            rejected.append(f"{result.url}: nothing extractable")
            continue
        canonical = canonical_url(captured.final_url)
        source = SourceRecord(
            source_id=f"src_{sha256_hex(canonical.encode())[:12]}",
            workspace_id=workspace_id,
            canonical_url=canonical,
            requested_url=result.url,
            final_url=captured.final_url,
            title=(document.title or result.title)[:500],
            publisher=document.publisher[:200],
            author=document.author[:200],
            # Never invented: a page with no date has no date, and a claim's freshness check is
            # allowed to fail for want of one.
            published_at=document.published_at or result.published,
            accessed_at=accessed,
            capture_sha256=captured.sha256,
            content_type=captured.content_type,
            size_bytes=len(captured.body),
            classification=SourceClass.unknown,
            # Instruction-like text in a fetched page is DATA. Flagged, carried, never obeyed.
            injection_flags=document.injection_flags or captured.injection_flags,
        )
        sources.append(source)
        texts.append((source, document.text))

    claims: list[ClaimRecord] = []
    sources_by_id = {s.source_id: s for s in sources}
    for statement in statements or [topic]:
        for source, text in texts:
            for start, end, sentence in _best_sentences(text, statement):
                evidence.append(
                    EvidenceRecord(
                        evidence_id=f"evd_{sha256_hex(f'{source.source_id}{start}'.encode())[:12]}",
                        source_id=source.source_id,
                        excerpt=sentence,
                        locator=EvidenceLocator(kind="char_range", start=start, end=end),
                        captured_at=accessed,
                    )
                )
        for_statement = [
            e for e in evidence if e.source_id in sources_by_id and statement and e.excerpt
        ]
        claims.append(
            build_claim(
                f"clm_{sha256_hex(statement.encode())[:12]}",
                workspace_id,
                statement,
                for_statement,
                dataset=dataset,
                dataset_id=dataset.dataset_id if dataset else None,
                sources_by_id=sources_by_id,
                today=today,
                checked_at=accessed,
            )
        )
    # Evidence ids are content-addressed, so the same sentence found twice is one record.
    unique = {e.evidence_id: e for e in evidence}
    return ResearchResult(sources, list(unique.values()), claims, rejected)
