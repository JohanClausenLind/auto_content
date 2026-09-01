"""External request portal (phase 12): branded submission of briefs without editor access.
A signed, expiring portal token authorizes ONLY brief submission into one workspace; a valid
submission creates a ProjectBrief + ActionItem, never a run and never any editor capability."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


class PortalError(Exception):
    pass


def make_portal_token(
    *, workspace_id: str, secret: str, ttl_seconds: int = 30 * 86400, now: float | None = None
) -> str:
    payload = {
        "ws": workspace_id,
        "exp": int((now if now is not None else time.time()) + ttl_seconds),
        "scope": "brief_submit",
    }
    body = (
        base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).rstrip(b"=").decode()
    )
    sig = (
        base64.urlsafe_b64encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        .rstrip(b"=")
        .decode()
    )
    return f"{body}.{sig}"


def verify_portal_token(token: str, *, secret: str, now: float | None = None) -> str:
    """Returns the workspace id or raises. The token grants brief submission and nothing else."""
    try:
        body, sig = token.split(".", 1)
    except ValueError as exc:
        raise PortalError("malformed portal token") from exc
    expected = (
        base64.urlsafe_b64encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
        .rstrip(b"=")
        .decode()
    )
    if not hmac.compare_digest(expected, sig):
        raise PortalError("portal token signature invalid")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if payload.get("scope") != "brief_submit":
        raise PortalError("portal token has the wrong scope")
    if (now if now is not None else time.time()) > payload["exp"]:
        raise PortalError("portal token expired")
    return payload["ws"]


@dataclass(frozen=True)
class PortalSubmission:
    topic: str
    objective: str
    deadline: str | None
    contact: str


def validate_submission(data: dict) -> PortalSubmission:
    topic = str(data.get("topic", "")).strip()
    objective = str(data.get("objective", "")).strip()
    contact = str(data.get("contact", "")).strip()
    if not (3 <= len(topic) <= 500):
        raise PortalError("topic must be 3-500 characters")
    if not (3 <= len(objective) <= 1000):
        raise PortalError("objective must be 3-1000 characters")
    if not contact:
        raise PortalError("a contact (name or email) is required so the operator can follow up")
    deadline = str(data["deadline"]).strip() if data.get("deadline") else None
    return PortalSubmission(topic=topic, objective=objective, deadline=deadline, contact=contact)
