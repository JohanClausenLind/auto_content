"""Offer supply sources. The default is a deterministic fixture; live provider/broker adapters
(Vast.ai, Runpod, Shadeform, Prime Intellect, or SkyPilot/dstack catalogs) are added behind the
same protocol once the operator supplies accounts — never scraped, never invented. Stale quotes
must be revalidated before purchase; an expired offer is not a candidate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from content_factory.schemas.offers import ComputeOffer


class OfferSource(Protocol):
    source_api: str

    def list_offers(self) -> tuple[ComputeOffer, ...]: ...


@dataclass(frozen=True)
class FixtureOfferSource:
    """Canned offers for tests and dry-run planning. Clearly labeled: source_api='fixture'."""

    offers: tuple[ComputeOffer, ...]
    source_api: str = "fixture"

    def list_offers(self) -> tuple[ComputeOffer, ...]:
        return self.offers


def fresh_offers(offers: tuple[ComputeOffer, ...], *, now_iso: str) -> tuple[ComputeOffer, ...]:
    """Drop expired quotes; the caller re-queries its sources for replacements."""
    now = datetime.fromisoformat(now_iso)
    keep = []
    for o in offers:
        if o.expires_at is not None and datetime.fromisoformat(o.expires_at) <= now:
            continue
        keep.append(o)
    return tuple(keep)
