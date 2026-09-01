"""Resource admission (2.7, 18.5): VRAM/concurrency leases, measured calibration records, and
quarantine after repeated OOM or corrupt output. In-memory engine; workers integrate in phase 6."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from content_factory.db.base import new_id
from content_factory.schemas.hardware import HardwareInventory


class LeaseError(Exception):
    pass


class QuarantinedError(LeaseError):
    pass


@dataclass(frozen=True)
class Calibration:
    """A measured run of (model_variant x skill) on this machine — first estimates get replaced."""

    key: str  # f"{alias}@{skill_id}"
    peak_vram_bytes: int
    peak_ram_bytes: int
    runtime_seconds: float
    quality_metric: float | None
    measured_at: str


@dataclass(frozen=True)
class Lease:
    lease_id: str
    key: str
    vram_bytes: int
    concurrency_slots: int
    expires_at: datetime


@dataclass
class ResourceManager:
    inventory: HardwareInventory
    headroom_ratio: float = 0.12
    max_concurrency: int = 2
    oom_quarantine_threshold: int = 2
    lease_ttl: timedelta = timedelta(minutes=30)
    calibrations: dict[str, Calibration] = field(default_factory=dict)
    _leases: dict[str, Lease] = field(default_factory=dict)
    _oom_counts: dict[str, int] = field(default_factory=dict)
    _quarantined: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _usable_vram(self) -> int:
        if not self.inventory.gpus:
            return 0
        best = max(g.vram_bytes for g in self.inventory.gpus)
        return int(best * (1 - self.headroom_ratio))

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def record_calibration(self, cal: Calibration) -> None:
        with self._lock:
            self.calibrations[cal.key] = cal

    def vram_estimate(self, key: str, declared_bytes: int) -> int:
        """Measured calibration overrides the declared ResourceProfile estimate."""
        cal = self.calibrations.get(key)
        return cal.peak_vram_bytes if cal else declared_bytes

    def acquire(self, key: str, *, declared_vram_bytes: int, slots: int = 1) -> Lease:
        with self._lock:
            if key in self._quarantined:
                raise QuarantinedError(f"{key} is quarantined: {self._quarantined[key]}")
            self._expire_locked()
            need = self.vram_estimate(key, declared_vram_bytes)
            usable = self._usable_vram()
            in_use_vram = sum(lease.vram_bytes for lease in self._leases.values())
            in_use_slots = sum(lease.concurrency_slots for lease in self._leases.values())
            if need > usable:
                raise LeaseError(f"{key} needs {need} B VRAM; {usable} B usable after headroom")
            if in_use_vram + need > usable:
                raise LeaseError(
                    f"{key} needs {need} B VRAM; {usable - in_use_vram} B free under active leases"
                )
            if in_use_slots + slots > self.max_concurrency:
                raise LeaseError(f"concurrency exhausted ({in_use_slots}/{self.max_concurrency})")
            lease = Lease(
                lease_id=new_id("lse"),
                key=key,
                vram_bytes=need,
                concurrency_slots=slots,
                expires_at=self._now() + self.lease_ttl,
            )
            self._leases[lease.lease_id] = lease
            return lease

    def release(self, lease: Lease) -> None:
        with self._lock:
            self._leases.pop(lease.lease_id, None)

    def report_oom(self, lease: Lease) -> bool:
        """Record an OOM under this lease. Returns True when the tuple is now quarantined."""
        with self._lock:
            self._leases.pop(lease.lease_id, None)
            count = self._oom_counts.get(lease.key, 0) + 1
            self._oom_counts[lease.key] = count
            if count >= self.oom_quarantine_threshold:
                self._quarantined[lease.key] = f"{count} OOMs on this machine"
                return True
            return False

    def report_corrupt_output(self, key: str, reason: str) -> None:
        with self._lock:
            self._quarantined[key] = f"corrupt output: {reason}"

    def quarantined(self) -> dict[str, str]:
        with self._lock:
            return dict(self._quarantined)

    def _expire_locked(self) -> None:
        now = self._now()
        for lid in [lid for lid, lease in self._leases.items() if lease.expires_at <= now]:
            del self._leases[lid]
