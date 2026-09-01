"""Content Opportunity Radar (10): authorized sources only → typed RadarSignals with evidence.
Signals feed topic scouting; they never auto-create public posts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from content_factory.originality.fingerprint import jaccard, shingles
from content_factory.research.feeds import FeedItem


class SignalKind(StrEnum):
    topic_gap = "topic_gap"
    expiring_opportunity = "expiring_opportunity"
    competitor_overlap = "competitor_overlap"
    source_quality = "source_quality"
    originality_risk = "originality_risk"


@dataclass(frozen=True)
class RadarSignal:
    kind: SignalKind
    topic: str
    evidence_url: str
    explanation: str
    observed_at: str


@dataclass(frozen=True)
class ArchiveEntry:
    """What the channel has already covered (Channel Brain memory, phase 12 persists this)."""

    topic: str
    published_on: date


def scan_feed(
    items: list[FeedItem],
    archive: list[ArchiveEntry],
    *,
    today: date | None = None,
    overlap_threshold: float = 0.5,
    saturation_count: int = 3,
) -> list[RadarSignal]:
    today = today or datetime.now(UTC).date()
    now = datetime.now(UTC).isoformat()
    signals: list[RadarSignal] = []
    for item in items:
        # Short titles reorder words freely: compare TOKEN sets, not phrase shingles.
        title_tokens = shingles(item.title, 1)
        overlaps = [
            a for a in archive if jaccard(title_tokens, shingles(a.topic, 1)) >= overlap_threshold
        ]
        if len(overlaps) >= saturation_count:
            signals.append(
                RadarSignal(
                    SignalKind.originality_risk,
                    item.title,
                    item.url,
                    f"already covered {len(overlaps)} times in the archive — saturated",
                    now,
                )
            )
            continue
        if overlaps:
            signals.append(
                RadarSignal(
                    SignalKind.competitor_overlap,
                    item.title,
                    item.url,
                    f"overlaps prior coverage ({overlaps[0].topic!r}); needs differentiation",
                    now,
                )
            )
            continue
        lowered = f"{item.title} {item.summary}".lower()
        if any(
            w in lowered
            for w in ("deadline", "expires", "last day", "closes", "until friday", "this week only")
        ):
            signals.append(
                RadarSignal(
                    SignalKind.expiring_opportunity,
                    item.title,
                    item.url,
                    "time-bound relevance detected in the source text",
                    now,
                )
            )
        else:
            signals.append(
                RadarSignal(
                    SignalKind.topic_gap,
                    item.title,
                    item.url,
                    "no prior coverage in the channel archive",
                    now,
                )
            )
    return signals
