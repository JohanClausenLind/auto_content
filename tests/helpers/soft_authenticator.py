"""Minimal FIDO2 software authenticator for tests: ES256, 'none' attestation, resident key."""

from __future__ import annotations

import base64
import hashlib
import json
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class SoftAuthenticator:
    def __init__(self, rp_id: str, origin: str, seed: bytes = b"soft-authenticator-cred") -> None:
        self.rp_id = rp_id
        self.origin = origin
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = hashlib.sha256(seed).digest()
        self.counter = 0

    def _cose_key(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")}
        )

    def _auth_data(self, flags: int, include_cred: bool) -> bytes:
        data = (
            hashlib.sha256(self.rp_id.encode()).digest()
            + bytes([flags])
            + struct.pack(">I", self.counter)
        )
        if include_cred:
            data += (
                b"\x00" * 16
                + struct.pack(">H", len(self.cred_id))
                + self.cred_id
                + self._cose_key()
            )
        return data

    def register(self, options: dict) -> dict:
        client_data = json.dumps(
            {
                "type": "webauthn.create",
                "challenge": options["challenge"],
                "origin": self.origin,
                "crossOrigin": False,
            }
        ).encode()
        self.counter += 1
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": self._auth_data(0x45, True)})
        return {
            "id": b64url(self.cred_id),
            "rawId": b64url(self.cred_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(att),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
        }

    def authenticate(self, options: dict) -> dict:
        client_data = json.dumps(
            {
                "type": "webauthn.get",
                "challenge": options["challenge"],
                "origin": self.origin,
                "crossOrigin": False,
            }
        ).encode()
        self.counter += 1
        auth_data = self._auth_data(0x05, False)
        sig = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64url(self.cred_id),
            "rawId": b64url(self.cred_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(sig),
                "userHandle": None,
            },
            "clientExtensionResults": {},
        }
