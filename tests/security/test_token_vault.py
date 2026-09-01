from __future__ import annotations

import base64
import os

import pytest

from content_factory.security.vault import Sealed, TokenVault, VaultError, VaultKeyMissingError


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def test_seal_open_roundtrip_with_context_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_MASTER_KEY", _key())
    vault = TokenVault()
    sealed = vault.seal("oauth-refresh-token-xyz", aad="ws_a|bluesky|refresh")
    assert vault.open(sealed, aad="ws_a|bluesky|refresh") == "oauth-refresh-token-xyz"
    with pytest.raises(VaultError, match=r"mismatched context|tampered|wrong key"):
        vault.open(sealed, aad="ws_b|bluesky|refresh")  # copied to another workspace: refuses
    tampered = Sealed(sealed.key_id, sealed.nonce_b64, sealed.ciphertext_b64[:-4] + "AAAA")
    with pytest.raises(VaultError):
        vault.open(tampered, aad="ws_a|bluesky|refresh")


def test_rotation_old_key_decrypts_new_key_seals(monkeypatch: pytest.MonkeyPatch) -> None:
    old = _key()
    monkeypatch.setenv("VAULT_MASTER_KEY", old)
    sealed_old = TokenVault().seal("tok", aad="a")
    new = _key()
    monkeypatch.setenv("VAULT_MASTER_KEY", new)
    monkeypatch.setenv("VAULT_MASTER_KEYS_OLD", old)
    vault = TokenVault()
    assert vault.open(sealed_old, aad="a") == "tok"
    resealed = vault.reseal(sealed_old, aad="a")
    assert resealed.key_id == vault.current_key_id != sealed_old.key_id
    monkeypatch.delenv("VAULT_MASTER_KEYS_OLD")
    fresh = TokenVault()
    with pytest.raises(VaultError, match="no key"):
        fresh.open(sealed_old, aad="a")
    assert fresh.open(resealed, aad="a") == "tok"


def test_missing_or_bad_master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)
    with pytest.raises(VaultKeyMissingError):
        TokenVault()
    monkeypatch.setenv("VAULT_MASTER_KEY", "too-short")
    with pytest.raises(VaultError):
        TokenVault()
