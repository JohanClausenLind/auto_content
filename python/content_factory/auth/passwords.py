"""Password hashing (Argon2id) and constant-time verification."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# OWASP-recommended Argon2id parameters (2024 guidance: m=19 MiB, t=2, p=1) with extra memory.
_hasher = PasswordHasher(
    time_cost=3, memory_cost=64 * 1024, parallelism=2, hash_len=32, salt_len=16
)


def hash_password(password: str, min_length: int | None = None) -> str:
    """Hash a password, enforcing the configured minimum length (settings.auth.password_min_length,
    default 12; the settings schema floors it at 4)."""
    if min_length is None:
        from content_factory.config import get_settings

        min_length = get_settings().auth.password_min_length
    if len(password) < min_length:
        msg = f"password must be at least {min_length} characters"
        raise ValueError(msg)
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)
