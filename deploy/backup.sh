#!/usr/bin/env bash
# Nightly C3 shared-layer backup. BACKUP_MODE=snapshot is ONLY the one-shot
# stopped old-layout migration backup. Owner durability uses C2 volume snapshots.
# Configuration: BACKUP_DEST, BACKUP_VOLUME; retention defaults to 7/4/6.
# DRY_RUN=1 prints configuration without any mutation.

set -euo pipefail

BACKUP_VOLUME="${BACKUP_VOLUME:-tinyassets-data}"
BOX_STORE_DIR="${BOX_STORE_DIR:-/var/lib/boxhostd/owners}"
# Browser owner keys live on a separate broker-only volume, deliberately without
# an offsite copy. Losing it requires signing in again. Legacy keys are excluded
# below while the broker migrates them on first use.
if [[ "${BACKUP_VOLUME}" == "tinyassets-browser-keys" ]]; then
    echo "ERROR: browser key material must never enter the data backup destination" >&2
    exit 2
fi
BACKUP_RETAIN_DAILY="${BACKUP_RETAIN_DAILY:-7}"
BACKUP_RETAIN_WEEKLY="${BACKUP_RETAIN_WEEKLY:-4}"
BACKUP_RETAIN_MONTHLY="${BACKUP_RETAIN_MONTHLY:-6}"
DRY_RUN="${DRY_RUN:-0}"
BACKUP_LOG="${BACKUP_LOG:-/var/log/tinyassets-backup.log}"

# Docker-internal volume mountpoint. Fall back to `docker volume inspect`
# when Docker uses a non-default storage root.
VOLUME_DIR="/var/lib/docker/volumes/${BACKUP_VOLUME}/_data"

log() {
    # Declared and assigned separately: `local x="$(cmd)"` masks the
    # command's return value in `local`'s own exit status (SC2155).
    local msg
    msg="[backup $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
    echo "${msg}"
    echo "${msg}" >> "${BACKUP_LOG}" 2>/dev/null || true
}

# ----- 1. validate env --------------------------------------------------

if [[ "${DRY_RUN}" != "1" ]]; then
    if [[ -z "${BACKUP_DEST:-}" ]]; then
        log "ERROR: BACKUP_DEST is not set"
        log "Set it in /etc/tinyassets/env, e.g.: BACKUP_DEST=s3://my-bucket/tinyassets-backups"
        exit 1
    fi
fi

if [[ "${DRY_RUN}" == "1" ]]; then
    log "DRY_RUN=1 — skipping volume locate, tar, upload, and prune. No mutations."
    log "  BACKUP_DEST=${BACKUP_DEST:-<unset>}"
    log "  BACKUP_VOLUME=${BACKUP_VOLUME}"
    log "  BOX_STORE_DIR=${BOX_STORE_DIR}"
    log "  retention: daily=${BACKUP_RETAIN_DAILY} weekly=${BACKUP_RETAIN_WEEKLY} monthly=${BACKUP_RETAIN_MONTHLY}"
    exit 0
fi

# ----- 2. locate volume -------------------------------------------------

if [[ ! -d "${VOLUME_DIR}" ]]; then
    VOLUME_DIR="$(docker volume inspect --format '{{ .Mountpoint }}' "${BACKUP_VOLUME}" 2>/dev/null || echo '')"
    if [[ -z "${VOLUME_DIR}" || ! -d "${VOLUME_DIR}" ]]; then
        log "ERROR: volume ${BACKUP_VOLUME} not found on disk"
        exit 2
    fi
fi

log "source: ${VOLUME_DIR}"
log "dest:   ${BACKUP_DEST}"

# The data-layout lock (tinyassets/storage_layout.py). The server holds it
# SHARED for its lifetime; a storage migration takes it EXCLUSIVELY. Holding it
# shared here means a backup never reads a half-migrated volume, and a migration
# never starts while a backup is reading. Waiting up to 10 min, then failing
# loudly, beats copying a moving target.
if ! command -v flock >/dev/null 2>&1; then
    log "ERROR: flock not found; refusing to read the volume without the layout lock"
    exit 2
fi
# O_CREAT on a service-owned lock in the sticky data root is denied even to
# root by fs.protected_regular. Preserve existing inodes and lock read-only.
if [[ ! -e "${VOLUME_DIR}/.layout.lock" ]]; then
    (set -o noclobber; : > "${VOLUME_DIR}/.layout.lock")
fi
if [[ ! -f "${VOLUME_DIR}/.layout.lock" || -L "${VOLUME_DIR}/.layout.lock" ]]; then
    log "ERROR: layout lock is not a regular file"
    exit 2
fi
exec 9<"${VOLUME_DIR}/.layout.lock"
# Root creates it on a fresh volume; the non-root server must still open it.
chmod 0666 "${VOLUME_DIR}/.layout.lock" 2>/dev/null || true
if ! flock -s -w 600 9; then
    log "ERROR: could not take the shared layout lock in 600s (a migration is running?)"
    exit 2
fi
# A crashed migration releases the lock but leaves its "migrating" marker: that
# volume is half-migrated and must not become a backup anyone restores.
if [[ -e "${VOLUME_DIR}/.layout.json" ]] && ! python3 - "${VOLUME_DIR}/.layout.json" <<'LAYOUT_PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        doc = json.load(handle)
except (OSError, ValueError):
    sys.exit(1)
sys.exit(0 if isinstance(doc, dict) and doc.get("state") == "stable" else 1)
LAYOUT_PY
then
    log "ERROR: ${VOLUME_DIR}/.layout.json is not stable (a migration did not finish); refusing to back up a half-migrated volume"
    exit 2
fi

# C2/C3: nightly durability is a shared-layer archive, never an owner-store copy.
if [[ "${BACKUP_MODE:-}" != snapshot ]]; then
    exec python3 "$(dirname "$(realpath "$0")")/backup-platform.py" "$VOLUME_DIR" "$BACKUP_DEST"
fi
# The one-shot old-layout migration is the only owner-content backup exception.
if [[ -f "$VOLUME_DIR/.layout.json" ]] && python3 - "$VOLUME_DIR/.layout.json" <<'PY'
import json, sys
sys.exit(0 if json.load(open(sys.argv[1])).get("box_layout") == "owner-box" else 1)
PY
then
    log "ERROR: owner durability requires per-owner snapshots; old-layout backup refused"
    exit 2
fi

TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"

box_archive() {  # $1 = archive path; $2 = remote name
    local path="$1" name="$2" box_root tree member
    if [[ ! -e "${BOX_STORE_DIR}" ]]; then
        if [[ "${BACKUP_REQUIRE_BOX_STORE:-0}" == 1 ]]; then
            log "ERROR: required box store is absent"
            return 2
        fi
        return 0
    fi
    if ! "$(dirname "$(realpath "$0")")/box-store-check.sh" "${BOX_STORE_DIR}"; then
        log "ERROR: box store exists but is not mounted with XFS project quota"
        return 2
    fi
    box_root="$(basename "${BOX_STORE_DIR}")"
    local box_members=()
    for tree in "${BOX_STORE_DIR}"/[0-9]*; do
        [[ -d "$tree" && ! -L "$tree" ]] || continue
        member="$(basename "$tree")"
        [[ "$member" =~ ^[0-9]+$ ]] && box_members+=("${box_root}/${member}")
    done
    # Freeze and thaw in a subshell so every error and signal thaws the store.
    if ! (
        frozen=0
        thaw_box() {
            if [[ "$frozen" == 1 ]]; then
                xfs_freeze -u "${BOX_STORE_DIR}"
            fi
        }
        trap thaw_box EXIT
        trap 'exit 129' HUP
        trap 'exit 130' INT
        trap 'exit 143' TERM
        xfs_freeze -f "${BOX_STORE_DIR}"
        frozen=1
        tar -czf "$path" --numeric-owner --acls --xattrs \
            -C "$(dirname "${BOX_STORE_DIR}")" \
            --no-recursion "$box_root" --recursion "${box_members[@]}"
        xfs_freeze -u "${BOX_STORE_DIR}"
        frozen=0
    ); then
        log "ERROR: box-store archive failed"
        return 2
    fi
    if ! python3 "$(dirname "$(realpath "$0")")/box-store-archive.py" "$path"; then
        log "ERROR: box-store archive validation failed"
        return 2
    fi
    if ! rclone copyto --contimeout 60s --timeout 900s \
            "$path" "${BACKUP_DEST}/${name}"; then
        log "ERROR: box-store upload failed"
        return 3
    fi
    log "  box-store upload OK: ${name}"
}

# ----- 2b. cutover snapshot (BACKUP_MODE=snapshot) ----------------------
# The owner-split window's rollback point (docs/ops/owner-split-cutover-runbook.md):
# a full-volume tar taken with every writer stopped, numeric owners, ACLs and
# xattrs kept, a local copy and an upload, then its id, sha256, size and name
# count. The count must match a fresh census of the volume.
if [[ "${BACKUP_MODE:-}" == "snapshot" ]]; then
    # The nightly unit and sudo deploy caller run this as host root to read
    # broker/owner 0700 trees. Tar and the inventory both fail on unreadable
    # entries, so a less privileged manual caller cannot publish a partial copy.
    if ! running="$(docker ps -q --filter "volume=${BACKUP_VOLUME}")"; then
        log "ERROR: cannot verify that volume writers are stopped"
        exit 2
    fi
    if [[ -n "${running}" ]]; then
        log "ERROR: snapshot needs every container on ${BACKUP_VOLUME} stopped"
        exit 2
    fi
    SNAPSHOT_DIR="${BACKUP_SNAPSHOT_DIR:-/var/backups/tinyassets}"
    SNAPSHOT_NAME="tinyassets-snapshot-${TS}.tar.gz"
    SNAPSHOT_PATH="${SNAPSHOT_DIR}/${SNAPSHOT_NAME}"
    mkdir -p "${SNAPSHOT_DIR}"
    chmod 0700 "${SNAPSHOT_DIR}"
    log "creating cutover snapshot ${SNAPSHOT_PATH}..."
    if ! tar -czf "${SNAPSHOT_PATH}" --numeric-owner --acls --xattrs \
            --exclude='_data/.broker/browser-vault' \
            -C "$(dirname "${VOLUME_DIR}")" "$(basename "${VOLUME_DIR}")"; then
        log "ERROR: snapshot tar failed"
        rm -f "${SNAPSHOT_PATH}"
        exit 2
    fi
    if ! names="$(python3 - "${VOLUME_DIR}" "${SNAPSHOT_PATH}" <<'INVENTORY_PY'
import os
import re
import stat
import sys
import tarfile
from collections import Counter

root, archive = sys.argv[1:]
# Runtime relay sockets cannot be represented in tar or restored as live
# endpoints. Match the daemon's role_relays.directory names; all other sockets
# still fail the census. Their parent directories remain in the archive.
relay = re.compile(r"\.universe-sidecars/[A-Za-z0-9_-]{1,128}/"
                   r"(?:egress-[0-9]+|engine-[0-9]+-[a-f0-9]{12}|"
                   r"ta-[a-f0-9]{32}|registry-[a-f0-9]{32})\.sock")
expected = []
def fail_walk(error):
    raise error
for parent, directories, files in os.walk(root, onerror=fail_walk):
    if os.path.relpath(parent, root) == '.broker':
        directories[:] = [name for name in directories if name != 'browser-vault']
    for name in directories + files:
        path = os.path.join(parent, name)
        relative = os.path.relpath(path, root)
        if stat.S_ISSOCK(os.lstat(path).st_mode):
            if relay.fullmatch(relative):
                continue
            raise SystemExit('unrecognized socket in snapshot inventory: ' + ascii(relative))
        expected.append(relative)
with tarfile.open(archive, 'r:gz') as handle:
    actual = [member.name.removeprefix('_data/').rstrip('/') for member in handle
              if member.name.rstrip('/') != '_data']
missing = Counter(expected) - Counter(actual)
extra = Counter(actual) - Counter(expected)
if missing or extra:
    for label, difference in (('missing from archive', missing),
                              ('extra in archive', extra)):
        if difference:
            names = sorted(difference.elements())
            print(f'{label} ({len(names)}): ' + ', '.join(ascii(name) for name in names[:20])
                  + (' ...' if len(names) > 20 else ''), file=sys.stderr)
    raise SystemExit(1)
print(len(expected))
INVENTORY_PY
    )"; then
        log "ERROR: snapshot does not match the restorable inventory"
        rm -f -- "${SNAPSHOT_PATH}" "${SNAPSHOT_PATH}.sha256"
        exit 2
    fi
    sha="$(sha256sum "${SNAPSHOT_PATH}" | cut -d' ' -f1)"
    bytes="$(stat -c %s "${SNAPSHOT_PATH}")"
    printf '%s  %s\n' "${sha}" "${SNAPSHOT_NAME}" > "${SNAPSHOT_PATH}.sha256"
    BOX_SNAPSHOT_NAME="tinyassets-box-store-${TS}.tar.gz"
    BOX_SNAPSHOT_PATH="${SNAPSHOT_DIR}/${BOX_SNAPSHOT_NAME}"
    if box_archive "${BOX_SNAPSHOT_PATH}" "${BOX_SNAPSHOT_NAME}"; then
        if [[ -f "${BOX_SNAPSHOT_PATH}" ]]; then
            box_sha="$(sha256sum "${BOX_SNAPSHOT_PATH}" | cut -d' ' -f1)"
            printf '%s  %s\n' "$box_sha" "$BOX_SNAPSHOT_NAME" > "${BOX_SNAPSHOT_PATH}.sha256"
        fi
    else
        box_rc=$?
        rm -f -- "${SNAPSHOT_PATH}" "${SNAPSHOT_PATH}.sha256" \
            "${BOX_SNAPSHOT_PATH}" "${BOX_SNAPSHOT_PATH}.sha256"
        exit "$box_rc"
    fi
    if ! rclone copyto --contimeout 60s --timeout 3600s \
            "${SNAPSHOT_PATH}" "${BACKUP_DEST}/${SNAPSHOT_NAME}"; then
        log "ERROR: snapshot upload failed (local copy kept)"
        exit 3
    fi
    log "SNAPSHOT id=${SNAPSHOT_NAME} sha256=${sha} bytes=${bytes} names=${names}"
    exit 0
fi
