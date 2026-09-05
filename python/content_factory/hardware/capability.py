"""Build a node capability report: probe hardware, verify encoders, inventory volumes.

Listed is not verified: ffmpeg's `-encoders` output only proves the build knows the encoder
name. Verification runs a real 0.2 s test encode — this is what catches an RTX 3090 build that
lists av1_nvenc but cannot encode AV1. The runner is injectable so tests stay offline and the
report never guesses: anything unprobeable lands in ``unknown_fields`` or a failed ProbeResult.
"""

from __future__ import annotations

import socket
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime

from content_factory.hardware.probe import probe_hardware
from content_factory.schemas.hardware import HardwareInventory
from content_factory.schemas.nodes import (
    EncoderCapability,
    NodeCapabilityReport,
    ProbeResult,
    VolumeInfo,
)
from content_factory.storage.volumes import discover_volumes

DEFAULT_ENCODER_CANDIDATES = (
    "h264_nvenc",
    "hevc_nvenc",
    "av1_nvenc",
    "libx264",
    "libx265",
    "libsvtav1",
)

Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=60)  # noqa: S603


def probe_encoders(
    candidates: tuple[str, ...] = DEFAULT_ENCODER_CANDIDATES, *, runner: Runner = _run
) -> tuple[EncoderCapability, ...]:
    try:
        listing = runner(["ffmpeg", "-hide_banner", "-encoders"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        return tuple(
            EncoderCapability(name=n, status="failed", detail=f"ffmpeg unavailable: {exc}")
            for n in candidates
        )
    listed = {
        line.split()[1]
        for line in listing.stdout.splitlines()
        if line.strip().startswith("V") and len(line.split()) > 1
    }
    out: list[EncoderCapability] = []
    for name in candidates:
        if name not in listed:
            out.append(
                EncoderCapability(name=name, status="unsupported", detail="not in ffmpeg build")
            )
            continue
        try:
            probe = runner(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostdin",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=256x256:d=0.2:r=30",
                    "-c:v",
                    name,
                    "-frames:v",
                    "4",
                    "-f",
                    "null",
                    "-",
                ]
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            out.append(EncoderCapability(name=name, status="failed", detail=str(exc)[:280]))
            continue
        if probe.returncode == 0:
            out.append(EncoderCapability(name=name, status="verified", detail="test encode ok"))
        else:
            tail = (probe.stderr or "").strip().splitlines()
            out.append(
                EncoderCapability(
                    name=name,
                    status="failed",
                    detail=(tail[-1] if tail else "test encode failed")[:280],
                )
            )
    return tuple(out)


def build_capability_report(
    node_id: str,
    *,
    hostname: str | None = None,
    inventory: HardwareInventory | None = None,
    volumes: tuple[VolumeInfo, ...] | None = None,
    encoders: tuple[EncoderCapability, ...] | None = None,
    discovered_at: str | None = None,
) -> NodeCapabilityReport:
    """Assemble the report; every argument defaults to a live probe of THIS machine."""
    inventory = inventory if inventory is not None else probe_hardware()
    volumes = volumes if volumes is not None else discover_volumes()
    encoders = encoders if encoders is not None else probe_encoders()
    probes = [
        ProbeResult(
            name="gpu",
            status="ok" if inventory.gpus else "unsupported",
            detail=", ".join(g.model for g in inventory.gpus) or "no GPU detected (CPU-only node)",
        ),
        ProbeResult(
            name="encoders",
            status="ok" if any(e.status == "verified" for e in encoders) else "failed",
            detail=f"{sum(1 for e in encoders if e.status == 'verified')} verified",
        ),
        ProbeResult(
            name="volumes",
            status="ok" if volumes else "failed",
            detail=f"{len(volumes)} mounted real filesystems",
        ),
    ]
    unknown = []
    if not any(v.media_class != "unknown" for v in volumes):
        unknown.append("volume media classes")
    roles = ["cpu", "composition"]
    if inventory.gpus:
        roles.append("gpu")
    return NodeCapabilityReport(
        node_id=node_id,
        hostname=hostname or socket.gethostname(),
        discovered_at=discovered_at or datetime.now(UTC).isoformat(),
        inventory=inventory,
        volumes=volumes,
        encoders=encoders,
        probes=tuple(probes),
        unknown_fields=tuple(unknown),
        admitted_roles=tuple(roles),
    )
