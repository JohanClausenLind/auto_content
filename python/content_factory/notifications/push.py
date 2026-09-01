"""Web Push for ActionItems (2.16, 20.5): VAPID-signed notifications that inform and deep-link.
Push never approves anything, and push failure never erases the ActionItem."""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from content_factory.logging import get_logger

log = get_logger(__name__)


class PushError(Exception):
    pass


def generate_vapid_keys() -> dict[str, str]:
    """One ES256 keypair for the deployment; stored in env (VAPID_KEYS), never in the repo.
    The private key uses py_vapid's raw base64url form (what pywebpush consumes directly)."""
    key = ec.generate_private_key(ec.SECP256R1())
    raw_private = key.private_numbers().private_value.to_bytes(32, "big")
    private_b64 = base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    public_b64 = base64.urlsafe_b64encode(pub).rstrip(b"=").decode()
    return {"public": public_b64, "private": private_b64}


def load_vapid_keys(env_name: str = "VAPID_KEYS") -> dict[str, str] | None:
    raw = os.environ.get(env_name)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if {"public", "private"} <= set(data):
            return data
    except ValueError:
        pass
    return None


@dataclass(frozen=True)
class PushResult:
    endpoint: str
    ok: bool
    status: int | None
    gone: bool  # 404/410: the subscription is dead and should be removed


def send_web_push(
    subscription: dict,
    payload: dict,
    *,
    vapid: dict[str, str],
    subscriber: str = "mailto:operator@localhost",
    timeout: int = 10,
    requests_session=None,
) -> PushResult:
    from pywebpush import WebPushException, webpush

    try:
        resp: Any = webpush(
            subscription_info=subscription,
            data=json.dumps(payload),
            vapid_private_key=vapid["private"],
            vapid_claims={"sub": subscriber},
            timeout=timeout,
            requests_session=requests_session,
        )
        return PushResult(
            subscription.get("endpoint", ""), resp.status_code < 300, resp.status_code, False
        )
    except WebPushException as exc:
        response = getattr(exc, "response", None)
        status = (
            response.status_code
            if response is not None and hasattr(response, "status_code")
            else None
        )
        gone = status in {404, 410}
        log.warning("push.failed", endpoint=subscription.get("endpoint", "")[:60], status=status)
        return PushResult(subscription.get("endpoint", ""), False, status, gone)


def action_item_payload(item: dict, public_base_url: str) -> dict:
    return {
        "title": item["title"],
        "body": item.get("body", ""),
        "kind": item.get("kind", ""),
        "severity": item.get("severity", "normal"),
        # Deep link only: the notification never carries an approval action.
        "url": f"{public_base_url.rstrip('/')}{item.get('deep_link') or '/'}",
        "action_item_id": item["id"],
    }
