"""Local auth spike: Argon2id passwords, TOTP with replay protection, and a full passkey
registration + authentication round trip against a software authenticator (test-only)."""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import time

import cbor2
import pyotp
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from content_factory.auth import passkeys, passwords, totp

RP_ID = "localhost"
ORIGIN = "http://localhost:3000"


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class SoftAuthenticator:
    """Minimal FIDO2 software authenticator: ES256, 'none' attestation, resident key."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = hashlib.sha256(b"soft-authenticator-cred").digest()
        self.counter = 0

    def _cose_key(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")}
        )

    def _auth_data(self, flags: int, include_cred: bool) -> bytes:
        rp_hash = hashlib.sha256(RP_ID.encode()).digest()
        data = rp_hash + bytes([flags]) + struct.pack(">I", self.counter)
        if include_cred:
            aaguid = b"\x00" * 16
            data += aaguid + struct.pack(">H", len(self.cred_id)) + self.cred_id + self._cose_key()
        return data

    def register(self, options_json: str) -> dict:
        opts = json.loads(options_json)
        client_data = json.dumps(
            {
                "type": "webauthn.create",
                "challenge": opts["challenge"],
                "origin": ORIGIN,
                "crossOrigin": False,
            }
        ).encode()
        self.counter += 1
        auth_data = self._auth_data(flags=0x45, include_cred=True)  # UP | UV | AT
        att_obj = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(self.cred_id),
            "rawId": b64url(self.cred_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(att_obj),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
        }

    def authenticate(self, options_json: str) -> dict:
        opts = json.loads(options_json)
        client_data = json.dumps(
            {
                "type": "webauthn.get",
                "challenge": opts["challenge"],
                "origin": ORIGIN,
                "crossOrigin": False,
            }
        ).encode()
        self.counter += 1
        auth_data = self._auth_data(flags=0x05, include_cred=False)  # UP | UV
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64url(self.cred_id),
            "rawId": b64url(self.cred_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": None,
            },
            "clientExtensionResults": {},
        }


def test_password_hashing_roundtrip_and_policy() -> None:
    h = passwords.hash_password("correct horse battery staple")
    assert h.startswith("$argon2id$")
    assert passwords.verify_password(h, "correct horse battery staple")
    assert not passwords.verify_password(h, "wrong password entirely")
    assert not passwords.verify_password("not-a-hash", "x")
    try:
        passwords.hash_password("short")
    except ValueError:
        pass
    else:
        raise AssertionError("short passwords must be rejected")


def test_totp_accepts_current_code_once_and_refuses_replay() -> None:
    secret = totp.new_totp_secret()
    assert "otpauth://totp/" in totp.provisioning_uri(secret, "operator")
    code = pyotp.TOTP(secret).now()
    counter = totp.verify_totp(secret, code)
    assert counter == int(time.time()) // 30
    assert totp.verify_totp(secret, code, last_used_counter=counter) is None  # replay refused
    assert totp.verify_totp(secret, "000000" if code != "000000" else "111111") is None


def test_recovery_codes_hash_normalized() -> None:
    codes = totp.new_recovery_codes(3)
    assert len(set(codes)) == 3
    assert totp.hash_recovery_code(codes[0].upper() + " ") == totp.hash_recovery_code(codes[0])


def test_passkey_registration_and_authentication_roundtrip() -> None:
    authenticator = SoftAuthenticator()
    reg = passkeys.begin_registration(
        rp_id=RP_ID,
        rp_name="Content Factory",
        user_id=b"user-0001",
        user_name="operator",
        display_name="Operator",
        existing=[],
    )
    stored = passkeys.finish_registration(
        credential_json=authenticator.register(reg.options_json),
        challenge=reg.challenge,
        rp_id=RP_ID,
        origin=ORIGIN,
    )
    assert stored.credential_id == authenticator.cred_id
    assert stored.sign_count == 1

    auth = passkeys.begin_authentication(rp_id=RP_ID, allowed=[stored])
    updated = passkeys.finish_authentication(
        credential_json=authenticator.authenticate(auth.options_json),
        challenge=auth.challenge,
        rp_id=RP_ID,
        origin=ORIGIN,
        passkey=stored,
    )
    assert updated.sign_count == 2

    # A replayed assertion (stale challenge) must be rejected.
    import pytest
    from webauthn.helpers.exceptions import InvalidAuthenticationResponse

    stale = passkeys.begin_authentication(rp_id=RP_ID, allowed=[updated])
    assertion = authenticator.authenticate(stale.options_json)
    fresh = passkeys.begin_authentication(rp_id=RP_ID, allowed=[updated])
    with pytest.raises(InvalidAuthenticationResponse):
        passkeys.finish_authentication(
            credential_json=assertion,
            challenge=fresh.challenge,
            rp_id=RP_ID,
            origin=ORIGIN,
            passkey=updated,
        )

    # Wrong origin is rejected too.
    ok = passkeys.begin_authentication(rp_id=RP_ID, allowed=[updated])
    with pytest.raises(InvalidAuthenticationResponse):
        passkeys.finish_authentication(
            credential_json=authenticator.authenticate(ok.options_json),
            challenge=ok.challenge,
            rp_id=RP_ID,
            origin="https://evil.example",
            passkey=updated,
        )
