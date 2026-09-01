"""Article/newsletter Tier-1 adapters (drafts only) against adversarial mocks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import httpx
import pytest

from content_factory.distribution.articles import (
    ArticlePackage,
    ArticlePublishError,
    GhostBackend,
    ListmonkBackend,
    NewsletterPackage,
    WordPressBackend,
)

ARTICLE = ArticlePackage(
    title="How wind reached a fifth of Sweden's electricity",
    html="<p>In 2025, wind supplied about 21%…</p>",
    excerpt="The share doubled since 2018.",
    slug="wind-share-2025",
    meta_description="Wind supplied about 21% of Sweden's electricity in 2025.",
    tags=("energy", "sweden"),
    sources_html="<h3>Sources</h3><ul><li><a href='https://example.se/wind-2025'>Energimyndigheten</a></li></ul>",
)


def test_wordpress_draft_carries_sources_and_finds_existing() -> None:
    posts: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers["Authorization"]
        assert auth.startswith("Basic ") and base64.b64decode(auth[6:]).decode().startswith("op:")
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["status"] == "draft"
            assert "Sources" in body["content"]  # references never dropped
            posts.append(body)
            return httpx.Response(
                201,
                json={
                    "id": 42,
                    "status": "draft",
                    "link": "https://blog.example/?p=42",
                    "type": "post",
                },
            )
        return httpx.Response(
            200,
            json=[
                {
                    "id": 42,
                    "status": "draft",
                    "title": {"rendered": ARTICLE.title},
                    "link": "https://blog.example/?p=42",
                }
            ],
        )

    wp = WordPressBackend(
        base_url="https://blog.example",
        username="op",
        app_password_getter=lambda: "abcd efgh",
        transport=httpx.MockTransport(handler),
    )
    receipt = wp.create_draft(ARTICLE)
    assert receipt.remote_id == "42" and receipt.status == "draft"
    existing = wp.find_existing(ARTICLE)
    assert existing is not None and existing.remote_id == "42"


def test_ghost_jwt_shape_and_draft_creation() -> None:
    secret_hex = "aa" * 32

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers["Authorization"]
        assert auth.startswith("Ghost ")
        token = auth.split(" ", 1)[1]
        head_b64, payload_b64, sig_b64 = token.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        header = json.loads(base64.urlsafe_b64decode(pad(head_b64)))
        payload = json.loads(base64.urlsafe_b64decode(pad(payload_b64)))
        assert header == {"alg": "HS256", "typ": "JWT", "kid": "keyid1"}
        assert payload["aud"] == "/admin/" and payload["exp"] - payload["iat"] == 300
        expected = hmac.new(
            bytes.fromhex(secret_hex), f"{head_b64}.{payload_b64}".encode(), hashlib.sha256
        ).digest()
        assert base64.urlsafe_b64decode(pad(sig_b64)) == expected  # signature verifies
        assert request.url.params["source"] == "html"
        post = json.loads(request.content)["posts"][0]
        assert post["status"] == "draft" and post["canonical_url"] is None
        return httpx.Response(
            201,
            json={
                "posts": [
                    {
                        "id": "ghost1",
                        "uuid": "u-1",
                        "status": "draft",
                        "url": "https://ghost.example/p/x",
                    }
                ]
            },
        )

    ghost = GhostBackend(
        base_url="https://ghost.example",
        admin_key_getter=lambda: f"keyid1:{secret_hex}",
        transport=httpx.MockTransport(handler),
    )
    receipt = ghost.create_draft(ARTICLE)
    assert receipt.remote_id == "ghost1" and receipt.status == "draft"


def test_listmonk_draft_requires_unsubscribe_and_plaintext() -> None:
    created: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        created.append(json.loads(request.content))
        return httpx.Response(200, json={"data": {"id": 7, "status": "draft"}})

    lm = ListmonkBackend(
        base_url="https://mail.example",
        api_user="api",
        token_getter=lambda: "tok",
        transport=httpx.MockTransport(handler),
    )
    good = NewsletterPackage(
        subject="Wind digest",
        preheader="21% and rising",
        html="<p>hi</p><p>{{ UnsubscribeURL }}</p>",
        plain_text="hi\nunsubscribe: link",
        list_ids=(3,),
        unsubscribe_present=False,
    )
    receipt = lm.create_draft_campaign(good)
    assert receipt.remote_id == "7" and created[0]["altbody"].startswith("hi")
    bad = NewsletterPackage(
        subject="Wind digest",
        preheader="",
        html="<p>no unsub</p>",
        plain_text="x",
        list_ids=(3,),
        unsubscribe_present=False,
    )
    with pytest.raises(ArticlePublishError, match="unsubscribe"):
        lm.create_draft_campaign(bad)
    no_alt = NewsletterPackage(
        subject="s",
        preheader="",
        html="<p>{{ UnsubscribeURL }}</p>",
        plain_text=" ",
        list_ids=(3,),
        unsubscribe_present=False,
    )
    with pytest.raises(ArticlePublishError, match="plain-text"):
        lm.create_draft_campaign(no_alt)
    assert len(created) == 1  # invalid packages never reach the API
