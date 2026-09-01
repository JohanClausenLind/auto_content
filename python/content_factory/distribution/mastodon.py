"""Mastodon adapter (Tier 1): native Idempotency-Key on POST /api/v1/statuses; media via
/api/v2/media with async processing polling; instance limits read from /api/v2/instance."""

from __future__ import annotations

import time
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


class MastodonBackend(DistributionBackend):
    platform = "mastodon"

    def __init__(
        self, *, base_url: str, token_getter, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=30, transport=transport)
        self._get_token = token_getter
        self._caps: Capabilities | None = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_token()}"}

    def capabilities(self) -> Capabilities:
        if self._caps:
            return self._caps
        max_chars, max_media, max_bytes = 500, 4, 16 * 1024 * 1024
        try:
            resp = self._http.get("/api/v2/instance")
            if resp.status_code < 400:
                cfg = resp.json().get("configuration", {})
                max_chars = int(cfg.get("statuses", {}).get("max_characters", max_chars))
                max_media = int(cfg.get("statuses", {}).get("max_media_attachments", max_media))
                max_bytes = int(cfg.get("media_attachments", {}).get("image_size_limit", max_bytes))
        except httpx.TransportError:
            pass  # fall back to defaults; validation stays conservative
        self._caps = Capabilities(
            platform=self.platform,
            max_text_chars=max_chars,
            max_images=max_media,
            supports_alt_text=True,
            supports_idempotency_key=True,
            supported_visibilities=("public", "unlisted", "private", "direct"),
            max_image_bytes=max_bytes,
        )
        return self._caps

    def validate(self, package: PostPackage) -> list[str]:
        caps = self.capabilities()
        problems: list[str] = []
        if len(package.text) > caps.max_text_chars:
            problems.append(
                f"text is {len(package.text)} characters; this instance allows {caps.max_text_chars}"  # noqa: E501
            )
        if len(package.media) > caps.max_images:
            problems.append(
                f"{len(package.media)} attachments; this instance allows {caps.max_images}"
            )
        for m in package.media:
            if not m.alt_text.strip():
                problems.append("every attachment needs a description (alt text)")
            if len(m.data) > caps.max_image_bytes:
                problems.append("an attachment exceeds the instance size limit")
        if package.visibility not in caps.supported_visibilities:
            problems.append(
                f"visibility {package.visibility!r} is not one of {caps.supported_visibilities}"
            )
        return problems

    def _upload_media(self, m) -> str:
        resp = self._http.post(
            "/api/v2/media",
            files={"file": ("upload", m.data, m.mime)},
            data={"description": m.alt_text},
            headers=self._headers(),
        )
        if resp.status_code in {429, 500, 502, 503}:
            raise TransientPublishError(f"media upload -> {resp.status_code}")
        resp.raise_for_status()
        media_id = resp.json()["id"]
        if resp.status_code == 202:  # async processing: poll until ready
            for _ in range(30):
                poll = self._http.get(f"/api/v1/media/{media_id}", headers=self._headers())
                if poll.status_code == 200:
                    return media_id
                time.sleep(0.2)
            raise TransientPublishError("media never finished processing")
        return media_id

    def publish(self, package: PostPackage) -> PublishReceipt:
        media_ids = [self._upload_media(m) for m in package.media]
        payload: dict[str, Any] = {
            "status": package.text,
            "visibility": package.visibility,
            "language": package.language,
        }
        if media_ids:
            payload["media_ids"] = media_ids
        headers = {**self._headers()}
        if package.idempotency_token:
            headers["Idempotency-Key"] = package.idempotency_token
        try:
            resp = self._http.post("/api/v1/statuses", json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise AmbiguousPublishError("timeout after submitting the status") from exc
        except httpx.TransportError as exc:
            raise TransientPublishError(str(exc)) from exc
        if resp.status_code in {429, 500, 502, 503, 504}:
            raise TransientPublishError(f"statuses -> {resp.status_code}")
        resp.raise_for_status()
        data = resp.json()
        return PublishReceipt(remote_id=str(data["id"]), url=data.get("url"), raw=data)

    def find_existing(self, package: PostPackage) -> PublishReceipt | None:
        resp = self._http.get("/api/v1/accounts/verify_credentials", headers=self._headers())
        if resp.status_code >= 400:
            return None
        account_id = resp.json()["id"]
        statuses = self._http.get(
            f"/api/v1/accounts/{account_id}/statuses",
            params={"limit": 40, "exclude_reblogs": True},
            headers=self._headers(),
        )
        if statuses.status_code >= 400:
            return None
        import re

        wanted = package.text.strip()
        for st in statuses.json():
            plain = (
                re.sub(r"<[^>]+>", "", st.get("content", ""))
                .replace("&amp;", "&")
                .replace("&#39;", "'")
                .strip()
            )
            if plain == wanted:
                return PublishReceipt(remote_id=str(st["id"]), url=st.get("url"), raw=st)
        return None
