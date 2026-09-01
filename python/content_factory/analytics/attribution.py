"""Analytics & revenue attribution (23): raw observations stay raw; joins are by UTM identity;
every conclusion is labeled correlation. Nothing here mutates the Channel Brain."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


@dataclass(frozen=True)
class UTMPolicy:
    source_by_platform: dict[str, str] = field(
        default_factory=lambda: {
            "bluesky": "bluesky",
            "mastodon": "mastodon",
            "discord": "discord",
            "export": "export",
        }
    )
    medium: str = "social"


def build_utm_url(
    url: str,
    *,
    platform: str,
    campaign_id: str,
    deliverable_id: str,
    variant: str = "a",
    policy: UTMPolicy | None = None,
) -> str:
    """Governed UTM builder: parameters derive from destination/campaign/deliverable/variant IDs
    deterministically — never hand-typed, never dropped."""
    policy = policy or UTMPolicy()
    parsed = urlparse(url)
    existing = {k: v[0] for k, v in parse_qs(parsed.query).items() if not k.startswith("utm_")}
    params = {
        **existing,
        "utm_source": policy.source_by_platform.get(platform, platform),
        "utm_medium": policy.medium,
        "utm_campaign": campaign_id,
        "utm_content": f"{deliverable_id}:{variant}",
    }
    return urlunparse(parsed._replace(query=urlencode(sorted(params.items()))))


def parse_utm_identity(url: str) -> dict[str, str] | None:
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    if "utm_campaign" not in q or "utm_content" not in q:
        return None
    deliverable_id, _, variant = q["utm_content"].partition(":")
    return {
        "campaign_id": q["utm_campaign"],
        "deliverable_id": deliverable_id,
        "variant": variant or "a",
        "source": q.get("utm_source", ""),
        "medium": q.get("utm_medium", ""),
    }


def verify_shopify_hmac(body: bytes, header_b64: str, client_secret: str) -> bool:
    import base64

    digest = hmac.new(client_secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), header_b64)


def verify_generic_signature(body: bytes, signature_hex: str, secret: str) -> bool:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature_hex)


@dataclass(frozen=True)
class ConversionEvent:
    event_id: str
    kind: str  # order | lead | subscription
    value: float
    currency: str
    landing_url: str
    occurred_at: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AttributionRecord:
    """A conversion joined to the exact post/variant through UTM identity. ALWAYS correlation."""

    event_id: str
    campaign_id: str
    deliverable_id: str
    variant: str
    model: str  # "last_touch" first; others later
    window_days: int
    label: str = "correlation"  # never "caused"

    def as_report_line(self) -> str:
        return (
            f"{self.kind_line()} is associated with {self.deliverable_id} (variant {self.variant}) "
            f"under {self.model} attribution within {self.window_days} days. This is a correlation; "  # noqa: E501
            f"topic, timing, and audience confound any causal reading."
        )

    def kind_line(self) -> str:
        return f"conversion {self.event_id}"


def attribute_last_touch(
    event: ConversionEvent, *, window_days: int = 30
) -> AttributionRecord | None:
    identity = parse_utm_identity(event.landing_url)
    if identity is None:
        return None  # missing stays missing — never guessed
    return AttributionRecord(
        event_id=event.event_id,
        campaign_id=identity["campaign_id"],
        deliverable_id=identity["deliverable_id"],
        variant=identity["variant"],
        model="last_touch",
        window_days=window_days,
    )


@dataclass(frozen=True)
class RawObservation:
    """Immutable provider observation: raw metric name + definition preserved verbatim."""

    provider: str
    metric_name: str
    definition: str
    value: float | None
    scope: str
    window: str
    collected_at: str


def map_retention_to_timeline(
    retention_points: list[tuple[float, float]],  # (fraction_of_video, audience_fraction)
    total_frames: int,
    fps: int,
    scenes: list[tuple[str, int, int]],  # (scene_id, start_frame, duration_frames)
) -> list[dict[str, Any]]:
    """Map an audience-retention curve onto exact scenes/time ranges. Output is descriptive only:
    drops are located, never explained."""
    out: list[dict[str, Any]] = []
    for scene_id, start, duration in scenes:
        s_frac = start / total_frames
        e_frac = (start + duration) / total_frames
        points = [p for p in retention_points if s_frac <= p[0] < e_frac]
        if not points:
            out.append(
                {
                    "scene_id": scene_id,
                    "start_s": round(start / fps, 2),
                    "end_s": round((start + duration) / fps, 2),
                    "retention": None,
                    "note": "no data points in this range (missing stays missing)",
                }
            )
            continue
        entry_r = points[0][1]
        exit_r = points[-1][1]
        out.append(
            {
                "scene_id": scene_id,
                "start_s": round(start / fps, 2),
                "end_s": round((start + duration) / fps, 2),
                "retention": {
                    "entry": entry_r,
                    "exit": exit_r,
                    "delta": round(exit_r - entry_r, 4),
                },
                "note": "observational: a drop here coincides with this scene; causes are not established",  # noqa: E501
            }
        )
    return out
