from __future__ import annotations

from datetime import timedelta

import pytest

from content_factory.hardware.leases import (
    Calibration,
    LeaseError,
    QuarantinedError,
    ResourceManager,
)
from content_factory.hardware.probe import mock_inventory

GB = 1024**3


def rm(profile: str = "rtx3090") -> ResourceManager:
    return ResourceManager(inventory=mock_inventory(profile))


def test_vram_admission_with_headroom_and_active_leases() -> None:
    m = rm()  # 24 GiB * 0.88 = ~21.1 GiB usable
    l1 = m.acquire("sdxl@image.generate", declared_vram_bytes=12 * GB)
    with pytest.raises(LeaseError, match="free under active leases"):
        m.acquire("kokoro@voice.synthesize", declared_vram_bytes=12 * GB)
    m.release(l1)
    l2 = m.acquire("kokoro@voice.synthesize", declared_vram_bytes=12 * GB)
    m.release(l2)
    with pytest.raises(LeaseError, match="usable after headroom"):
        m.acquire("huge@image.generate", declared_vram_bytes=23 * GB)
    assert rm("cpu_only")._usable_vram() == 0


def test_measured_calibration_overrides_declared_estimate() -> None:
    m = rm()
    m.record_calibration(
        Calibration(
            key="sdxl@image.generate",
            peak_vram_bytes=20 * GB,
            peak_ram_bytes=8 * GB,
            runtime_seconds=12.5,
            quality_metric=0.9,
            measured_at="2026-09-01",
        )
    )
    lease = m.acquire(
        "sdxl@image.generate", declared_vram_bytes=8 * GB
    )  # declared says 8, measured says 20
    assert lease.vram_bytes == 20 * GB
    with pytest.raises(LeaseError):
        m.acquire("other@image.generate", declared_vram_bytes=8 * GB)


def test_concurrency_slots_and_lease_expiry() -> None:
    m = rm()
    m.max_concurrency = 2
    m.acquire("a@x", declared_vram_bytes=1 * GB)
    m.acquire("b@x", declared_vram_bytes=1 * GB)
    with pytest.raises(LeaseError, match="concurrency"):
        m.acquire("c@x", declared_vram_bytes=1 * GB)
    m.lease_ttl = timedelta(seconds=-1)  # everything already expired
    lease = None
    m._leases.clear()
    lease = m.acquire("c@x", declared_vram_bytes=1 * GB)
    assert lease.key == "c@x"


def test_repeated_oom_quarantines_the_tuple() -> None:
    m = rm()
    l1 = m.acquire("flaky@image.generate", declared_vram_bytes=4 * GB)
    assert m.report_oom(l1) is False
    l2 = m.acquire("flaky@image.generate", declared_vram_bytes=4 * GB)
    assert m.report_oom(l2) is True
    with pytest.raises(QuarantinedError):
        m.acquire("flaky@image.generate", declared_vram_bytes=4 * GB)
    assert "flaky@image.generate" in m.quarantined()
    # Other tuples are unaffected.
    m.acquire("healthy@image.generate", declared_vram_bytes=4 * GB)


def test_corrupt_output_quarantines_immediately() -> None:
    m = rm()
    m.report_corrupt_output("bad@image.generate", "NaN frames")
    with pytest.raises(QuarantinedError, match="corrupt output"):
        m.acquire("bad@image.generate", declared_vram_bytes=1 * GB)
