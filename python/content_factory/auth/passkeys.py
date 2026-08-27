"""WebAuthn / passkeys via py_webauthn (registration + authentication ceremonies).

The server keeps: credential id, COSE public key, sign count, transports. Challenges are single-use
and bound to the pending ceremony; callers store them server-side (session/DB), never in cookies.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)


@dataclass(frozen=True)
class StoredPasskey:
    credential_id: bytes
    public_key: bytes
    sign_count: int
    transports: tuple[str, ...] = ()
    label: str = "passkey"


@dataclass(frozen=True)
class RegistrationChallenge:
    challenge: bytes
    options_json: str


@dataclass(frozen=True)
class AuthenticationChallenge:
    challenge: bytes
    options_json: str


def begin_registration(
    *,
    rp_id: str,
    rp_name: str,
    user_id: bytes,
    user_name: str,
    display_name: str,
    existing: list[StoredPasskey],
) -> RegistrationChallenge:
    challenge = secrets.token_bytes(32)
    opts = generate_registration_options(
        rp_id=rp_id,
        rp_name=rp_name,
        user_id=user_id,
        user_name=user_name,
        user_display_name=display_name,
        challenge=challenge,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=p.credential_id) for p in existing],
    )
    return RegistrationChallenge(challenge=challenge, options_json=options_to_json(opts))


def finish_registration(
    *,
    credential_json: str | dict[str, Any],
    challenge: bytes,
    rp_id: str,
    origin: str,
    label: str = "passkey",
) -> StoredPasskey:
    verified = verify_registration_response(
        credential=credential_json,
        expected_challenge=challenge,
        expected_rp_id=rp_id,
        expected_origin=origin,
        require_user_verification=False,
    )
    return StoredPasskey(
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        label=label,
    )


def begin_authentication(*, rp_id: str, allowed: list[StoredPasskey]) -> AuthenticationChallenge:
    challenge = secrets.token_bytes(32)
    opts = generate_authentication_options(
        rp_id=rp_id,
        challenge=challenge,
        allow_credentials=[PublicKeyCredentialDescriptor(id=p.credential_id) for p in allowed]
        or None,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return AuthenticationChallenge(challenge=challenge, options_json=options_to_json(opts))


def finish_authentication(
    *,
    credential_json: str | dict[str, Any],
    challenge: bytes,
    rp_id: str,
    origin: str,
    passkey: StoredPasskey,
) -> StoredPasskey:
    verified = verify_authentication_response(
        credential=credential_json,
        expected_challenge=challenge,
        expected_rp_id=rp_id,
        expected_origin=origin,
        credential_public_key=passkey.public_key,
        credential_current_sign_count=passkey.sign_count,
        require_user_verification=False,
    )
    return StoredPasskey(
        credential_id=passkey.credential_id,
        public_key=passkey.public_key,
        sign_count=verified.new_sign_count,
        transports=passkey.transports,
        label=passkey.label,
    )
