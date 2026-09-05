from __future__ import annotations

import subprocess

import pytest

from content_factory.hardware.capability import build_capability_report, probe_encoders
from content_factory.schemas.hardware import GPUInfo, HardwareInventory
from content_factory.schemas.nodes import NodeCapabilityReport, VolumeInfo

ENCODER_LISTING = """\
Encoders:
 V....D libx264              libx264 H.264 / AVC
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder
 V....D av1_nvenc            NVIDIA NVENC av1 encoder
 A....D aac                  AAC (Advanced Audio Coding)
"""


def _fake_runner(results: dict[str, int]):
    """Map encoder name → return code for the tiny test encode; '-encoders' returns the listing."""

    def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        if "-encoders" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=ENCODER_LISTING, stderr="")
        name = cmd[cmd.index("-c:v") + 1]
        rc = results.get(name, 0)
        return subprocess.CompletedProcess(
            cmd, rc, stdout="", stderr="" if rc == 0 else "InitializeEncoder failed"
        )

    return run


def test_listed_is_not_verified_the_test_encode_decides() -> None:
    # av1_nvenc is LISTED by this ffmpeg build but the hardware cannot encode it (RTX 3090 case).
    caps = probe_encoders(
        ("h264_nvenc", "av1_nvenc", "hevc_nvenc", "libx264"),
        runner=_fake_runner({"av1_nvenc": 1}),
    )
    by_name = {c.name: c.status for c in caps}
    assert by_name["h264_nvenc"] == "verified"
    assert by_name["av1_nvenc"] == "failed"  # listed, probed, refused — never assumed
    assert by_name["hevc_nvenc"] == "unsupported"  # not even in the build
    assert by_name["libx264"] == "verified"


def _inventory(gpus: tuple[GPUInfo, ...]) -> HardwareInventory:
    return HardwareInventory(
        os="linux",
        os_version="ubuntu-24.04",
        arch="x86_64",
        cpu_model="test-cpu",
        cpu_threads=16,
        ram_bytes=32 << 30,
        disk_free_bytes=1 << 40,
        gpus=gpus,
        probed_at="2026-09-05T12:00:00+00:00",
    )


def test_cpu_only_nodes_are_healthy_but_not_gpu_admitted() -> None:
    report = build_capability_report(
        "nde_cpuonly0001",
        hostname="cpu-box",
        inventory=_inventory(()),
        volumes=(),
        encoders=(),
        discovered_at="2026-09-05T12:00:00+00:00",
    )
    assert "gpu" not in report.admitted_roles and "cpu" in report.admitted_roles
    gpu_probe = next(p for p in report.probes if p.name == "gpu")
    assert gpu_probe.status == "unsupported" and "CPU-only" in gpu_probe.detail


def test_gpu_node_report_carries_verified_encoders_only() -> None:
    caps = probe_encoders(("h264_nvenc", "av1_nvenc"), runner=_fake_runner({"av1_nvenc": 1}))
    report = build_capability_report(
        "nde_gpubox00001",
        hostname="gpu-box",
        inventory=_inventory((GPUInfo(vendor="nvidia", model="RTX 3090", vram_bytes=24 << 30),)),
        volumes=(),
        encoders=caps,
        discovered_at="2026-09-05T12:00:00+00:00",
    )
    assert "gpu" in report.admitted_roles
    assert report.verified_encoders() == {"h264_nvenc"}
    # Deterministic and serializable: same probe → same report hash.
    again = build_capability_report(
        "nde_gpubox00001",
        hostname="gpu-box",
        inventory=report.inventory,
        volumes=(),
        encoders=caps,
        discovered_at="2026-09-05T12:00:00+00:00",
    )
    assert again.content_hash() == report.content_hash()


def test_volume_invariants() -> None:
    vol = VolumeInfo(
        volume_id="uuid:abc",
        mount_path="/mnt/a",
        total_bytes=100,
        free_bytes=40,
        writable=True,
    )
    with pytest.raises(ValueError, match="free exceeds total"):
        NodeCapabilityReport(
            node_id="nde_test0000001",
            hostname="h",
            discovered_at="2026-09-05",
            inventory=_inventory(()),
            volumes=(vol.model_copy(update={"free_bytes": 200}),),
        )
    with pytest.raises(ValueError, match="unique"):
        NodeCapabilityReport(
            node_id="nde_test0000001",
            hostname="h",
            discovered_at="2026-09-05",
            inventory=_inventory(()),
            volumes=(vol, vol),
        )
