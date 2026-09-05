"""Non-destructive volume discovery, stable-identity write guards, and shared-space accounting.

Discovery only reads: mounts, sizes, writability, rotational class. Binding a role writes one
marker file at the chosen root; verification refuses to write when the expected volume is not
where it was bound — a reboot that reorders devices, a replacement disk at the same mount path,
a read-only remount, or a missing drive must queue/fail the affected work, never silently fill
the boot filesystem. Nothing here formats, repartitions, or deletes existing files.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from content_factory.schemas.nodes import MediaClass, VolumeInfo
from content_factory.schemas.storage_plan import RoleAssignment

MARKER_NAME = ".content-factory-volume.json"

_PSEUDO_FS = {
    "proc",
    "sysfs",
    "devtmpfs",
    "devpts",
    "tmpfs",
    "cgroup",
    "cgroup2",
    "securityfs",
    "pstore",
    "efivarfs",
    "bpf",
    "tracefs",
    "debugfs",
    "hugetlbfs",
    "mqueue",
    "configfs",
    "fusectl",
    "binfmt_misc",
    "autofs",
    "overlay",
    "squashfs",
    "ramfs",
    "nsfs",
    "rpc_pipefs",
}


class StorageGuardError(Exception):
    pass


# The kernel's mangle_path escapes exactly space, tab, newline, and backslash as octal.
# A targeted unescape keeps UTF-8 mount paths intact — decode("unicode_escape") would
# reinterpret their bytes as latin-1 and mojibake every non-ASCII path.
_MOUNT_ESCAPE = re.compile(r"\\(040|011|012|134)")


def _unescape_mount(mount: str) -> str:
    return _MOUNT_ESCAPE.sub(lambda m: chr(int(m.group(1), 8)), mount)


def parse_mounts(text: str) -> list[tuple[str, str, str]]:
    """(device, mount_path, fstype) for real filesystems from /proc/mounts content."""
    out: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        device, mount, fstype = parts[0], parts[1], parts[2]
        if fstype in _PSEUDO_FS:
            continue
        if mount.startswith(("/proc", "/sys", "/dev")):
            continue
        if mount.startswith("/run") and not mount.startswith("/run/media"):
            continue  # /run is tmpfs plumbing, but removable media mounts under /run/media
        # Octal escapes in /proc/mounts (e.g. \040 for space).
        out.append((device, _unescape_mount(mount), fstype))
    return out


def volume_identity(path: Path) -> str:
    """Stable volume identity for the filesystem holding ``path``: the filesystem UUID when
    findmnt can report it, else the device number (stable within a boot, refreshed on bind)."""
    try:
        proc = subprocess.run(  # noqa: S603
            ["findmnt", "-no", "UUID", "--target", str(path)],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        fs_uuid = proc.stdout.strip()
        if proc.returncode == 0 and fs_uuid:
            return f"uuid:{fs_uuid}"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return f"dev:{path.stat().st_dev}"


def _media_class(device: str) -> MediaClass:
    name = Path(device).name
    if name.startswith("nvme"):
        return "nvme"
    base = name.rstrip("0123456789")
    rotational = Path(f"/sys/class/block/{base}/queue/rotational")
    try:
        if rotational.exists():
            return "hdd" if rotational.read_text().strip() == "1" else "ssd"
    except OSError:
        pass
    if device.startswith(("//", "nfs", "cifs")) or ":" in device:
        return "network"
    return "unknown"


def discover_volumes(mounts_text: str | None = None) -> tuple[VolumeInfo, ...]:
    """Read-only inventory of mounted real filesystems. Never scans file contents."""
    if mounts_text is None:
        mounts_text = Path("/proc/mounts").read_text()
    seen: dict[str, VolumeInfo] = {}
    for device, mount, fstype in parse_mounts(mounts_text):
        p = Path(mount)
        try:
            usage = shutil.disk_usage(p)
            identity = volume_identity(p)
        except OSError:
            continue
        if identity in seen:  # bind mounts of the same filesystem count once
            continue
        seen[identity] = VolumeInfo(
            volume_id=identity,
            mount_path=str(p),
            filesystem=fstype,
            media_class=_media_class(device),
            total_bytes=usage.total,
            free_bytes=usage.free,
            writable=os.access(p, os.W_OK),
        )
    return tuple(seen.values())


def bind_role(assignment: RoleAssignment) -> None:
    """Explicit setup step: verify the volume identity, create the root, write the marker.
    Refuses when the mounted volume is not the one the assignment names."""
    root = Path(assignment.root_path)
    probe = root if root.exists() else root.parent
    if not probe.exists():
        msg = f"{assignment.role}: parent of {root} does not exist — is the volume mounted?"
        raise StorageGuardError(msg)
    identity = volume_identity(probe)
    if identity != assignment.volume_id:
        msg = (
            f"{assignment.role}: {probe} is on {identity}, expected {assignment.volume_id} — "
            "refusing to bind onto a different volume"
        )
        raise StorageGuardError(msg)
    root.mkdir(parents=True, exist_ok=True)
    marker = {
        "role": assignment.role.value,
        "volume_id": assignment.volume_id,
        "bound_at": datetime.now(UTC).isoformat(),
    }
    (root / MARKER_NAME).write_text(json.dumps(marker, indent=1))


@dataclass(frozen=True)
class RoleCheck:
    ok: bool
    reason: str
    free_bytes: int = 0


def verify_role(assignment: RoleAssignment) -> RoleCheck:
    """Run at worker start and before writes. Never creates the root: an absent volume fails or
    queues the operation instead of silently writing to whatever is at the path."""
    root = Path(assignment.root_path)
    if not root.is_dir():
        return RoleCheck(False, f"root {root} is absent — volume not mounted or path removed")
    marker_path = root / MARKER_NAME
    if not marker_path.exists():
        return RoleCheck(False, f"no volume marker at {root} — bind the role first")
    try:
        marker = json.loads(marker_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return RoleCheck(False, f"unreadable volume marker: {exc}")
    if marker.get("role") != assignment.role.value:
        return RoleCheck(False, f"marker belongs to role {marker.get('role')!r}")
    if marker.get("volume_id") != assignment.volume_id:
        return RoleCheck(
            False,
            f"marker volume {marker.get('volume_id')!r} != expected {assignment.volume_id!r} — "
            "a different disk may be mounted at this path",
        )
    identity = volume_identity(root)
    if identity != assignment.volume_id:
        return RoleCheck(
            False, f"live volume identity {identity} != expected {assignment.volume_id}"
        )
    probe = root / f".write-probe-{uuid.uuid4().hex[:8]}"
    try:
        probe.write_text("ok")
        probe.unlink()
    except OSError as exc:
        return RoleCheck(False, f"not writable: {exc}")
    free = shutil.disk_usage(root).free
    if free < assignment.min_free_reserve_bytes:
        return RoleCheck(
            False,
            f"free {free} below reserve {assignment.min_free_reserve_bytes}",
            free_bytes=free,
        )
    return RoleCheck(True, "ok", free_bytes=free)


def safe_child(root: Path, relative: str) -> Path:
    """Resolve a path under the role root; refuse traversal and symlink escapes."""
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        msg = f"path {relative!r} escapes the role root {root}"
        raise StorageGuardError(msg)
    return candidate


@dataclass
class StorageAccountant:
    """Free-space reservations accounted per VOLUME, not per role: two roles sharing one
    filesystem cannot both promise the same bytes. In-memory, thread-safe (mirrors CostLedger)."""

    volume_free: dict[str, int]  # volume_id -> currently free bytes
    _reserved: dict[str, int] = field(default_factory=dict)  # volume_id -> reserved bytes
    _role_usage: dict[str, int] = field(default_factory=dict)  # role value -> reserved bytes
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def available(self, assignment: RoleAssignment) -> int:
        with self._lock:
            return self._available_locked(assignment)

    def _available_locked(self, assignment: RoleAssignment) -> int:
        free = self.volume_free.get(assignment.volume_id, 0)
        volume_avail = free - self._reserved.get(assignment.volume_id, 0)
        volume_avail -= assignment.min_free_reserve_bytes
        if assignment.quota_bytes is not None:
            role_left = assignment.quota_bytes - self._role_usage.get(assignment.role.value, 0)
            return max(0, min(volume_avail, role_left))
        return max(0, volume_avail)

    def reserve(self, assignment: RoleAssignment, nbytes: int) -> None:
        if nbytes <= 0:
            msg = "reserve needs a positive size"
            raise StorageGuardError(msg)
        with self._lock:
            if self._available_locked(assignment) < nbytes:
                msg = (
                    f"{assignment.role}: cannot reserve {nbytes} bytes on "
                    f"{assignment.volume_id} (available {self._available_locked(assignment)})"
                )
                raise StorageGuardError(msg)
            self._reserved[assignment.volume_id] = (
                self._reserved.get(assignment.volume_id, 0) + nbytes
            )
            self._role_usage[assignment.role.value] = (
                self._role_usage.get(assignment.role.value, 0) + nbytes
            )

    def release(self, assignment: RoleAssignment, nbytes: int) -> None:
        with self._lock:
            self._reserved[assignment.volume_id] = max(
                0, self._reserved.get(assignment.volume_id, 0) - nbytes
            )
            self._role_usage[assignment.role.value] = max(
                0, self._role_usage.get(assignment.role.value, 0) - nbytes
            )
