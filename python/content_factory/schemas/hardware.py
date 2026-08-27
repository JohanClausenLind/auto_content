"""HardwareInventory (5.8) — normalized probe output."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from content_factory.schemas.base import SchemaModel


class GPUInfo(SchemaModel):
    vendor: Literal["nvidia", "amd", "apple", "intel", "unknown"]
    model: str
    vram_bytes: int = Field(ge=0)
    driver_version: str | None = None
    compute_capability: str | None = None


class RuntimeInfo(SchemaModel):
    name: str
    version: str | None = None
    path: str | None = None


class HardwareInventory(SchemaModel):
    os: str
    os_version: str
    arch: str
    cpu_model: str
    cpu_threads: int = Field(ge=1)
    ram_bytes: int = Field(ge=0)
    disk_free_bytes: int = Field(ge=0)
    gpus: tuple[GPUInfo, ...] = ()
    runtimes: tuple[RuntimeInfo, ...] = ()
    benchmark_revision: str | None = None
    probed_at: str
