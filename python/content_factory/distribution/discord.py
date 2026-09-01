"""Discord webhook adapter (Tier 1): execute-webhook with mention safety always on."""

from __future__ import annotations

import httpx

from content_factory.distribution.backend import (
    AmbiguousPublishError,
    Capabilities,
    DistributionBackend,
    PostPackage,
    PublishReceipt,
    TransientPublishError,
)


class DiscordWebhookBackend(DistributionBackend):
    platform = "discord"

    def __init__(self, *, webhook_url_getter, transport: httpx.BaseTransport | None = None) -> None:
        self._get_url = webhook_url_getter
        self._http = httpx.Client(timeout=30, transport=transport)

    def capabilities(self) -> Capabilities:
        return Capabilities(
            platform=self.platform,
            max_text_chars=2000,
            max_images=10,
            supports_alt_text=True,
            supports_idempotency_key=False,
            supported_visibilities=("public",),
            max_image_bytes=10 * 1024 * 1024,
        )

    def validate(self, package: PostPackage) -> list[str]:
        problems = []
        if len(package.text) > 2000:
            problems.append(f"message is {len(package.text)} characters; Discord allows 2000")
        if len(package.media) > 10:
            problems.append("Discord webhooks allow at most 10 attachments")
        return problems

    def publish(self, package: PostPackage) -> PublishReceipt:
        url = self._get_url()
        data = {
            "content": package.text,
            # Mention safety (22 tests): a webhook post must never ping anyone.
            "allowed_mentions": {"parse": []},
        }
        files = {
            f"files[{i}]": (f"image{i}.png", m.data, m.mime) for i, m in enumerate(package.media)
        }
        import json as _json

        try:
            if files:
                resp = self._http.post(
                    url,
                    params={"wait": "true"},
                    data={"payload_json": _json.dumps(data)},
                    files=files,
                )
            else:
                resp = self._http.post(url, params={"wait": "true"}, json=data)
        except httpx.TimeoutException as exc:
            raise AmbiguousPublishError("timeout after webhook execute") from exc
        except httpx.TransportError as exc:
            raise TransientPublishError(str(exc)) from exc
        if resp.status_code in {429, 500, 502, 503}:
            raise TransientPublishError(f"webhook -> {resp.status_code}")
        resp.raise_for_status()
        body = resp.json()
        return PublishReceipt(
            remote_id=str(body["id"]), url=None, raw={"channel_id": body.get("channel_id")}
        )

    def find_existing(self, package: PostPackage) -> PublishReceipt | None:
        return None  # webhooks cannot read the channel; ambiguity stays a blocking state
