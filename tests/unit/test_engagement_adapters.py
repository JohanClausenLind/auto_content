from __future__ import annotations

from pathlib import Path

import httpx

from content_factory.engagement.adapters import DiscordEngagement, MastodonEngagement
from content_factory.engagement.fan_memory import FanMemoryStore


def test_mastodon_mentions_normalize_and_cursor() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(
            200,
            json=[
                {
                    "id": "n2",
                    "created_at": "2026-09-01T10:00:00Z",
                    "account": {"acct": "fan@masto.example"},
                    "status": {
                        "id": "s2",
                        "in_reply_to_id": "s1",
                        "content": "<p>Love this! How was the data &amp; sourced?</p>",
                    },
                },
            ],
        )

    adapter = MastodonEngagement(
        base_url="https://masto.example",
        token_getter=lambda: "tok",
        transport=httpx.MockTransport(handler),
    )
    msgs = adapter.fetch_inbound(since_id="n1")
    assert seen[0]["since_id"] == "n1"
    assert msgs[0].fan_id == "fan@masto.example" and msgs[0].thread_id == "s1"
    assert (
        msgs[0].text == "Love this! How was the data & sourced?"
    )  # HTML stripped, entities decoded


def test_discord_reads_skip_bots_and_use_after_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"].startswith("Bot ")
        assert request.url.params["after"] == "m1"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "m3",
                    "timestamp": "t",
                    "author": {"id": "bot9", "bot": True},
                    "content": "beep",
                },
                {
                    "id": "m2",
                    "timestamp": "t",
                    "author": {"id": "fan7"},
                    "content": "when is the next video?",
                },
            ],
        )

    adapter = DiscordEngagement(
        channel_id="chan1",
        bot_token_getter=lambda: "bot-tok",
        transport=httpx.MockTransport(handler),
    )
    msgs = adapter.fetch_inbound(since_id="m1")
    assert len(msgs) == 1 and msgs[0].fan_id == "fan7" and msgs[0].thread_id == "chan1"


def test_fan_memory_name_topics_stage_and_deletion(tmp_path: Path) -> None:
    store = FanMemoryStore(tmp_path)
    store.observe("discord", "chan1", "fan7", "hi! I'm Sara, loved the turbine video")
    memory = store.observe(
        "discord", "chan1", "fan7", "call me Sara btw — question about siting rules"
    )
    assert (
        memory.given_name == "Sara"
        and memory.message_count == 2
        and memory.relationship_stage == "new"
    )
    assert "siting" in memory.last_topics or "turbine" in memory.last_topics
    for i in range(5):
        store.observe("discord", "chan1", "fan7", f"message {i}")
    assert store.get("discord", "chan1", "fan7").relationship_stage == "regular"
    # Scoped per platform account; deletable under privacy policy; persisted.
    assert store.get("mastodon", "acct", "fan7").message_count == 0
    fresh = FanMemoryStore(tmp_path)
    assert fresh.get("discord", "chan1", "fan7").message_count == 7
    assert fresh.delete_fan("discord", "chan1", "fan7") is True
    assert FanMemoryStore(tmp_path).get("discord", "chan1", "fan7").message_count == 0
