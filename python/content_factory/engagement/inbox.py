"""Engagement inbox (24.1): normalized inbound messages, deterministic classification, and the
answer-everything ledger. Reply governance/firewall live in content_factory.personas."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class MessageClass(StrEnum):
    question = "question"
    casual_question = "casual_question"
    compliment = "compliment"
    request = "request"
    spam = "spam"
    harassment = "harassment"
    safety_relevant = "safety_relevant"
    emoji_only = "emoji_only"
    other = "other"


@dataclass(frozen=True)
class InboundMessage:
    message_id: str
    platform: str
    thread_id: str
    fan_id: str
    text: str
    received_at: str


@dataclass(frozen=True)
class Classification:
    message_class: MessageClass
    vip: bool
    reasons: tuple[str, ...]


_SPAM = re.compile(
    r"\b(free followers|crypto signal|onlyfans promo|click my (bio|link)|earn \$\d|dm for promo)\b",
    re.I,
)
_HARASSMENT = re.compile(
    r"\b(kill yourself|kys|you('| a)?re (trash|garbage|worthless)|slur\b|die\b)", re.I
)
_SAFETY = re.compile(
    r"\b(suicide|self[- ]harm|kill myself|hurt myself|abuse at home|i'?m 1[0-7]\b)", re.I
)
_QUESTION = re.compile(r"\?|^(how|what|when|where|why|who|can you|could you|do you)\b", re.I)
_COMPLIMENT = re.compile(
    r"\b(love (this|your)|amazing|beautiful|great video|so good|awesome|underrated)\b", re.I
)
_REQUEST = re.compile(r"\b(please (do|make|cover)|can you (do|make|cover)|request:)\b", re.I)
_EMOJI_ONLY = re.compile(r"^[\s\U0001F000-\U0001FAFF☀-➿❤️!]+$")
_CASUAL = re.compile(r"^(how are you|good (morning|night)|what'?s up|hru)\b", re.I)


def classify(
    message: InboundMessage, *, vip_fans: frozenset[str] = frozenset(), fan_message_count: int = 0
) -> Classification:
    text = message.text.strip()
    reasons: list[str] = []
    vip = message.fan_id in vip_fans or fan_message_count >= 10
    if vip:
        reasons.append("frequent or designated fan")
    if _SAFETY.search(text):
        return Classification(MessageClass.safety_relevant, vip, (*reasons, "safety pattern"))
    if _HARASSMENT.search(text):
        return Classification(MessageClass.harassment, vip, (*reasons, "harassment pattern"))
    if _SPAM.search(text):
        return Classification(MessageClass.spam, vip, (*reasons, "spam pattern"))
    if _EMOJI_ONLY.match(text):
        return Classification(MessageClass.emoji_only, vip, (*reasons, "emoji only"))
    if _CASUAL.match(text):
        return Classification(MessageClass.casual_question, vip, (*reasons, "casual greeting"))
    if _REQUEST.search(text):
        return Classification(MessageClass.request, vip, (*reasons, "content request"))
    if _QUESTION.search(text):
        return Classification(MessageClass.question, vip, (*reasons, "question shape"))
    if _COMPLIMENT.search(text):
        return Classification(MessageClass.compliment, vip, (*reasons, "compliment"))
    return Classification(MessageClass.other, vip, tuple(reasons))


@dataclass
class AnswerLedger:
    """Answer-everything (24.2): every non-spam inbound ends with a response or a recorded skip."""

    entries: dict[str, dict] = field(default_factory=dict)

    def record_response(self, message_id: str, *, disposition: str) -> None:
        self.entries[message_id] = {"outcome": "responded", "disposition": disposition}

    def record_skip(self, message_id: str, *, reason: str) -> None:
        if not reason.strip():
            msg = "a skip always carries a recorded reason"
            raise ValueError(msg)
        self.entries[message_id] = {"outcome": "skipped", "reason": reason}

    def unanswered(self, all_ids: list[str]) -> list[str]:
        return [i for i in all_ids if i not in self.entries]
