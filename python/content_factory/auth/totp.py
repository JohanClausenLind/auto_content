"""RFC 6238 TOTP (Google Authenticator compatible) + single-use recovery codes."""

from __future__ import annotations

import hashlib
import hmac
import secrets

import pyotp

ISSUER = "Content Factory"


def new_totp_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account_name: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=ISSUER)


def verify_totp(secret: str, code: str, *, last_used_counter: int | None = None) -> int | None:
    """Return the accepted time-step counter, or None. Callers must persist the counter to refuse
    replays of the same code (``last_used_counter``)."""
    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=1):
        return None
    import time

    counter = int(time.time()) // 30
    for candidate in (counter, counter - 1, counter + 1):
        if hmac.compare_digest(totp.generate_otp(candidate), code):
            if last_used_counter is not None and candidate <= last_used_counter:
                return None
            return candidate
    return None


def new_recovery_codes(n: int = 10) -> list[str]:
    return [f"{secrets.token_hex(4)}-{secrets.token_hex(4)}" for _ in range(n)]


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.strip().lower().encode()).hexdigest()
