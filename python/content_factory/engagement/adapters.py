"""EngagementProvider read adapters (24.1), separate from publishing capabilities. Official APIs
only, within their documented rules; surfaces without an API stay manual-only with deep links."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

import httpx

from content_factory.engagement.inbox import InboundMessage


class EngagementReadError(Exception):
    pass


class EngagementProvider(ABC):
    platform: str
    supports_dms: bool

    @abstractmethod
    def fetch_inbound(
        self, *, since_id: str | None = None, limit: int = 40
    ) -> list[InboundMessage]:
        """Newest inbound comments/replies/mentions, normalized. Cursored by since_id."""


def _strip_html(html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"').strip()


class MastodonEngagement(EngagementProvider):
    """GET /api/v1/notifications?types[]=mention — mentions/replies to the connected account."""

    platform = "mastodon"
    supports_dms = True  # direct-visibility statuses arrive as mentions

    def __init__(
        self, *, base_url: str, token_getter, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=30, transport=transport)
        self._get_token = token_getter

    def fetch_inbound(
        self, *, since_id: str | None = None, limit: int = 40
    ) -> list[InboundMessage]:
        params: dict[str, str | int | list[str]] = {"limit": min(limit, 80), "types[]": ["mention"]}
        if since_id:
            params["since_id"] = since_id
        resp = self._http.get(
            "/api/v1/notifications",
            params=params,
            headers={"Authorization": f"Bearer {self._get_token()}"},
        )
        if resp.status_code >= 400:
            raise EngagementReadError(f"notifications -> {resp.status_code}: {resp.text[:200]}")
        out: list[InboundMessage] = []
        for n in resp.json():
            status = n.get("status") or {}
            account = n.get("account") or {}
            out.append(
                InboundMessage(
                    message_id=str(n["id"]),
                    platform=self.platform,
                    thread_id=str(status.get("in_reply_to_id") or status.get("id") or n["id"]),
                    fan_id=str(account.get("acct") or account.get("id") or "unknown"),
                    text=_strip_html(status.get("content", "")),
                    received_at=str(n.get("created_at", "")),
                )
            )
        return out


class DiscordEngagement(EngagementProvider):
    """Bot token channel reads: GET /channels/{id}/messages (MESSAGE_CONTENT intent required;
    self-enabled under 10k servers). Replies to the channel are the inbound stream."""

    platform = "discord"
    supports_dms = False

    def __init__(
        self,
        *,
        channel_id: str,
        bot_token_getter,
        transport: httpx.BaseTransport | None = None,
        api_base: str = "https://discord.com/api/v10",
    ) -> None:
        self._http = httpx.Client(base_url=api_base, timeout=30, transport=transport)
        self._channel_id = channel_id
        self._get_token = bot_token_getter

    def fetch_inbound(
        self, *, since_id: str | None = None, limit: int = 40
    ) -> list[InboundMessage]:
        params: dict[str, str | int] = {"limit": min(limit, 100)}
        if since_id:
            params["after"] = since_id
        resp = self._http.get(
            f"/channels/{self._channel_id}/messages",
            params=params,
            headers={"Authorization": f"Bot {self._get_token()}"},
        )
        if resp.status_code >= 400:
            raise EngagementReadError(f"messages -> {resp.status_code}: {resp.text[:200]}")
        out: list[InboundMessage] = []
        for msg in resp.json():
            author = msg.get("author") or {}
            if author.get("bot"):
                continue  # never converse with other bots
            out.append(
                InboundMessage(
                    message_id=str(msg["id"]),
                    platform=self.platform,
                    thread_id=str(
                        msg.get("message_reference", {}).get("message_id") or self._channel_id
                    ),
                    fan_id=str(author.get("id", "unknown")),
                    text=str(msg.get("content", "")),
                    received_at=str(msg.get("timestamp", "")),
                )
            )
        return out


MANUAL_ONLY_SURFACES: dict[str, str] = {
    "linkedin_comments": "no official member comment-read API (r_member_social is closed) — handle in the app via the deep link",  # noqa: E501
    "tiktok_dms": "no official DM API — handle in the TikTok app",
}
