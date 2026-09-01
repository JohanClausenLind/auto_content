"""Token vault (22.3, 25): authenticated envelope encryption for provider tokens.

AES-256-GCM with a key ring: `VAULT_MASTER_KEY` holds the current key (base64, 32 bytes) and
`VAULT_MASTER_KEYS_OLD` an optional comma-separated list of previous keys for rotation. Token
plaintext exists only inside this module's callers on the server; models, renderers, the browser,
and logs never see it. Every ciphertext binds its context (workspace, platform, purpose) as AAD,
so a token copied to another row fails to decrypt."""

from __future__ import annotations

import base64
import os
import secrets
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class VaultError(Exception):
    pass


class VaultKeyMissingError(VaultError):
    pass


@dataclass(frozen=True)
class Sealed:
    key_id: str
    nonce_b64: str
    ciphertext_b64: str


def _decode_key(b64: str) -> bytes:
    try:
        key = base64.b64decode(b64)
    except Exception as exc:
        raise VaultError("master key is not valid base64") from exc
    if len(key) != 32:
        raise VaultError("master key must be 32 bytes")
    return key


def _key_id(key: bytes) -> str:
    import hashlib

    return hashlib.sha256(key).hexdigest()[:12]


class TokenVault:
    def __init__(
        self,
        *,
        master_key_env: str = "VAULT_MASTER_KEY",
        old_keys_env: str = "VAULT_MASTER_KEYS_OLD",
    ) -> None:
        current = os.environ.get(master_key_env)
        if not current:
            raise VaultKeyMissingError(
                f"{master_key_env} is not set — run setup and back the key up"
            )
        self._current = _decode_key(current)
        self._ring: dict[str, bytes] = {_key_id(self._current): self._current}
        for old in filter(None, (os.environ.get(old_keys_env) or "").split(",")):
            key = _decode_key(old.strip())
            self._ring[_key_id(key)] = key

    @property
    def current_key_id(self) -> str:
        return _key_id(self._current)

    def seal(self, plaintext: str, *, aad: str) -> Sealed:
        nonce = secrets.token_bytes(12)
        ct = AESGCM(self._current).encrypt(nonce, plaintext.encode(), aad.encode())
        return Sealed(
            self.current_key_id, base64.b64encode(nonce).decode(), base64.b64encode(ct).decode()
        )

    def open(self, sealed: Sealed, *, aad: str) -> str:
        key = self._ring.get(sealed.key_id)
        if key is None:
            raise VaultError(
                f"no key {sealed.key_id!r} in the ring (rotated away without re-sealing?)"
            )
        try:
            pt = AESGCM(key).decrypt(
                base64.b64decode(sealed.nonce_b64),
                base64.b64decode(sealed.ciphertext_b64),
                aad.encode(),
            )
        except InvalidTag as exc:
            raise VaultError(
                "decryption failed: wrong key, tampered ciphertext, or mismatched context"
            ) from exc
        return pt.decode()

    def reseal(self, sealed: Sealed, *, aad: str) -> Sealed:
        """Rotate one ciphertext onto the current key."""
        return self.seal(self.open(sealed, aad=aad), aad=aad)
