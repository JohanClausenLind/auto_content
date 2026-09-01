from __future__ import annotations

import base64
import hashlib
import hmac
import json

from content_factory.analytics.attribution import (
    ConversionEvent,
    RawObservation,
    attribute_last_touch,
    build_utm_url,
    map_retention_to_timeline,
    parse_utm_identity,
    verify_generic_signature,
    verify_shopify_hmac,
)


def test_utm_builder_is_deterministic_and_preserves_existing_query() -> None:
    url = build_utm_url(
        "https://shop.example/product?ref=nav",
        platform="bluesky",
        campaign_id="cmp_a",
        deliverable_id="dlv_1",
        variant="b",
    )
    assert (
        "utm_source=bluesky" in url
        and "utm_campaign=cmp_a" in url
        and "utm_content=dlv_1%3Ab" in url
    )
    assert "ref=nav" in url
    assert (
        build_utm_url(
            "https://shop.example/product?ref=nav",
            platform="bluesky",
            campaign_id="cmp_a",
            deliverable_id="dlv_1",
            variant="b",
        )
        == url
    )
    identity = parse_utm_identity(url)
    assert identity == {
        "campaign_id": "cmp_a",
        "deliverable_id": "dlv_1",
        "variant": "b",
        "source": "bluesky",
        "medium": "social",
    }


def test_conversion_joins_to_exact_post_and_is_labeled_correlation() -> None:
    url = build_utm_url(
        "https://shop.example/p",
        platform="mastodon",
        campaign_id="cmp_a",
        deliverable_id="dlv_short0001",
        variant="a",
    )
    event = ConversionEvent(
        event_id="ord_1",
        kind="order",
        value=49.0,
        currency="EUR",
        landing_url=url,
        occurred_at="2026-09-01T10:00:00Z",
    )
    record = attribute_last_touch(event)
    assert record is not None
    assert (
        record.deliverable_id == "dlv_short0001"
        and record.variant == "a"
        and record.model == "last_touch"
    )
    assert record.label == "correlation"
    line = record.as_report_line()
    assert "correlation" in line and "confound" in line and "caus" in line
    # Missing UTM identity stays missing — no guessing.
    naked = ConversionEvent(
        event_id="ord_2",
        kind="order",
        value=10.0,
        currency="EUR",
        landing_url="https://shop.example/p",
        occurred_at="2026-09-01T11:00:00Z",
    )
    assert attribute_last_touch(naked) is None


def test_webhook_signatures_shopify_and_generic() -> None:
    body = json.dumps({"id": 1, "landing_site": "/p?utm_campaign=cmp_a"}).encode()
    secret = "shhh"
    good = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    assert verify_shopify_hmac(body, good, secret)
    assert not verify_shopify_hmac(body + b" ", good, secret)
    assert not verify_shopify_hmac(body, good, "wrong")
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_generic_signature(body, sig, secret)
    assert not verify_generic_signature(body, sig[:-2] + "aa", secret)


def test_retention_maps_to_scene_ranges_without_causal_claims() -> None:
    scenes = [("scn_a", 0, 150), ("scn_b", 150, 150), ("scn_c", 300, 100)]
    points = [(i / 20, 1.0 - 0.02 * i - (0.3 if i >= 9 else 0.0)) for i in range(20)]
    mapped = map_retention_to_timeline(points, total_frames=400, fps=30, scenes=scenes)
    assert [m["scene_id"] for m in mapped] == ["scn_a", "scn_b", "scn_c"]
    assert mapped[0]["start_s"] == 0.0 and mapped[1]["start_s"] == 5.0
    b = mapped[1]["retention"]
    assert b is not None and b["delta"] < 0  # the drop lands in scene b's range
    for m in mapped:
        assert "caus" not in json.dumps(m).replace("causes are not established", "")
        assert m["retention"] is None or "observational" in m["note"]


def test_raw_observations_preserve_names_and_missing_values() -> None:
    obs = RawObservation(
        provider="youtube",
        metric_name="estimatedMinutesWatched",
        definition="as defined by YouTube Analytics API dimension docs",
        value=None,
        scope="video:abc",
        window="2026-08",
        collected_at="2026-09-01",
    )
    assert obs.metric_name == "estimatedMinutesWatched"  # never renamed
    assert obs.value is None  # missing stays missing
