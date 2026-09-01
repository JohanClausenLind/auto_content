"""Article & newsletter destination adapters (Tier 1, 21): WordPress (REST + Application
Passwords), Ghost (Admin API, HS256 JWT, source=html), Listmonk (draft campaigns). DRAFTS by
default everywhere; publishing/sending is gated exactly like social publishing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from content_factory.distribution.backend import TransientPublishError


class ArticlePublishError(Exception):
    pass


@dataclass(frozen=True)
class ArticlePackage:
    title: str
    html: str
    excerpt: str = ""
    slug: str | None = None
    canonical_url: str | None = None
    meta_description: str = ""
    tags: tuple[str, ...] = ()
    sources_html: str = ""  # rendered references section — never dropped


@dataclass(frozen=True)
class ArticleReceipt:
    remote_id: str
    status: str
    edit_url: str | None
    raw: dict[str, Any] = field(default_factory=dict)


def _raise_for(resp: httpx.Response, what: str) -> None:
    if resp.status_code in {429, 500, 502, 503, 504}:
        raise TransientPublishError(f"{what} -> {resp.status_code}")
    if resp.status_code >= 400:
        raise ArticlePublishError(f"{what} -> {resp.status_code}: {resp.text[:300]}")


class WordPressBackend:
    """Self-hosted WordPress core REST: Basic auth with an Application Password. Draft-only here;
    `status=publish`/`future` is a distribution-profile decision made elsewhere."""

    platform = "wordpress"

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        app_password_getter,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._username = username
        self._get_password = app_password_getter
        self._http = httpx.Client(timeout=30, transport=transport)

    def _auth(self) -> dict[str, str]:
        raw = f"{self._username}:{self._get_password()}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}

    def create_draft(self, package: ArticlePackage) -> ArticleReceipt:
        body: dict[str, Any] = {
            "title": package.title,
            "content": package.html
            + (f"\n<hr/>\n{package.sources_html}" if package.sources_html else ""),
            "status": "draft",
            "excerpt": package.excerpt,
        }
        if package.slug:
            body["slug"] = package.slug
        resp = self._http.post(f"{self._base}/wp-json/wp/v2/posts", json=body, headers=self._auth())
        _raise_for(resp, "wp posts")
        data = resp.json()
        return ArticleReceipt(
            remote_id=str(data["id"]),
            status=data.get("status", "draft"),
            edit_url=data.get("link"),
            raw={"type": data.get("type")},
        )

    def find_existing(self, package: ArticlePackage) -> ArticleReceipt | None:
        resp = self._http.get(
            f"{self._base}/wp-json/wp/v2/posts",
            params={"status": "draft", "search": package.title, "per_page": 20},
            headers=self._auth(),
        )
        if resp.status_code >= 400:
            return None
        for post in resp.json():
            if (
                post.get("title", {}).get("raw") == package.title
                or post.get("title", {}).get("rendered", "").strip() == package.title
            ):
                return ArticleReceipt(
                    remote_id=str(post["id"]),
                    status=post.get("status", "draft"),
                    edit_url=post.get("link"),
                )
        return None


class GhostBackend:
    """Ghost Admin API: HS256 JWT from the Admin key (`id:secret`), 5-minute expiry,
    `aud: /admin/`; posts created from HTML via ?source=html. Draft-only here."""

    platform = "ghost"

    def __init__(
        self, *, base_url: str, admin_key_getter, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._base = base_url.rstrip("/")
        self._get_key = admin_key_getter
        self._http = httpx.Client(timeout=30, transport=transport)

    def _jwt(self, *, now: int | None = None) -> str:
        key_id, secret_hex = self._get_key().split(":", 1)
        secret = bytes.fromhex(secret_hex)
        iat = now or int(time.time())
        header = {"alg": "HS256", "typ": "JWT", "kid": key_id}
        payload = {"iat": iat, "exp": iat + 5 * 60, "aud": "/admin/"}

        def b64(obj: dict) -> str:
            return (
                base64.urlsafe_b64encode(json.dumps(obj, separators=(",", ":")).encode())
                .rstrip(b"=")
                .decode()
            )

        signing_input = f"{b64(header)}.{b64(payload)}"
        sig = (
            base64.urlsafe_b64encode(
                hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        return f"{signing_input}.{sig}"

    def create_draft(self, package: ArticlePackage) -> ArticleReceipt:
        post: dict[str, Any] = {
            "title": package.title,
            "html": package.html
            + (f"\n<hr/>\n{package.sources_html}" if package.sources_html else ""),
            "status": "draft",
            "custom_excerpt": package.excerpt[:300],
            "meta_description": package.meta_description[:500] or None,
            "canonical_url": package.canonical_url,
            "tags": [{"name": t} for t in package.tags],
        }
        resp = self._http.post(
            f"{self._base}/ghost/api/admin/posts/?source=html",
            json={"posts": [post]},
            headers={"Authorization": f"Ghost {self._jwt()}", "Accept-Version": "v5.0"},
        )
        _raise_for(resp, "ghost posts")
        data = resp.json()["posts"][0]
        return ArticleReceipt(
            remote_id=str(data["id"]),
            status=data.get("status", "draft"),
            edit_url=data.get("url"),
            raw={"uuid": data.get("uuid")},
        )


@dataclass(frozen=True)
class NewsletterPackage:
    subject: str
    preheader: str
    html: str
    plain_text: str
    list_ids: tuple[int, ...]
    unsubscribe_present: bool

    def validate(self) -> list[str]:
        problems = []
        if not self.subject.strip():
            problems.append("subject is required")
        if not self.plain_text.strip():
            problems.append("a plain-text alternative is required")
        if (
            not self.unsubscribe_present
            and "{{ UnsubscribeURL }}" not in self.html
            and "*|UNSUB|*" not in self.html
        ):
            problems.append("the email must carry an unsubscribe mechanism")
        if not self.list_ids:
            problems.append("at least one recipient list is required")
        return problems


class ListmonkBackend:
    """Self-hosted Listmonk: draft campaigns via POST /api/campaigns (Basic auth api user:token).
    Sending is a separate, gated status change this adapter deliberately does not expose."""

    platform = "listmonk"

    def __init__(
        self,
        *,
        base_url: str,
        api_user: str,
        token_getter,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._user = api_user
        self._get_token = token_getter
        self._http = httpx.Client(timeout=30, transport=transport)

    def _auth(self) -> dict[str, str]:
        raw = f"{self._user}:{self._get_token()}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}

    def create_draft_campaign(self, package: NewsletterPackage) -> ArticleReceipt:
        problems = package.validate()
        if problems:
            raise ArticlePublishError("; ".join(problems))
        body = {
            "name": package.subject[:80],
            "subject": package.subject,
            "lists": list(package.list_ids),
            "type": "regular",
            "content_type": "html",
            "body": package.html,
            "altbody": package.plain_text,
        }
        resp = self._http.post(f"{self._base}/api/campaigns", json=body, headers=self._auth())
        _raise_for(resp, "listmonk campaigns")
        data = resp.json()["data"]
        return ArticleReceipt(
            remote_id=str(data["id"]),
            status=data.get("status", "draft"),
            edit_url=f"{self._base}/admin/campaigns/{data['id']}",
        )
