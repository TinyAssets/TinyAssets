#!/usr/bin/env bash
# Live owner-box nightly backup: every service keeps serving (24/7).
# Called only by backup.sh after it identifies a committed owner-box layout;
# it inherits backup.sh's SHARED layout lock on fd 9, so no migration can
# start while the trees are copied, and nothing here ever stops a writer.
#
# Copy: data volume and owner trees by reflink (same-filesystem XFS clone for
# the box store), then every SQLite file in the copies re-taken from its live
# source through the SQLite online backup API (backup-live-sqlite.py).
set -euo pipefail

DATA_DIR="$1"
BOX_DIR="$2"
DEST="$3"
HERE="$(dirname "$(realpath "$0")")"
STAGE_ROOT="${BACKUP_SNAPSHOT_DIR:-/var/backups/tinyassets}"
TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
DATA_NAME="tinyassets-data-${TS}.tar.gz"
BOX_NAME="tinyassets-box-store-${TS}.tar.gz"
MANIFEST_NAME="tinyassets-pair-${TS}.json"
STAGE=""
DATA_COPY=""
BOX_COPY=""
BOX_STAGE=""

log() { echo "[backup $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

finish() {
    local rc=$?
    trap - EXIT
    if [[ -n "$BOX_STAGE" ]]; then
        rm -rf -- "$BOX_STAGE" || rc=2
    fi
    if [[ "$rc" != 0 && -n "$STAGE" ]]; then
        log "incomplete paired backup retained at $STAGE"
    fi
    exit "$rc"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

[[ "$EUID" == 0 && -d "$DATA_DIR" && ! -L "$DATA_DIR" \
    && -d "$BOX_DIR" && ! -L "$BOX_DIR" ]] || {
    log "ERROR: owner-box backup needs root and real data/box directories"; exit 2;
}
"$HERE/box-store-check.sh" "$BOX_DIR" || exit 2
python3 - "$DATA_DIR" "$BOX_DIR" "$STAGE_ROOT" <<'PY' || exit 2
import sys
from pathlib import Path

if not Path(sys.argv[3]).is_absolute():
    raise SystemExit("backup staging must be an absolute path")
data, box, stage = (Path(value).resolve() for value in sys.argv[1:])
if stage.is_relative_to(data) or stage.is_relative_to(box):
    raise SystemExit("backup staging must be outside data and owner stores")
PY

exec 7>>/var/lock/tinyassets-backup-owner-box.lock
flock -n 7 || { log "ERROR: another owner-box backup is active"; exit 2; }

mkdir -p "$STAGE_ROOT"
chmod 0700 "$STAGE_ROOT"
STAGE="$(mktemp -d "$STAGE_ROOT/.tinyassets-pair-${TS}.XXXXXX")"
chmod 0700 "$STAGE"
data_root="$(basename "$DATA_DIR")"
box_root="$(basename "$BOX_DIR")"
[[ "$data_root" != "$box_root" ]] || { log "ERROR: backup roots collide"; exit 2; }
DATA_COPY="$STAGE/$data_root"

# Owner trees first, then the data volume. Box reservations are permanent
# broker rows, so every tree copied here already has its reservation in the
# later data copy; the reverse order could archive an owner admitted between
# the two copies without its reservation, and restore would refuse the pair.
# The owner store is a separate XFS mount. A destination under that mount is
# necessary for a real reflink; copying to /var/backups silently falls back to
# a full copy. Keep only numeric owner trees.
# Owner trees are re-taken as their owner (--as-owner), so the stage is
# traversable (0711, not listable) down to each owner's own copy.
BOX_STAGE="$(mktemp -d "$BOX_DIR/.backup-stage-${TS}.XXXXXX")"
chmod 0711 "$BOX_STAGE"
BOX_COPY="$BOX_STAGE/$box_root"
mkdir -m 0711 "$BOX_COPY"
for tree in "$BOX_DIR"/[0-9]*; do
    [[ -d "$tree" && ! -L "$tree" ]] || continue
    cp -a --reflink=always -- "$tree" "$BOX_COPY/$(basename "$tree")"
    python3 "$HERE/backup-live-sqlite.py" --as-owner "$tree" "$BOX_COPY/$(basename "$tree")"
done
cp -a --reflink=auto -- "$DATA_DIR" "$DATA_COPY"
python3 "$HERE/backup-live-sqlite.py" "$DATA_DIR" "$DATA_COPY"
# An owner whose destruction began (a durable fence written before any
# deletion IO) is left out: the later data copy records it as destroyed, so
# restoring its copied tree would bring erased files back. Then fail loudly on
# a pair restore would refuse (box-store-quotas.py reapply).
python3 - "$BOX_COPY" "$DATA_COPY/.broker/state/owner-identities.db" \
    "$DATA_COPY/.boxhost/boxhost.db" <<'PY' || exit 2
import shutil
import sqlite3
import sys
from pathlib import Path

trees, registry, boxhost = (Path(value) for value in sys.argv[1:])
owners = {entry.name for entry in trees.iterdir()
          if entry.name.isdecimal() and entry.is_dir() and not entry.is_symlink()}
if owners and boxhost.is_file() and not boxhost.is_symlink():
    with sqlite3.connect(boxhost.as_uri() + "?mode=ro", uri=True) as db:
        destroyed = {str(row[0]) for row in db.execute("SELECT uid FROM owner_destroys")}
    for uid in sorted(owners & destroyed):
        shutil.rmtree(trees / uid)
        print(f"owner {uid} is being destroyed; its tree is left out", file=sys.stderr)
    owners -= destroyed
if owners:
    if registry.is_symlink() or not registry.is_file():
        raise SystemExit("owner trees copied without the broker identity database")
    with sqlite3.connect(registry.as_uri() + "?mode=ro", uri=True) as db:
        reserved = {str(row[0]) for row in db.execute("SELECT machine FROM box_records")}
    if missing := sorted(owners - reserved):
        raise SystemExit(f"owner trees without a paired reservation: {missing}")
PY
# The copies are now immutable; release the layout lock before the slow part.
flock -u 9 2>/dev/null || true
log "live copy staged; services were never paused"

tar -czf "$STAGE/$DATA_NAME" --numeric-owner --acls --xattrs \
    --exclude="$data_root/.broker/browser-vault" \
    -C "$STAGE" "$data_root"
tar -tzf "$STAGE/$DATA_NAME" >/dev/null

box_members=()
for tree in "$BOX_COPY"/[0-9]*; do
    [[ -d "$tree" && ! -L "$tree" ]] || continue
    box_members+=("$box_root/$(basename "$tree")")
done
tar -czf "$STAGE/$BOX_NAME" --numeric-owner --acls --xattrs \
    -C "$BOX_STAGE" --no-recursion "$box_root" \
    --recursion "${box_members[@]}"
python3 "$HERE/box-store-archive.py" "$STAGE/$BOX_NAME"

# The manifest is the commit record for a single generation. Both local
# members and the manifest are fsynced before the remote upload.
python3 "$HERE/backup-pair.py" create "$STAGE/$DATA_NAME" \
    "$STAGE/$BOX_NAME" "$STAGE/$MANIFEST_NAME"
log "paired generation staged durably at $STAGE"

# Remote commit is the manifest, uploaded only after both immutable members.
# A failed upload leaves the local generation for repair and publishes no
# manifest that could make a partial pair look restorable.
rclone copyto --contimeout 60s --timeout 3600s "$STAGE/$DATA_NAME" "$DEST/$DATA_NAME" || exit 3
rclone copyto --contimeout 60s --timeout 3600s "$STAGE/$BOX_NAME" "$DEST/$BOX_NAME" || exit 3
rclone copyto --contimeout 60s --timeout 60s "$STAGE/$MANIFEST_NAME" "$DEST/$MANIFEST_NAME" || exit 3
log "owner-box paired backup complete: $MANIFEST_NAME"

# Prune only complete older pairs. Remove each manifest first, so an
# interrupted prune can never advertise an incomplete generation.
if rclone lsf "$DEST/" > "$STAGE/remote-names"; then
    if python3 - "$STAGE/remote-names" \
            "${BACKUP_RETAIN_DAILY:-7}" "${BACKUP_RETAIN_WEEKLY:-4}" \
            "${BACKUP_RETAIN_MONTHLY:-6}" "$HERE/../scripts" \
            > "$STAGE/victims" <<'PY'
import re
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[5])
from backup_prune import _apply_retention_one_tier  # noqa: E402

names = set(Path(sys.argv[1]).read_text().splitlines())
data = []
for name in names:
    match = re.fullmatch(r"tinyassets-data-(\d{4}-\d\d-\d\dT\d\d-\d\d-\d\dZ)\.tar\.gz", name)
    if match and all(f"tinyassets-{prefix}-{match[1]}{suffix}" in names for prefix, suffix in (
        ("box-store", ".tar.gz"), ("pair", ".json"),
    )):
        data.append(name)
for name in _apply_retention_one_tier(data, *map(int, sys.argv[2:5])):
    print(name.removeprefix("tinyassets-data-").removesuffix(".tar.gz"))
PY
    then
        while IFS= read -r victim; do
            [[ -n "$victim" ]] || continue
            if rclone deletefile "$DEST/tinyassets-pair-${victim}.json"; then
                rclone deletefile "$DEST/tinyassets-data-${victim}.tar.gz" || \
                    log "WARN: old data member retained: $victim"
                rclone deletefile "$DEST/tinyassets-box-store-${victim}.tar.gz" || \
                    log "WARN: old box member retained: $victim"
            else
                log "WARN: old pair manifest could not be pruned: $victim"
            fi
        done < "$STAGE/victims"
    else
        log "WARN: owner-box pair retention calculation failed"
    fi
else
    log "WARN: owner-box pair retention listing failed"
fi

rm -rf -- "$DATA_COPY" "$BOX_STAGE"
BOX_STAGE=""
rm -f -- "$STAGE/$DATA_NAME" "$STAGE/$BOX_NAME" "$STAGE/$MANIFEST_NAME" \
    "$STAGE/remote-names" "$STAGE/victims"
rmdir -- "$STAGE"
STAGE=""
