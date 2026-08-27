"""HardwareProbe (5.8): normalized inventory from the real machine or a deterministic mock."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from datetime import UTC, datetime

from content_factory.schemas.hardware import GPUInfo, HardwareInventory, RuntimeInfo


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)  # noqa: S603
        return out.stdout.strip() if out.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def probe_gpus() -> tuple[GPUInfo, ...]:
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,driver_version,compute_cap",
            "--format=csv,noheader,nounits",
        ]
    )
    gpus: list[GPUInfo] = []
    if out:
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                gpus.append(
                    GPUInfo(
                        vendor="nvidia",
                        model=parts[0],
                        vram_bytes=int(float(parts[1])) * 1024 * 1024,
                        driver_version=parts[2],
                        compute_capability=parts[3] if len(parts) > 3 else None,
                    )
                )
    return tuple(gpus)


def _runtime(name: str, args: list[str]) -> RuntimeInfo | None:
    path = shutil.which(name)
    if not path:
        return None
    out = _run([name, *args])
    return RuntimeInfo(name=name, version=(out.splitlines()[0][:80] if out else None), path=path)


def probe_hardware() -> HardwareInventory:
    ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") if hasattr(os, "sysconf") else 0
    cpu = platform.processor() or platform.machine()
    lscpu = _run(["bash", "-c", "grep -m1 'model name' /proc/cpuinfo | cut -d: -f2"])
    if lscpu:
        cpu = lscpu.strip()
    runtimes = [
        r
        for r in (
            _runtime("ffmpeg", ["-version"]),
            _runtime("node", ["--version"]),
            _runtime("comfy", ["--version"]),
            _runtime("ollama", ["--version"]),
        )
        if r
    ]
    return HardwareInventory(
        os=platform.system(),
        os_version=platform.release(),
        arch=platform.machine(),
        cpu_model=cpu,
        cpu_threads=os.cpu_count() or 1,
        ram_bytes=ram,
        disk_free_bytes=shutil.disk_usage(os.getcwd()).free,
        gpus=probe_gpus(),
        runtimes=tuple(runtimes),
        probed_at=datetime.now(UTC).isoformat(),
    )


def mock_inventory(profile: str) -> HardwareInventory:
    """Deterministic profiles for tests and CI: 'cpu_only', 'rtx3090', 'rtx4060_8gb', 'a100_80gb'."""  # noqa: E501
    base = dict(
        os="Linux",
        os_version="mock",
        arch="x86_64",
        cpu_model="mock-cpu",
        cpu_threads=16,
        ram_bytes=32 * 1024**3,
        disk_free_bytes=500 * 1024**3,
        runtimes=(),
        probed_at="2026-01-01T00:00:00Z",
    )
    gpus = {
        "cpu_only": (),
        "rtx3090": (
            GPUInfo(
                vendor="nvidia",
                model="NVIDIA GeForce RTX 3090",
                vram_bytes=24 * 1024**3,
                driver_version="595.84",
                compute_capability="8.6",
            ),
        ),
        "rtx4060_8gb": (
            GPUInfo(
                vendor="nvidia",
                model="NVIDIA GeForce RTX 4060",
                vram_bytes=8 * 1024**3,
                driver_version="595.84",
                compute_capability="8.9",
            ),
        ),
        "a100_80gb": (
            GPUInfo(
                vendor="nvidia",
                model="NVIDIA A100 80GB",
                vram_bytes=80 * 1024**3,
                driver_version="595.84",
                compute_capability="8.0",
            ),
        ),
    }
    return HardwareInventory(gpus=gpus[profile], **base)  # type: ignore[arg-type]
