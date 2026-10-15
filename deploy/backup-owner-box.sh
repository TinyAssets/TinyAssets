#!/usr/bin/env bash
# One acknowledged writer barrier for a live owner-box nightly backup.
# Called only by backup.sh after it identifies a committed owner-box layout.
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
FROZEN=0
STOPPED=0
LOCKED=0

log() { echo "[backup $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }
running() { [[ "$(docker inspect --format '{{.State.Running}}' "$1")" == true ]]; }

resume() {
    if [[ "$FROZEN" == 1 ]]; then
        xfs_freeze -u "$BOX_DIR" || return 1
        FROZEN=0
    fi
    if [[ "$LOCKED" == 1 ]]; then
        flock -u 9 || return 1
        LOCKED=0
    fi
    if [[ "$STOPPED" == 1 ]]; then
        # The root host must be ready before the broker, and the broker before
        # the frontend. A failed restart remains a failed backup invocation.
        systemctl is-active --quiet boxhostd.service \
            || systemctl start boxhostd.service || return 1
        running tinyassets-broker || docker start tinyassets-broker || return 1
        running tinyassets-daemon || docker start tinyassets-daemon || return 1
        systemctl is-active --quiet boxhostd.service \
            && running tinyassets-broker && running tinyassets-daemon || return 1
        STOPPED=0
    fi
}

finish() {
    local rc=$?
    trap - EXIT
    if ! resume; then
        log "CRITICAL: writer barrier release failed; inspect host and containers"
        rc=2
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

# Serialize deploy, watchdog and backup mutations. The parent has released its
# shared layout lock, avoiding a lock inversion with deployment/migration.
exec 7>>/var/lock/tinyassets-backup-owner-box.lock
flock -n 7 || { log "ERROR: another owner-box backup is active"; exit 2; }
exec 8>>/var/lock/tinyassets-host-mutation.lock
flock -x -w 600 8 || { log "ERROR: host mutation lock unavailable"; exit 2; }
running tinyassets-daemon && running tinyassets-broker \
    && systemctl is-active --quiet boxhostd.service || {
    log "ERROR: all three owner-box writers must be running before the barrier"; exit 2;
}

# Stopping the frontend interrupts active sessions/ticks and prevents new
# admissions. The broker stop drains its SQLite writers; boxhostd.close stops
# every owner sandbox and closes the root receipt journal. Each stop command
# acknowledges process exit; the census below is the final guest assertion.
STOPPED=1
docker stop -t 30 tinyassets-daemon || exit 2
docker stop -t 30 tinyassets-broker || exit 2
systemctl stop boxhostd.service || exit 2

exec 9>>"$DATA_DIR/.layout.lock"
flock -x -w 60 9 || { log "ERROR: exclusive layout lock unavailable"; exit 2; }
LOCKED=1
if ! python3 - "$DATA_DIR/.layout.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    marker = json.load(source)
assert marker.get("state") == "stable" and marker.get("box_layout") == "owner-box"
PY
then
    log "ERROR: layout changed before the writer barrier"; exit 2
fi
if [[ -n "$(docker ps -q --filter "volume=${BACKUP_VOLUME:-tinyassets-data}")" ]] \
        || systemctl is-active --quiet boxhostd.service; then
    log "ERROR: a volume writer survived the barrier"; exit 2
fi
python3 "$HERE/box-store-quotas.py" --snapshot-data "$DATA_DIR" --quiescent || {
    log "ERROR: root receipts or guest census refused the barrier"; exit 2;
}
log "writer barrier acknowledged: frontend, broker, host and guests stopped"

mkdir -p "$STAGE_ROOT"
chmod 0700 "$STAGE_ROOT"
STAGE="$(mktemp -d "$STAGE_ROOT/.tinyassets-pair-${TS}.XXXXXX")"
chmod 0700 "$STAGE"
tar -czf "$STAGE/$DATA_NAME" --numeric-owner --acls --xattrs \
    --exclude="$(basename "$DATA_DIR")/.broker/browser-vault" \
    -C "$(dirname "$DATA_DIR")" "$(basename "$DATA_DIR")"
tar -tzf "$STAGE/$DATA_NAME" >/dev/null

box_root="$(basename "$BOX_DIR")"
box_members=()
for tree in "$BOX_DIR"/[0-9]*; do
    [[ -d "$tree" && ! -L "$tree" ]] || continue
    box_members+=("$box_root/$(basename "$tree")")
done
xfs_freeze -f "$BOX_DIR"
FROZEN=1
tar -czf "$STAGE/$BOX_NAME" --numeric-owner --acls --xattrs \
    -C "$(dirname "$BOX_DIR")" --no-recursion "$box_root" \
    --recursion "${box_members[@]}"
xfs_freeze -u "$BOX_DIR"
FROZEN=0
python3 "$HERE/box-store-archive.py" "$STAGE/$BOX_NAME"

# The manifest is the commit record for a single generation. Both local
# members and the manifest are fsynced before writers are released.
python3 "$HERE/backup-pair.py" create "$STAGE/$DATA_NAME" \
    "$STAGE/$BOX_NAME" "$STAGE/$MANIFEST_NAME"
log "paired generation staged durably at $STAGE"
resume || { log "ERROR: writer barrier could not be released"; exit 2; }
flock -u 8
exec 8>&-

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

rm -f -- "$STAGE/$DATA_NAME" "$STAGE/$BOX_NAME" "$STAGE/$MANIFEST_NAME" \
    "$STAGE/remote-names" "$STAGE/victims"
rmdir -- "$STAGE"
STAGE=""
