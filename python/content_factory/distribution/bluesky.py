"""Bluesky adapter (AT Protocol, Tier 1). App-password session; text ≤300 graphemes; ≤4 images
with alt text (blob ≤ ~976 KB); facets omitted in phase 9 (plain text + media). No native
idempotency key: reconciliation lists recent posts and matches the embedded idempotency token in
the record's own `via`-style field is unavailable, so we match on exact text + recency window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from content_factory.distribution.backend import (
    AmbiguousPublishError,
    Capabilities,
    DistributionBackend,
    PostPackage,
    PublishReceipt,
    TransientPublishError,
)

MAX_GRAPHEMES = 300
MAX_BLOB_BYTES = 976 * 1024


def _grapheme_len(text: str) -> int:
    # Close enough for validation without ICU: count characters, treating surrogate pairs once.
    return len(text)


class BlueskyBackend(DistributionBackend):
    platform = "bluesky"

    def __init__(
        self,
        *,
        service: str,
        handle: str,
        app_password_getter,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._service = service.rstrip("/")
        self._handle = handle
        self._get_password = app_password_getter  # called at publish time; never stored here
        self._http = httpx.Client(base_url=self._service, timeout=30, transport=transport)
        self._session: dict[str, Any] | None = None

    def capabilities(self) -> Capabilities:
        return Capabilities(
            platform=self.platform,
            max_text_chars=MAX_GRAPHEMES,
            max_images=4,
            supports_alt_text=True,
            supports_idempotency_key=False,
            supported_visibilities=("public",),
            max_image_bytes=MAX_BLOB_BYTES,
        )

    def validate(self, package: PostPackage) -> list[str]:
        problems: list[str] = []
        caps = self.capabilities()
        if _grapheme_len(package.text) > caps.max_text_chars:
            problems.append(
                f"text is {_grapheme_len(package.text)} characters; Bluesky allows {caps.max_text_chars}"  # noqa: E501
            )
        if len(package.media) > caps.max_images:
            problems.append(f"{len(package.media)} images; Bluesky allows {caps.max_images}")
        for m in package.media:
            if len(m.data) > caps.max_image_bytes:
                problems.append(
                    f"an image is {len(m.data)} bytes; Bluesky blobs are capped at {caps.max_image_bytes}"  # noqa: E501
                )
            if not m.alt_text.strip():
                problems.append("every image needs alt text")
        if package.visibility != "public":
            problems.append("Bluesky posts are public; use another destination for drafts")
        return problems

    # -- session -----------------------------------------------------------------------------
    def _ensure_session(self) -> dict[str, Any]:
        if self._session:
            return self._session
        resp = self._request(
            "POST",
            "/xrpc/com.atproto.server.createSession",
            json={"identifier": self._handle, "password": self._get_password()},
        )
        self._session = resp
        return resp

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._ensure_session()['accessJwt']}"}

    def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        try:
            resp = self._http.request(method, path, **kw)
        except httpx.TimeoutException as exc:
            if method == "POST" and "createRecord" in path:
                raise AmbiguousPublishError(f"timeout after submitting {path}") from exc
            raise TransientPublishError(str(exc)) from exc
        except httpx.TransportError as exc:
            raise TransientPublishError(str(exc)) from exc
        if resp.status_code in {429, 500, 502, 503, 504}:
            raise TransientPublishError(f"{path} -> {resp.status_code}")
        if resp.status_code >= 400:
            raise RuntimeError(f"bluesky {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    # -- publishing --------------------------------------------------------------------------
    def publish(self, package: PostPackage) -> PublishReceipt:
        session = self._ensure_session()
        embed: dict[str, Any] | None = None
        if package.media:
            images = []
            for m in package.media:
                blob = self._request(
                    "POST",
                    "/xrpc/com.atproto.repo.uploadBlob",
                    content=m.data,
                    headers={**self._auth_headers(), "Content-Type": m.mime},
                )
                images.append({"image": blob["blob"], "alt": m.alt_text})
            embed = {"$type": "app.bsky.embed.images", "images": images}
        record: dict[str, Any] = {
            "$type": "app.bsky.feed.post",
            "text": package.text,
            "createdAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "langs": [package.language],
        }
        if embed:
            record["embed"] = embed
        out = self._request(
            "POST",
            "/xrpc/com.atproto.repo.createRecord",
            json={"repo": session["did"], "collection": "app.bsky.feed.post", "record": record},
            headers=self._auth_headers(),
        )
        uri = out["uri"]
        rkey = uri.rsplit("/", 1)[-1]
        return PublishReceipt(
            remote_id=uri, url=f"https://bsky.app/profile/{self._handle}/post/{rkey}", raw=out
        )

    def find_existing(self, package: PostPackage) -> PublishReceipt | None:
        session = self._ensure_session()
        out = self._request(
            "GET",
            "/xrpc/com.atproto.repo.listRecords",
            params={"repo": session["did"], "collection": "app.bsky.feed.post", "limit": 50},
            headers=self._auth_headers(),
        )
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        for rec in out.get("records", []):
            value = rec.get("value", {})
            created = value.get("createdAt", "1970-01-01T00:00:00Z")
            try:
                when = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                continue
            if value.get("text") == package.text and when >= cutoff:
                rkey = rec["uri"].rsplit("/", 1)[-1]
                return PublishReceipt(
                    remote_id=rec["uri"],
                    url=f"https://bsky.app/profile/{self._handle}/post/{rkey}",
                    raw=rec,
                )
        return None
