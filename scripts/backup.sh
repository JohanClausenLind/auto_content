#!/usr/bin/env bash
# Backups (section 25): Postgres dump + artifact-store sync, timestamped, with a restore path.
# Usage: scripts/backup.sh [backup_dir]   ·   restore rehearsal: scripts/restore-rehearsal.sh <dump>
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
DEST="${1:-backups}/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$DEST"
docker exec content-factory-postgres-1 pg_dump -U content_factory -d content_factory -Fc > "$DEST/content_factory.dump"
rsync -a --delete data/artifacts/ "$DEST/artifacts/" 2>/dev/null || cp -r data/artifacts "$DEST/artifacts"
sha256sum "$DEST/content_factory.dump" > "$DEST/SHA256SUMS"
find "$DEST/artifacts" -type f -print0 | sort -z | xargs -0 sha256sum >> "$DEST/SHA256SUMS" 2>/dev/null || true
echo "backup written to $DEST ($(du -sh "$DEST" | cut -f1))"
