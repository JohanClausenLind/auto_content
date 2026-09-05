from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from content_factory.schemas.storage_plan import RoleAssignment, StoragePlan, StorageRole
from content_factory.storage import (
    StorageAccountant,
    StorageGuardError,
    bind_role,
    parse_mounts,
    safe_child,
    verify_role,
    volume_identity,
)
from content_factory.storage.volumes import MARKER_NAME

MOUNTS = """\
proc /proc proc rw 0 0
sysfs /sys sysfs rw 0 0
tmpfs /run tmpfs rw 0 0
/dev/nvme0n1p2 / ext4 rw 0 0
/dev/sda1 /mnt/archive ext4 rw 0 0
/dev/sdb1 /run/media/vega/usb exfat rw 0 0
overlay /var/lib/docker/overlay2/x/merged overlay rw 0 0
"""


def _assignment(tmp_path: Path, role: StorageRole = StorageRole.scratch, **kw) -> RoleAssignment:
    root = tmp_path / role.value
    base = {
        "role": role,
        "volume_id": volume_identity(tmp_path),
        "root_path": str(root),
    }
    base.update(kw)
    return RoleAssignment(**base)


def test_parse_mounts_keeps_real_filesystems_and_removable_media() -> None:
    mounts = parse_mounts(MOUNTS)
    paths = [m for _, m, _ in mounts]
    assert paths == ["/", "/mnt/archive", "/run/media/vega/usb"]  # pseudo/overlay/run dropped


def test_storage_plan_refuses_backup_on_the_archive_volume() -> None:
    def plan(backup_volume: str) -> StoragePlan:
        return StoragePlan(
            node_id="nde_test0000001",
            assignments=(
                RoleAssignment(
                    role=StorageRole.durable_archive, volume_id="uuid:aaa", root_path="/a"
                ),
                RoleAssignment(role=StorageRole.backup, volume_id=backup_volume, root_path="/b"),
            ),
        )

    assert plan("uuid:bbb").assignment(StorageRole.backup) is not None
    with pytest.raises(ValueError, match="independent backup"):
        plan("uuid:aaa")
    with pytest.raises(ValueError, match="exactly one"):
        StoragePlan(
            node_id="nde_test0000001",
            assignments=(
                RoleAssignment(role=StorageRole.scratch, volume_id="uuid:aaa", root_path="/a"),
                RoleAssignment(role=StorageRole.scratch, volume_id="uuid:aaa", root_path="/c"),
            ),
        )


def test_bind_then_verify_round_trip(tmp_path: Path) -> None:
    a = _assignment(tmp_path)
    bind_role(a)
    check = verify_role(a)
    assert check.ok and check.free_bytes > 0
    marker = json.loads((Path(a.root_path) / MARKER_NAME).read_text())
    assert marker["role"] == "scratch" and marker["volume_id"] == a.volume_id


def test_bind_refuses_a_different_volume(tmp_path: Path) -> None:
    wrong = _assignment(tmp_path, volume_id="uuid:not-this-volume")
    with pytest.raises(StorageGuardError, match="different volume"):
        bind_role(wrong)
    assert not Path(wrong.root_path).exists()  # nothing was created on the wrong disk


def test_verify_fails_when_root_absent_and_never_creates_it(tmp_path: Path) -> None:
    a = _assignment(tmp_path)
    check = verify_role(a)  # never bound: the "volume" is not where it should be
    assert not check.ok and "absent" in check.reason
    assert not Path(a.root_path).exists()  # verify must not mkdir on whatever disk is there


def test_verify_detects_a_swapped_disk_at_the_same_mount_path(tmp_path: Path) -> None:
    a = _assignment(tmp_path)
    bind_role(a)
    marker_path = Path(a.root_path) / MARKER_NAME
    marker = json.loads(marker_path.read_text())
    marker["volume_id"] = "uuid:replacement-disk"  # simulate a different disk mounted here
    marker_path.write_text(json.dumps(marker))
    check = verify_role(a)
    assert not check.ok and "different disk" in check.reason


def test_verify_fails_on_read_only_and_on_reserve_breach(tmp_path: Path) -> None:
    a = _assignment(tmp_path)
    bind_role(a)
    root = Path(a.root_path)
    os.chmod(root, 0o500)
    try:
        check = verify_role(a)
        assert not check.ok and "not writable" in check.reason
    finally:
        os.chmod(root, 0o700)
    greedy = _assignment(tmp_path, min_free_reserve_bytes=1 << 60)
    bind_role(greedy)  # same root role, marker already matches scratch
    check = verify_role(greedy)
    assert not check.ok and "below reserve" in check.reason


def test_safe_child_refuses_traversal(tmp_path: Path) -> None:
    assert safe_child(tmp_path, "frames/0001.png").is_relative_to(tmp_path)
    with pytest.raises(StorageGuardError, match="escapes"):
        safe_child(tmp_path, "../outside.bin")


def test_shared_volume_free_space_is_never_promised_twice(tmp_path: Path) -> None:
    vol = volume_identity(tmp_path)
    scratch = _assignment(tmp_path, StorageRole.scratch)
    cache = _assignment(tmp_path, StorageRole.model_cache)
    acct = StorageAccountant(volume_free={vol: 100})
    acct.reserve(scratch, 60)
    with pytest.raises(StorageGuardError, match="cannot reserve"):
        acct.reserve(cache, 60)  # the same 100 free bytes back both roles
    acct.reserve(cache, 40)
    acct.release(scratch, 60)
    assert acct.available(scratch) == 60


def test_role_quota_and_min_reserve_bound_reservations(tmp_path: Path) -> None:
    vol = volume_identity(tmp_path)
    limited = _assignment(tmp_path, StorageRole.scratch, quota_bytes=50)
    acct = StorageAccountant(volume_free={vol: 1000})
    with pytest.raises(StorageGuardError):
        acct.reserve(limited, 60)  # over its quota despite plenty of volume space
    acct.reserve(limited, 50)
    reserved = _assignment(tmp_path, StorageRole.model_cache, min_free_reserve_bytes=900)
    assert acct.available(reserved) == 50  # 1000 - 50 reserved - 900 minimum reserve
