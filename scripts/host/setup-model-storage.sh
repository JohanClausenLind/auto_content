#!/usr/bin/env bash
# Mount the idle 10TB disk + carve the free space on nvme0n1 into a fast model volume.
# Generated 2026-09-05. Run with: sudo bash /home/vega/setup-model-storage.sh
set -euo pipefail

RED=$'\e[31m'; GRN=$'\e[32m'; YEL=$'\e[33m'; NC=$'\e[0m'
info(){ echo "${GRN}==>${NC} $*"; }
warn(){ echo "${YEL}!!${NC} $*"; }
die(){  echo "${RED}ERROR:${NC} $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run me with sudo"

ESP_PARTUUID=cf51f8dd-fd10-44b6-a3b7-f71e392171b3   # nvme0n1p1 = the ESP firmware boots from

# ---------------------------------------------------------------- safety gate
info "Safety check: confirming /dev/nvme0n1p1 is the live ESP and will NOT be touched"
actual=$(blkid -s PARTUUID -o value /dev/nvme0n1p1)
[ "$actual" = "$ESP_PARTUUID" ] || die "nvme0n1p1 PARTUUID changed ($actual). Aborting - layout is not what was surveyed."
if [ -e /dev/nvme0n1p2 ]; then
  fs=$(blkid -s TYPE -o value /dev/nvme0n1p2 2>/dev/null || true)
  if [ -n "${fs// /}" ] && [ "$fs" != "ext4" ]; then
    die "/dev/nvme0n1p2 exists and holds a '$fs' filesystem. Aborting rather than risk existing data."
  fi
  if [ "$fs" = "ext4" ] && [ "$(blkid -s LABEL -o value /dev/nvme0n1p2 2>/dev/null)" != "fastmodels" ]; then
    die "/dev/nvme0n1p2 holds an ext4 fs that is NOT ours (label != fastmodels). Aborting."
  fi
  SKIP_CREATE=1
  info "  p2 already exists (bare or already ours) - will resume, not recreate"
else
  SKIP_CREATE=0
  info "  ok - p1 is the ESP, p2 does not exist yet"
fi

cp /etc/fstab /etc/fstab.bak.$(date +%Y%m%d-%H%M%S)
info "fstab backed up"

# ------------------------------------------------- 1. mount the existing 10TB
info "Loading xfs kernel module"
modprobe xfs
grep -qw xfs /proc/filesystems || die "kernel refused to load xfs support"

if ! command -v xfs_repair >/dev/null; then
  info "Installing xfsprogs (for xfs_repair / xfs_growfs; the mount itself needs only the module)"
  apt-get install -y xfsprogs || warn "xfsprogs install failed - mount will still work, repair tools will not"
fi

info "Mounting existing 10TB XFS (/dev/sda1, label '10tb_disk') at /mnt/bulk"
mkdir -p /mnt/bulk
if ! mountpoint -q /mnt/bulk; then mount /dev/sda1 /mnt/bulk; fi
echo "--- contents of the 10TB disk ---"
ls -la /mnt/bulk/ | head -30
df -h /mnt/bulk | tail -1
echo "---------------------------------"

# ------------------------------- 2. new partition in nvme0n1's free space
if [ "$SKIP_CREATE" = "1" ]; then
  info "Skipping partition creation - /dev/nvme0n1p2 is already present"
else
info "Creating /dev/nvme0n1p2 from the ~952G of free space after the ESP"
sgdisk -n 2:0:0 -t 2:8300 -c 2:"fastmodels" /dev/nvme0n1

# /boot/efi is now mounted FROM this disk, so the kernel will refuse a full
# partition-table re-read ("device busy"). partx adds just the new partition
# without disturbing the mounted p1.
udevadm settle
partx -a --nr 2 /dev/nvme0n1 2>/dev/null || true
partprobe /dev/nvme0n1 2>/dev/null || true
udevadm settle; sleep 2

if [ ! -e /dev/nvme0n1p2 ]; then
  warn "p2 node did not appear; retrying partx"
  partx -u /dev/nvme0n1 2>/dev/null || true
  udevadm settle; sleep 2
fi
[ -e /dev/nvme0n1p2 ] || die "p2 did not appear. The partition IS created in the GPT (verify: sgdisk -p /dev/nvme0n1); a reboot will expose it, then re-run this script."
fi

# paranoia: never format something already carrying a filesystem.
# NOTE: `blkid <dev>` exits 0 even on a bare GPT partition because it reports
# PARTLABEL/PARTUUID from the partition table. Only the TYPE field means "has
# a filesystem", so test that specifically.
existing_fs=$(blkid -s TYPE -o value /dev/nvme0n1p2 2>/dev/null || true)
if [ -n "${existing_fs// /}" ]; then
  die "/dev/nvme0n1p2 already has a $existing_fs filesystem. Refusing to format."
fi
info "  confirmed: p2 is bare (no filesystem)"

if [ "$(blkid -s LABEL -o value /dev/nvme0n1p2 2>/dev/null)" = "fastmodels" ]; then
  info "Already formatted as ext4/fastmodels - skipping mkfs"
else
  info "Formatting /dev/nvme0n1p2 as ext4 (label: fastmodels)"
  mkfs.ext4 -m 0 -L fastmodels /dev/nvme0n1p2
fi

mkdir -p /mnt/fast
mountpoint -q /mnt/fast || mount /dev/nvme0n1p2 /mnt/fast

info "Re-checking the ESP survived the partition-table edit"
findmnt -no SOURCE /boot/efi | grep -q nvme0n1p1 || die "/boot/efi is no longer on nvme0n1p1 - STOP, do not reboot, investigate"
[ -f /boot/efi/EFI/ubuntu/shimx64.efi ] || die "shimx64.efi vanished from the ESP - STOP, do not reboot"
info "  ESP intact: $(findmnt -no SOURCE /boot/efi), shimx64.efi present"

# ---------------------------------------------------------------- 3. fstab
BULK_UUID=$(blkid -s UUID -o value /dev/sda1)
FAST_UUID=$(blkid -s UUID -o value /dev/nvme0n1p2)

grep -q "$BULK_UUID" /etc/fstab || cat >> /etc/fstab <<EOF

# 10TB archive (Seagate IronWolf) - added 2026-09-05
UUID=$BULK_UUID  /mnt/bulk  xfs   defaults,nofail,x-systemd.device-timeout=15  0 2
EOF
grep -q "$FAST_UUID" /etc/fstab || cat >> /etc/fstab <<EOF
# fast NVMe model volume (nvme0n1p2) - added 2026-09-05
UUID=$FAST_UUID  /mnt/fast  ext4  defaults,nofail,x-systemd.device-timeout=15  0 2
EOF

systemctl daemon-reload
mount -a
info "fstab updated (both use 'nofail' so a missing disk can never block boot)"

# ---------------------------------------------------------------- 4. ownership
mkdir -p /mnt/fast/models /mnt/bulk/ai-archive
# NOTE: deliberately NOT recursing into /mnt/bulk - it already holds ~537G of
# pre-existing data (an old /home backup) whose ownership must not be rewritten.
chown vega:vega /mnt/fast /mnt/fast/models /mnt/bulk/ai-archive
chown -R vega:vega /mnt/fast/models
info "Ownership handed to vega"

echo
info "DONE"
df -h /mnt/fast /mnt/bulk | sed '1p;/mnt/d'
echo
warn "Separate latent issue found during the survey (NOT changed by this script):"
warn "  Your firmware boots the ESP on nvme0n1p1, but fstab mounts nvme1n1p5 at"
warn "  /boot/efi. There are two /boot/efi lines in fstab. Kernel updates land in"
warn "  /boot (fine), but grub/shim EFI binaries are written to the ESP you do NOT"
warn "  boot from, so they are drifting out of date. Worth fixing separately."
