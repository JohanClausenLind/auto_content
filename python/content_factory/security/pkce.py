"""RFC 7636 PKCE (S256): one derivation for every authorization-code flow."""

from __future__ import annotations

import base64
import hashlib
import secrets


def pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for ``code_challenge_method=S256``."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge
