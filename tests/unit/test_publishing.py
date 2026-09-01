"""Adapter contract + idempotency tests with adversarial mocks (phase 9 gate, offline):
exactly-once under chaos retries, ambiguous responses become reconciliation not blind retries,
kill switch halts everything, mention safety, capability validation."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from content_factory.distribution.backend import (
    MediaAttachment,
    PostPackage,
    PublishBlockedError,
    PublishState,
)
from content_factory.distribution.bluesky import BlueskyBackend
from content_factory.distribution.discord import DiscordWebhookBackend
from content_factory.distribution.mastodon import MastodonBackend
from content_factory.distribution.publisher import IntentStore, PublishPolicy, publish_with_intent

PKG = PostPackage(
    text="Wind supplied about 21% of Sweden's electricity in 2025.",
    idempotency_token="intent-abc123",
)
POLICY = PublishPolicy(kill_switch=False, distribution_enabled=True)


class MastodonServer:
    """Adversarial fixture instance: fails, times out, and honours Idempotency-Key."""

    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.idempotency: dict[str, dict] = {}
        self.fail_next: list[str] = []  # queue of behaviours: "500", "timeout-after-post"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v2/instance":
            return httpx.Response(
                200,
                json={
                    "configuration": {
                        "statuses": {"max_characters": 500, "max_media_attachments": 4},
                        "media_attachments": {"image_size_limit": 1000000},
                    }
                },
            )
        if path == "/api/v1/accounts/verify_credentials":
            return httpx.Response(200, json={"id": "acct1"})
        if path == "/api/v1/accounts/acct1/statuses":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": p["id"],
                        "content": f"<p>{p['status']}</p>",
                        "url": f"https://masto.example/@op/{p['id']}",
                    }
                    for p in self.posts
                ],
            )
        if path == "/api/v1/statuses":
            if self.fail_next:
                mode = self.fail_next.pop(0)
                if mode == "500":
                    return httpx.Response(500, json={"error": "boom"})
                if mode == "timeout-after-post":
                    import json as _json

                    body = _json.loads(request.content)
                    post = {"id": str(1000 + len(self.posts)), "status": body["status"]}
                    self.posts.append(post)
                    raise httpx.ReadTimeout("timed out reading response", request=request)
            key = request.headers.get("Idempotency-Key")
            if key and key in self.idempotency:
                p = self.idempotency[key]
                return httpx.Response(
                    200, json={"id": p["id"], "url": f"https://masto.example/@op/{p['id']}"}
                )
            import json as _json

            body = _json.loads(request.content)
            post = {"id": str(1000 + len(self.posts)), "status": body["status"]}
            self.posts.append(post)
            if key:
                self.idempotency[key] = post
            return httpx.Response(
                200, json={"id": post["id"], "url": f"https://masto.example/@op/{post['id']}"}
            )
        return httpx.Response(404)


def masto(server: MastodonServer) -> MastodonBackend:
    return MastodonBackend(
        base_url="https://masto.example",
        token_getter=lambda: "tok",
        transport=httpx.MockTransport(server.handler),
    )


def test_exactly_once_under_chaos_retries(tmp_path: Path) -> None:
    server = MastodonServer()
    server.fail_next = [
        "500",
        "500",
        "timeout-after-post",
    ]  # two transient failures, then an ambiguous success
    backend = masto(server)
    store = IntentStore(tmp_path)
    state, receipt = publish_with_intent("key1", PKG, backend, store, POLICY)
    assert state == PublishState.published and receipt is not None
    assert (
        len(server.posts) == 1
    )  # the timeout's landed post was found by reconciliation, not re-posted
    # A later retry of the same intent returns the stored receipt without touching the API.
    state2, receipt2 = publish_with_intent("key1", PKG, backend, store, POLICY)
    assert state2 == PublishState.published and receipt2 is not None
    assert receipt2.remote_id == receipt.remote_id
    assert len(server.posts) == 1


def test_ambiguous_without_reconciliation_stays_blocking(tmp_path: Path) -> None:
    class NoReadBackend(MastodonBackend):
        def find_existing(self, package):
            return None

    server = MastodonServer()
    server.fail_next = ["timeout-after-post"]
    backend = NoReadBackend(
        base_url="https://masto.example",
        token_getter=lambda: "tok",
        transport=httpx.MockTransport(server.handler),
    )
    store = IntentStore(tmp_path)
    state, receipt = publish_with_intent("key2", PKG, backend, store, POLICY)
    assert state == PublishState.ambiguous and receipt is None
    record = store.load("key2")
    assert record is not None and record["state"] == "ambiguous"
    # Later, reconciliation succeeds (the read path is back) and resolves WITHOUT a new post.
    healthy = masto(server)
    state2, receipt2 = publish_with_intent("key2", PKG, healthy, store, POLICY)
    assert state2 == PublishState.published and receipt2 is not None
    assert len(server.posts) == 1


def test_kill_switch_and_disabled_distribution_block_everything(tmp_path: Path) -> None:
    server = MastodonServer()
    store = IntentStore(tmp_path)
    with pytest.raises(PublishBlockedError, match="kill switch"):
        publish_with_intent(
            "k",
            PKG,
            masto(server),
            store,
            PublishPolicy(kill_switch=True, distribution_enabled=True),
        )
    with pytest.raises(PublishBlockedError, match="disabled"):
        publish_with_intent(
            "k",
            PKG,
            masto(server),
            store,
            PublishPolicy(kill_switch=False, distribution_enabled=False),
        )
    with pytest.raises(PublishBlockedError, match="not authorized"):
        publish_with_intent(
            "k",
            PKG,
            masto(server),
            store,
            PublishPolicy(
                kill_switch=False, distribution_enabled=True, allowed_visibilities=("draft",)
            ),
        )
    assert server.posts == []


def test_capability_validation_blocks_locally(tmp_path: Path) -> None:
    server = MastodonServer()
    big = PostPackage(text="x" * 501)
    store = IntentStore(tmp_path)
    with pytest.raises(PublishBlockedError, match="allows 500"):
        publish_with_intent("k3", big, masto(server), store, POLICY)
    blocked = store.load("k3")
    assert server.posts == [] and blocked is not None and blocked["state"] == "blocked"
    no_alt = PostPackage(
        text="hi", media=(MediaAttachment(data=b"x" * 10, mime="image/png", alt_text=" "),)
    )
    with pytest.raises(PublishBlockedError, match="alt text"):
        publish_with_intent("k4", no_alt, masto(server), store, POLICY)


def test_bluesky_validation_and_reconciliation() -> None:
    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("createSession"):
            return httpx.Response(200, json={"accessJwt": "jwt", "did": "did:plc:abc"})
        if path.endswith("createRecord"):
            import json as _json

            record = _json.loads(request.content)["record"]
            posts.append(record)
            return httpx.Response(
                200,
                json={
                    "uri": f"at://did:plc:abc/app.bsky.feed.post/rkey{len(posts)}",
                    "cid": "cid1",
                },
            )
        if path.endswith("listRecords"):
            return httpx.Response(
                200,
                json={
                    "records": [
                        {"uri": f"at://did:plc:abc/app.bsky.feed.post/rkey{i + 1}", "value": r}
                        for i, r in enumerate(posts)
                    ]
                },
            )
        return httpx.Response(404)

    backend = BlueskyBackend(
        service="https://bsky.social",
        handle="op.example",
        app_password_getter=lambda: "app-pass",
        transport=httpx.MockTransport(handler),
    )
    assert backend.validate(PostPackage(text="x" * 301)) != []
    assert backend.validate(PostPackage(text="ok", visibility="draft")) != []
    receipt = backend.publish(PKG)
    assert receipt.remote_id.startswith("at://")
    assert receipt.url is not None and "bsky.app/profile/op.example" in receipt.url
    found = backend.find_existing(PKG)
    assert found is not None and found.remote_id == receipt.remote_id
    assert backend.find_existing(PostPackage(text="something else")) is None


def test_discord_mention_safety_and_length() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen.append(_json.loads(request.content))
        return httpx.Response(200, json={"id": "msg1", "channel_id": "chan1"})

    backend = DiscordWebhookBackend(
        webhook_url_getter=lambda: "https://discord.com/api/webhooks/1/tok",
        transport=httpx.MockTransport(handler),
    )
    backend.publish(PostPackage(text="hello @everyone <@123>"))
    assert seen[0]["allowed_mentions"] == {"parse": []}  # pings can never fire
    assert backend.validate(PostPackage(text="x" * 2001)) != []
