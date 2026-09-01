from __future__ import annotations

from datetime import date

import pytest

from content_factory.engagement.inbox import AnswerLedger, InboundMessage, MessageClass, classify
from content_factory.radar.signals import ArchiveEntry, SignalKind, scan_feed
from content_factory.research.feeds import FeedItem


def msg(text: str, fan: str = "fan1") -> InboundMessage:
    return InboundMessage("m1", "discord", "t1", fan, text, "2026-09-01T10:00:00Z")


def test_classification_covers_classes_and_vip() -> None:
    assert (
        classify(msg("How do you calculate the wind share?")).message_class == MessageClass.question
    )
    assert classify(msg("good morning!! how are you")).message_class == MessageClass.casual_question
    assert classify(msg("love this, so good")).message_class == MessageClass.compliment
    assert (
        classify(msg("can you cover nuclear next? please do")).message_class == MessageClass.request
    )
    assert classify(msg("free followers click my bio")).message_class == MessageClass.spam
    assert classify(msg("kys you're worthless")).message_class == MessageClass.harassment
    assert (
        classify(msg("i've been thinking about suicide")).message_class
        == MessageClass.safety_relevant
    )
    assert classify(msg("❤️🔥🔥")).message_class == MessageClass.emoji_only
    assert classify(msg("hello there", fan="vip9"), vip_fans=frozenset({"vip9"})).vip
    assert classify(msg("hello"), fan_message_count=12).vip


def test_answer_everything_ledger() -> None:
    ledger = AnswerLedger()
    ledger.record_response("m1", disposition="auto_send")
    ledger.record_skip("m2", reason="classified as spam")
    with pytest.raises(ValueError, match="recorded reason"):
        ledger.record_skip("m3", reason="  ")
    assert ledger.unanswered(["m1", "m2", "m3"]) == ["m3"]


def test_radar_signals_gap_overlap_saturation_expiring() -> None:
    archive = [
        ArchiveEntry("Sweden wind share electricity", date(2026, 6, 1)),
        ArchiveEntry("wind share of Sweden electricity mix", date(2026, 4, 2)),
        ArchiveEntry("Sweden electricity wind share record", date(2026, 2, 3)),
    ]
    items = [
        FeedItem("Sweden wind share hits new record in electricity mix", "https://a", None, ""),
        FeedItem(
            "Baltic offshore auction deadline closes Friday", "https://b", None, "bids until friday"
        ),
        FeedItem("How heat pumps changed Nordic demand", "https://c", None, ""),
    ]
    signals = scan_feed(items, archive, today=date(2026, 9, 1))
    by_url = {s.evidence_url: s for s in signals}
    assert (
        by_url["https://a"].kind == SignalKind.originality_risk
        and "saturated" in by_url["https://a"].explanation
    )
    assert by_url["https://b"].kind == SignalKind.expiring_opportunity
    assert by_url["https://c"].kind == SignalKind.topic_gap
    overlap = scan_feed(
        [FeedItem("Sweden wind share explained", "https://d", None, "")],
        archive[:1],
        today=date(2026, 9, 1),
    )
    assert (
        overlap[0].kind == SignalKind.competitor_overlap
        and "differentiation" in overlap[0].explanation
    )
