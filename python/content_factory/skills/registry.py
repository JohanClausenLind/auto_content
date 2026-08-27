"""SkillRegistry (18.1): load, verify (Ed25519), and select skills. No marketplace.

Manifests live at ``skills/<domain>/<name>/manifest.json``. Every manifest must be signed by a key
in the operator's trust store; unsigned or tampered manifests are refused. Lifecycle governs
selection: only ``active``/``canary`` skills are selectable for production.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from content_factory.schemas.base import canonical_dumps
from content_factory.schemas.skills import Lifecycle, SkillManifest


class SkillRegistryError(Exception):
    pass


class SignatureError(SkillRegistryError):
    pass


def _signing_payload(manifest: SkillManifest) -> bytes:
    data = manifest.model_dump(mode="json")
    data.pop("signature", None)
    return canonical_dumps(data).encode("utf-8")


def sign_manifest(manifest: SkillManifest, key: Ed25519PrivateKey) -> SkillManifest:
    sig = key.sign(_signing_payload(manifest))
    return manifest.model_copy(update={"signature": base64.b64encode(sig).decode()})


def verify_manifest(manifest: SkillManifest, trusted: Iterable[Ed25519PublicKey]) -> None:
    if not manifest.signature:
        raise SignatureError(f"{manifest.skill_id}@{manifest.version} is unsigned")
    sig = base64.b64decode(manifest.signature)
    payload = _signing_payload(manifest)
    for key in trusted:
        try:
            key.verify(sig, payload)
            return
        except InvalidSignature:
            continue
    raise SignatureError(f"{manifest.skill_id}@{manifest.version}: signature not trusted")


@dataclass
class SkillRegistry:
    trusted_keys: tuple[Ed25519PublicKey, ...]
    _skills: dict[tuple[str, str], SkillManifest] = field(default_factory=dict)
    rejected: list[tuple[Path, str]] = field(default_factory=list)

    def load_dir(self, root: Path) -> int:
        loaded = 0
        for path in sorted(root.rglob("manifest.json")):
            try:
                manifest = SkillManifest.model_validate(json.loads(path.read_text("utf-8")))
                verify_manifest(manifest, self.trusted_keys)
            except (ValueError, SignatureError) as exc:
                self.rejected.append((path, str(exc)))
                continue
            self.register(manifest)
            loaded += 1
        return loaded

    def register(self, manifest: SkillManifest) -> None:
        verify_manifest(manifest, self.trusted_keys)
        key = (manifest.skill_id, manifest.version)
        if key in self._skills:
            raise SkillRegistryError(f"duplicate skill {manifest.skill_id}@{manifest.version}")
        self._skills[key] = manifest

    def get(self, skill_id: str, version: str) -> SkillManifest:
        try:
            return self._skills[(skill_id, version)]
        except KeyError as exc:
            raise SkillRegistryError(f"unknown skill {skill_id}@{version}") from exc

    def selectable(self, skill_id: str) -> list[SkillManifest]:
        """Versions eligible for production: active first, then canary; never draft/deprecated/…"""
        rank = {Lifecycle.active: 0, Lifecycle.canary: 1}
        out = [m for (sid, _), m in self._skills.items() if sid == skill_id and m.status in rank]
        return sorted(out, key=lambda m: (rank[m.status], m.version), reverse=False)

    def all(self) -> list[SkillManifest]:
        return sorted(self._skills.values(), key=lambda m: (m.skill_id, m.version))
