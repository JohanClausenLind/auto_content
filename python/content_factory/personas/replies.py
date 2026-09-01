"""Reply drafting governance (24.2, 24.3): autonomy tiers, variation pressure, schedule-gated
human-like release. The drafting model is pluggable; the governance here is not."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum

from content_factory.originality.fingerprint import jaccard, shingles
from content_factory.personas.firewall import (
    FirewallAction,
    FirewallDecision,
    RealPersonDenyList,
    screen_inbound,
    screen_outbound,
)


class Autonomy(StrEnum):
    draft_only = "draft_only"
    human_approve_each = "human_approve_each"
    auto_send_low_risk = "auto_send_low_risk"


class ReplyDisposition(StrEnum):
    auto_send = "auto_send"
    needs_approval = "needs_approval"
    escalated = "escalated"
    blocked = "blocked"
    skipped = "skipped"


@dataclass(frozen=True)
class ReplyDecision:
    disposition: ReplyDisposition
    reason: str
    firewall: FirewallDecision | None = None
    release_at: datetime | None = None


LOW_RISK_CLASSES = frozenset({"compliment", "casual_question", "emoji_only"})


def govern_reply(
    *,
    inbound: str,
    draft: str | None,
    message_class: str,
    autonomy: Autonomy,
    deny_list: RealPersonDenyList,
    recent_replies: list[str],
    counterpart_flagged_minor: bool = False,
    hourly_auto_sends: int = 0,
    hourly_cap: int = 12,
    is_spam: bool = False,
) -> ReplyDecision:
    if is_spam:
        return ReplyDecision(
            ReplyDisposition.skipped,
            "classified as spam (answer-everything records the skip reason)",
        )
    inbound_screen = screen_inbound(inbound)
    if inbound_screen.action == FirewallAction.escalate_to_human:
        return ReplyDecision(
            ReplyDisposition.escalated, inbound_screen.guidance, firewall=inbound_screen
        )
    if draft is None:
        return ReplyDecision(
            ReplyDisposition.needs_approval, "no draft produced; a human answers directly"
        )
    autonomous = autonomy == Autonomy.auto_send_low_risk and message_class in LOW_RISK_CLASSES
    outbound_screen = screen_outbound(
        draft,
        deny_list=deny_list,
        counterpart_flagged_minor=counterpart_flagged_minor,
        autonomous=autonomous,
    )
    if not outbound_screen.allowed:
        return ReplyDecision(
            ReplyDisposition.blocked, outbound_screen.guidance, firewall=outbound_screen
        )
    # Variation pressure: a hundred fans must not receive the same sentence.
    for prior in recent_replies[-50:]:
        if jaccard(shingles(draft, 2), shingles(prior, 2)) >= 0.8:
            return ReplyDecision(
                ReplyDisposition.needs_approval,
                "too similar to a recent reply — vary the wording (variation pressure)",
            )
    if autonomy in {Autonomy.draft_only, Autonomy.human_approve_each} or not autonomous:
        return ReplyDecision(
            ReplyDisposition.needs_approval,
            f"autonomy tier {autonomy.value} requires a human sender",
        )
    if hourly_auto_sends >= hourly_cap:
        return ReplyDecision(
            ReplyDisposition.needs_approval, f"auto-send hourly cap ({hourly_cap}) reached"
        )
    return ReplyDecision(
        ReplyDisposition.auto_send, "low-risk class within caps; schedule gates the release time"
    )


@dataclass(frozen=True)
class PersonaSchedule:
    """Awake/asleep windows with jitter; sends release only inside windows, never metronomically."""

    timezone_offset_hours: int = 0
    awake_start: time = time(8, 30)
    awake_end: time = time(23, 15)
    min_delay_s: int = 45
    max_delay_s: int = 25 * 60
    jitter_seed: str = "persona"

    def _rng(self, key: str) -> random.Random:
        return random.Random(
            int(hashlib.sha256(f"{self.jitter_seed}|{key}".encode()).hexdigest()[:12], 16)
        )

    def release_time(self, now: datetime, *, message_id: str) -> datetime:
        """Deterministic per message (idempotent retries), human-shaped delays."""
        rng = self._rng(message_id)
        delay = timedelta(seconds=rng.randint(self.min_delay_s, self.max_delay_s))
        candidate = now + delay
        local = candidate + timedelta(hours=self.timezone_offset_hours)
        day_jitter = timedelta(minutes=rng.randint(-20, 35))
        start = (datetime.combine(local.date(), self.awake_start, tzinfo=UTC) + day_jitter).time()
        end = self.awake_end
        if local.time() < start:
            wake = datetime.combine(local.date(), start, tzinfo=candidate.tzinfo) - timedelta(
                hours=self.timezone_offset_hours
            )
            return wake + timedelta(seconds=rng.randint(120, 1800))
        if local.time() > end:
            next_day = local.date() + timedelta(days=1)
            wake = datetime.combine(next_day, start, tzinfo=candidate.tzinfo) - timedelta(
                hours=self.timezone_offset_hours
            )
            return wake + timedelta(seconds=rng.randint(120, 1800))
        return candidate
