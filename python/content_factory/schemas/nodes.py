"""Node capability reports: what a machine can actually do, probed and versioned."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from content_factory.schemas.base import OpaqueId, SchemaModel, VersionedModel
from content_factory.schemas.hardware import HardwareInventory

MediaClass = Literal["hdd", "ssd", "nvme", "removable", "network", "unknown"]


class VolumeInfo(SchemaModel):
    """One mounted volume, identified stably (filesystem UUID or device identity)."""

    volume_id: str = Field(min_length=1, max_length=128)  # fs UUID, or "dev:<st_dev>" fallback
    mount_path: str = Field(min_length=1)
    filesystem: str = Field(default="unknown", max_length=40)
    media_class: MediaClass = "unknown"
    total_bytes: int = Field(ge=0)
    free_bytes: int = Field(ge=0)
    writable: bool
    label: str = Field(default="", max_length=120)


class EncoderCapability(SchemaModel):
    name: str = Field(min_length=1, max_length=60)  # h264_nvenc, hevc_nvenc, av1_nvenc, libx264…
    kind: Literal["encode", "decode"] = "encode"
    status: Literal["verified", "listed", "failed", "unsupported"]
    detail: str = Field(default="", max_length=300)


class ProbeResult(SchemaModel):
    name: str = Field(min_length=1, max_length=80)
    status: Literal["ok", "failed", "unsupported", "unknown"]
    detail: str = Field(default="", max_length=500)


class NodeCapabilityReport(VersionedModel):
    """Versioned probe of one enrolled machine."""

    node_id: OpaqueId
    hostname: str = Field(min_length=1, max_length=200)
    discovered_at: str = Field(min_length=4)  # ISO instant, UTC
    inventory: HardwareInventory
    volumes: tuple[VolumeInfo, ...] = ()
    encoders: tuple[EncoderCapability, ...] = ()
    network_path: Literal["tailscale_direct", "tailscale_relay", "lan", "unknown"] = "unknown"
    probes: tuple[ProbeResult, ...] = ()
    unknown_fields: tuple[str, ...] = ()  # explicitly not-determinable on this machine
    admitted_roles: tuple[str, ...] = ()  # workload roles this node may accept (validated only)

    @model_validator(mode="after")
    def _consistent(self) -> NodeCapabilityReport:
        ids = [v.volume_id for v in self.volumes]
        if len(ids) != len(set(ids)):
            msg = "volume ids must be unique per node"
            raise ValueError(msg)
        for v in self.volumes:
            if v.free_bytes > v.total_bytes:
                msg = f"volume {v.volume_id}: free exceeds total"
                raise ValueError(msg)
        return self

    def verified_encoders(self) -> set[str]:
        return {e.name for e in self.encoders if e.status == "verified" and e.kind == "encode"}
