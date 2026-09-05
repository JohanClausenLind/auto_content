"""Storage roles mapped to stable volume identities — never to fragile device-order paths.

The operator picks which volumes and folders each role may use and how much space it may
consume. Roles may share a volume, but shared free space is accounted once (the accountant
reserves per volume, not per role), and a backup role must not share the archive's volume:
partitions on one device are one failure domain, not independent copies.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from content_factory.schemas.base import OpaqueId, SchemaModel, VersionedModel


class StorageRole(StrEnum):
    scratch = "scratch"  # active frames, temp tensors; disposable after confirmed handoff
    model_cache = "model_cache"  # verified weights; pinned packs plus economic eviction
    durable_archive = "durable_archive"  # originals, approved assets, masters
    artifact_exchange = "artifact_exchange"  # inputs/outputs exchanged between nodes
    cloud_cache = "cloud_cache"  # regional cache; kept only while it beats re-staging cost
    backup = "backup"  # independent failure domain, versioned retention


class RoleAssignment(SchemaModel):
    role: StorageRole
    volume_id: str = Field(min_length=1, max_length=128)  # stable identity (fs UUID / device)
    root_path: str = Field(min_length=1)  # operator-selected folder on that volume
    quota_bytes: int | None = Field(default=None, ge=1)
    min_free_reserve_bytes: int = Field(default=0, ge=0)
    allow_synced_replicas: bool = False


class StoragePlan(VersionedModel):
    node_id: OpaqueId
    assignments: tuple[RoleAssignment, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _roles_and_failure_domains(self) -> StoragePlan:
        roles = [a.role for a in self.assignments]
        if len(roles) != len(set(roles)):
            msg = "each storage role gets exactly one assignment"
            raise ValueError(msg)
        by_role = {a.role: a for a in self.assignments}
        backup = by_role.get(StorageRole.backup)
        archive = by_role.get(StorageRole.durable_archive)
        if backup and archive and backup.volume_id == archive.volume_id:
            msg = (
                "backup and durable_archive share one volume — a partition on the same device "
                "is not an independent backup copy"
            )
            raise ValueError(msg)
        return self

    def assignment(self, role: StorageRole) -> RoleAssignment | None:
        return next((a for a in self.assignments if a.role == role), None)
