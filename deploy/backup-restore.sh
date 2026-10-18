#!/usr/bin/env bash
# backup-restore.sh — stage and restore tinyassets-data from a full snapshot.
#
# The script restores data only. It stops containers that mount the selected
# volume, but it never starts services; the caller owns startup and health
# verification after inspecting the retained pre-restore directory.
#
# Usage:
#   sudo bash deploy/backup-restore.sh
#   sudo bash deploy/backup-restore.sh --timestamp=2026-04-20T02-00-00Z
#   sudo bash deploy/backup-restore.sh --list
#   BACKUP_FILE=/tmp/tinyassets-data-....tar.gz sudo bash deploy/backup-restore.sh
#   DRY_RUN=1 sudo bash deploy/backup-restore.sh
#
# Required for remote mode:
#   BACKUP_DEST      rclone destination used by backup.sh
#
# Optional:
#   BACKUP_FILE      absolute, readable non-symlink local full archive; bypasses rclone
#   BACKUP_VOLUME    Docker volume name (default: tinyassets-data)
#   DRY_RUN          "1" to skip all mutations
#   BACKUP_LOG       log file (default: /var/log/tinyassets-backup.log)
#
# Exit codes:
#   0  restore complete (or DRY_RUN=1); caller starts services
#   1  configuration, argument, path, or lock failure
#   2  archive not found
#   3  remote download failed
#   4  archive validation, extraction, stop, or swap failed
#   5  reserved legacy restart failure; never emitted
#   6  disaster restore completed; acknowledged backup-to-restore loss; credentials and operations fenced

set -euo pipefail

BACKUP_VOLUME="${BACKUP_VOLUME:-tinyassets-data}"
BACKUP_FILE="${BACKUP_FILE:-}"
BOX_BACKUP_FILE="${BOX_BACKUP_FILE:-}"
BOX_STORE_DIR="${BOX_STORE_DIR:-/var/lib/boxhostd/owners}"
DRY_RUN="${DRY_RUN:-0}"
BACKUP_LOG="${BACKUP_LOG:-/var/log/tinyassets-backup.log}"

TIMESTAMP_ARG=""
LIST_MODE=0
DISASTER_RESTORE=0
ACKNOWLEDGE_FACTS=0
REHEARSAL_PROVIDER=0
DOWNLOAD_DIR=""
STAGE_DIR=""
BOX_STAGE=""
VOLUME_PARENT=""
BOX_OLD=""
BOX_SWAP_ACTIVE=0
box_old_names=()
box_new_names=()

for arg in "$@"; do
    case "${arg}" in
        --timestamp=*) TIMESTAMP_ARG="${arg#--timestamp=}" ;;
        --list)        LIST_MODE=1 ;;
        --disaster-restore) DISASTER_RESTORE=1 ;;
        --acknowledge-unrecoverable-facts) ACKNOWLEDGE_FACTS=1 ;;
        --rehearsal-provider) REHEARSAL_PROVIDER=1 ;;
        *) echo "Unknown argument: ${arg}" >&2; exit 1 ;;
    esac
done

if [[ "$REHEARSAL_PROVIDER" == 1 && ( "$DISASTER_RESTORE" != 1 ||
        ! -f /run/tinyassets-rehearsal/rehearsal_restore_provider.py ||
        -L /run/tinyassets-rehearsal/rehearsal_restore_provider.py ) ]]; then
    echo "Recorded providers require the harness-only mount and disaster restore" >&2
    exit 1
fi

if [[ "$DISASTER_RESTORE" != "$ACKNOWLEDGE_FACTS" ]]; then
    echo "Disaster restore requires both --disaster-restore and --acknowledge-unrecoverable-facts" >&2
    exit 1
fi
if [[ "$DISASTER_RESTORE" == 1 && ( -n "$TIMESTAMP_ARG" || "$LIST_MODE" == 1 ) ]]; then
    echo "Disaster restore requires the newest available backup, without --timestamp or --list" >&2
    exit 1
fi

log() {
    # Declared and assigned separately: `local x="$(cmd)"` masks the
    # command's return value in `local`'s own exit status (SC2155).
    local msg
    msg="[restore $(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
    echo "${msg}"
    echo "${msg}" >> "${BACKUP_LOG}" 2>/dev/null || true
}

rollback_box() {
    [[ "$BOX_SWAP_ACTIVE" -eq 1 ]] || return 0
    log "rolling back box-store swap"
    mkdir -p -- "$BOX_STAGE"
    for name in "${box_new_names[@]}"; do
        if [[ -e "${BOX_STORE_DIR}/${name}" ]]; then
            mv -- "${BOX_STORE_DIR}/${name}" "${BOX_STAGE}/" || \
                log "CRITICAL: failed to retain restored box tree ${name}"
        fi
    done
    for name in "${box_old_names[@]}"; do
        if [[ -e "${BOX_OLD}/${name}" ]]; then
            mv -- "${BOX_OLD}/${name}" "${BOX_STORE_DIR}/" || \
                log "CRITICAL: failed to restore original box tree ${name}"
        fi
    done
    if [[ "${quota_restore:-0}" == 1 ]]; then
        if ! python3 "$(dirname "$(realpath "$0")")/box-store-quotas.py" \
                "${BOX_STORE_DIR}" "${BOX_STORE_DIR}" \
                "${VOLUME_DIR}/.broker/state/owner-identities.db"; then
            log "CRITICAL: failed to restore original box quota limits"
        fi
    fi
    rmdir -- "$BOX_OLD" 2>/dev/null || true
    BOX_SWAP_ACTIVE=0
}

cleanup() {
    rollback_box
    if [[ -n "${BOX_STAGE}" && -d "${BOX_STAGE}" ]]; then
        case "${BOX_STAGE}" in
            "${BOX_STORE_DIR}"/.tinyassets-box-restore-stage.*) rm -rf -- "${BOX_STAGE}" ;;
            *) log "ERROR: refusing to clean unexpected box stage ${BOX_STAGE}" ;;
        esac
    fi
    if [[ -n "${STAGE_DIR}" && -d "${STAGE_DIR}" ]]; then
        case "${STAGE_DIR}" in
            "${VOLUME_PARENT}"/.tinyassets-restore-stage.*)
                rm -rf -- "${STAGE_DIR}"
                ;;
            *)
                log "ERROR: refusing to clean unexpected stage path ${STAGE_DIR}"
                ;;
        esac
    fi
    if [[ -n "${DOWNLOAD_DIR}" && -d "${DOWNLOAD_DIR}" ]]; then
        case "${DOWNLOAD_DIR}" in
            "${TMPDIR:-/tmp}"/.tinyassets-restore-download.*)
                rm -rf -- "${DOWNLOAD_DIR}"
                ;;
            *)
                log "ERROR: refusing to clean unexpected download path ${DOWNLOAD_DIR}"
                ;;
        esac
    fi
}
trap cleanup EXIT

if [[ ! "${BACKUP_VOLUME}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
    log "ERROR: invalid Docker volume name ${BACKUP_VOLUME}"
    exit 1
fi

is_full_archive_name() {
    [[ "$1" =~ ^tinyassets-(data|snapshot)-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}Z[.]tar[.]gz$ ]]
}

# ----- 1. select local or remote source --------------------------------

if [[ -n "${BACKUP_FILE}" ]]; then
    if [[ "${LIST_MODE}" -eq 1 || -n "${TIMESTAMP_ARG}" ]]; then
        log "ERROR: BACKUP_FILE cannot be combined with --list or --timestamp"
        exit 1
    fi
    if [[ "${BACKUP_FILE}" != /* || -L "${BACKUP_FILE}" \
            || ! -f "${BACKUP_FILE}" || ! -r "${BACKUP_FILE}" ]]; then
        log "ERROR: BACKUP_FILE must be an absolute readable non-symlink regular file"
        exit 2
    fi
    TAR_PATH="${BACKUP_FILE}"
    TAR_NAME="$(basename "${BACKUP_FILE}")"
    log "target local archive: ${TAR_PATH}"
else
    if [[ -z "${BACKUP_DEST:-}" ]]; then
        log "ERROR: BACKUP_DEST is not set"
        log "Set it in /etc/tinyassets/env or set absolute BACKUP_FILE."
        exit 1
    fi

    if [[ "${LIST_MODE}" -eq 1 ]]; then
        log "available full archives at ${BACKUP_DEST}:"
        rclone lsf --format tp "${BACKUP_DEST}/" 2>/dev/null \
            | awk -F';' '$2 ~ /^tinyassets-data-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}Z[.]tar[.]gz$/ {print "  " $2}' \
            | sort -r
        exit 0
    fi

    if [[ -n "${TIMESTAMP_ARG}" ]]; then
        if [[ ! "${TIMESTAMP_ARG}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}Z$ ]]; then
            log "ERROR: invalid UTC backup timestamp ${TIMESTAMP_ARG}"
            exit 1
        fi
        TAR_NAME="tinyassets-data-${TIMESTAMP_ARG}.tar.gz"
    else
        TAR_NAME="$(
            rclone lsf --format tp "${BACKUP_DEST}/" 2>/dev/null \
                | awk -F';' '$2 ~ /^tinyassets-data-[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}-[0-9]{2}-[0-9]{2}Z[.]tar[.]gz$/ {print $2}' \
                | sort -r \
                | awk 'NR==1 {print}'
        )"
        if [[ -z "${TAR_NAME}" ]]; then
            log "ERROR: no full-volume archives found at ${BACKUP_DEST}/"
            exit 2
        fi
    fi
    if ! is_full_archive_name "${TAR_NAME}"; then
        log "ERROR: unsafe or non-full archive name ${TAR_NAME}"
        exit 2
    fi

    log "target remote archive: ${TAR_NAME}"
    if ! rclone ls "${BACKUP_DEST}/${TAR_NAME}" > /dev/null 2>&1; then
        log "ERROR: archive not found: ${BACKUP_DEST}/${TAR_NAME}"
        exit 2
    fi

    if [[ "${DRY_RUN}" == "1" ]]; then
        log "DRY_RUN=1 — would restore ${TAR_NAME} into volume ${BACKUP_VOLUME}. No mutations."
        exit 0
    fi

    DOWNLOAD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/.tinyassets-restore-download.XXXXXX")"
    TAR_PATH="${DOWNLOAD_DIR}/${TAR_NAME}"
    log "downloading ${TAR_NAME}..."
    if ! rclone copyto --contimeout 60s --timeout 900s \
            "${BACKUP_DEST}/${TAR_NAME}" "${TAR_PATH}"; then
        log "ERROR: rclone download failed"
        exit 3
    fi
    log "  download OK ($(stat -c %s "${TAR_PATH}" 2>/dev/null || echo '?') bytes)"
fi

if [[ "$DISASTER_RESTORE" == 1 && -n "$BACKUP_FILE" ]]; then
    # Local availability is the selected archive's directory. Never silently
    # ignore a newer backup (including a damaged/incomplete newest generation).
    if ! python3 - "$BACKUP_FILE" <<'PY'
import pathlib
import re
import sys

selected = pathlib.Path(sys.argv[1])
pattern = re.compile(r"tinyassets-(?:data|snapshot)-(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z)[.]tar[.]gz$")
match = pattern.fullmatch(selected.name)
times = [m[1] for path in selected.parent.iterdir() if (m := pattern.fullmatch(path.name))]
if not match or match[1] != max(times):
    raise SystemExit("disaster restore requires the newest available local backup")
PY
    then
        log "ERROR: disaster backup selection refused"
        exit 2
    fi
fi

if [[ "${DRY_RUN}" == "1" ]]; then
    log "DRY_RUN=1 — would restore ${TAR_NAME} into volume ${BACKUP_VOLUME}. No mutations."
    exit 0
fi

# An installed box store gets an archive with the exact data timestamp.
BOX_TAR_PATH=""
BOX_REMOTE=""
if [[ -e "${BOX_STORE_DIR}" ]]; then
    if ! "$(dirname "$(realpath "$0")")/box-store-check.sh" "${BOX_STORE_DIR}"; then
        log "ERROR: box store is not mounted with XFS project quota"
        exit 1
    fi
    if ! is_full_archive_name "${TAR_NAME}"; then
        log "ERROR: paired restore requires a timestamped full archive"
        exit 2
    fi
    BOX_NAME="${TAR_NAME/tinyassets-data-/tinyassets-box-store-}"
    BOX_NAME="${BOX_NAME/tinyassets-snapshot-/tinyassets-box-store-}"
    if [[ -n "${BACKUP_FILE}" ]]; then
        BOX_TAR_PATH="${BOX_BACKUP_FILE:-$(dirname "${BACKUP_FILE}")/${BOX_NAME}}"
        if [[ "${BOX_TAR_PATH}" != /* || -L "${BOX_TAR_PATH}" || ! -f "${BOX_TAR_PATH}" ]]; then
            log "ERROR: paired box-store archive missing"
            exit 2
        fi
    else
        BOX_REMOTE="${BACKUP_DEST}/${BOX_NAME}"
        if ! rclone ls "${BOX_REMOTE}" > /dev/null 2>&1; then
            log "ERROR: paired box-store archive missing"
            exit 2
        fi
    fi
fi

# ----- 2. resolve and guard the Docker volume --------------------------

VOLUME_DIR="$(
    docker volume inspect --format '{{ .Mountpoint }}' "${BACKUP_VOLUME}" \
        2>/dev/null || true
)"
if [[ -z "${VOLUME_DIR}" ]]; then
    log "  volume ${BACKUP_VOLUME} not found; creating..."
    docker volume create "${BACKUP_VOLUME}" >/dev/null
    VOLUME_DIR="$(
        docker volume inspect --format '{{ .Mountpoint }}' "${BACKUP_VOLUME}"
    )"
fi
if [[ ! -d "${VOLUME_DIR}" ]]; then
    log "ERROR: resolved volume directory does not exist: ${VOLUME_DIR}"
    exit 1
fi
VOLUME_DIR="$(cd "${VOLUME_DIR}" && pwd -P)"
VOLUME_PARENT="$(dirname "${VOLUME_DIR}")"
if [[ "${VOLUME_DIR}" != /* || "$(basename "${VOLUME_DIR}")" != "_data" || "${VOLUME_PARENT}" == "/" ]]; then
    log "ERROR: refusing unsafe volume path ${VOLUME_DIR}"
    exit 1
fi

# The lock lives in the per-volume parent, so different Docker volumes do not
# block each other while a second restore of this volume fails before mutation.
exec 9> "${VOLUME_PARENT}/.tinyassets-restore.lock"
if ! flock -n 9; then
    log "ERROR: restore already in progress for ${VOLUME_DIR}"
    exit 1
fi

# ----- 3. validate and stage before stopping any container -------------

if ! python3 - "${TAR_PATH}" <<'PY'
import pathlib
import sys
import tarfile

archive = sys.argv[1]
try:
    with tarfile.open(archive, "r:gz") as tf:
        members = tf.getmembers()
except (OSError, tarfile.TarError) as exc:
    print(f"archive unreadable: {exc}", file=sys.stderr)
    raise SystemExit(1)

if not members:
    print("archive is empty", file=sys.stderr)
    raise SystemExit(1)

symlinks = {member.name.rstrip("/") for member in members if member.issym()}
for member in members:
    path = pathlib.PurePosixPath(member.name)
    parts = path.parts
    if path.is_absolute() or not parts or parts[0] != "_data":
        print(f"unsafe archive root: {member.name!r}", file=sys.stderr)
        raise SystemExit(1)
    if any(part in ("", ".", "..") for part in parts):
        print(f"unsafe archive path: {member.name!r}", file=sys.stderr)
        raise SystemExit(1)
    if len(parts) == 1 and not member.isdir():
        print(f"archive root is not a directory: {member.name!r}", file=sys.stderr)
        raise SystemExit(1)
    if any(str(parent) in symlinks for parent in path.parents):
        print(f"archive descends through a symlink: {member.name!r}", file=sys.stderr)
        raise SystemExit(1)
    if member.islnk():
        target = pathlib.PurePosixPath(member.linkname)
        if (target.is_absolute() or not target.parts or target.parts[0] != "_data"
                or ".." in target.parts or str(target) in symlinks):
            print(f"unsafe archive hardlink: {member.name!r}", file=sys.stderr)
            raise SystemExit(1)
    elif not (member.isfile() or member.isdir() or member.issym()):
        print(f"unsupported archive member type: {member.name!r}", file=sys.stderr)
        raise SystemExit(1)
PY
then
    log "ERROR: archive member validation failed"
    exit 4
fi
if ! tar -tzf "${TAR_PATH}" > /dev/null; then
    log "ERROR: archive integrity check failed"
    exit 4
fi

STAGE_DIR="$(mktemp -d "${VOLUME_PARENT}/.tinyassets-restore-stage.XXXXXX")"
if ! tar -xzf "${TAR_PATH}" -C "${STAGE_DIR}" --strip-components=1 \
        --numeric-owner --acls --xattrs; then
    log "ERROR: archive staging extract failed"
    exit 4
fi
if ! chown --reference="${VOLUME_DIR}" "${STAGE_DIR}" \
        || ! chmod --reference="${VOLUME_DIR}" "${STAGE_DIR}"; then
    log "ERROR: failed to preserve volume-root ownership or mode on staging"
    exit 4
fi
log "  archive validated and staged at ${STAGE_DIR}"

# A committed owner-box backup is one generation only when its manifest binds
# the exact data and box bytes. Remote members are downloaded before hashing;
# a missing manifest is an incomplete backup, never a legacy fallback.
if ! staged_layout="$(python3 - "${STAGE_DIR}/.layout.json" <<'PY'
import json
import pathlib
import sys

marker = pathlib.Path(sys.argv[1])
document = json.loads(marker.read_text()) if marker.exists() else {}
layout = document.get("box_layout", "owner-split")
if layout not in ("owner-split", "owner-box"):
    raise SystemExit("unknown staged box layout")
print(layout)
PY
)"; then
    log "ERROR: staged layout marker is invalid"
    exit 4
fi
if [[ "$staged_layout" == owner-box ]]; then
    if [[ -z "${BOX_NAME:-}" ]]; then
        log "ERROR: owner-box generation has no box member"
        exit 4
    fi
    pair_ts="${TAR_NAME#tinyassets-data-}"
    pair_ts="${pair_ts#tinyassets-snapshot-}"
    pair_ts="${pair_ts%.tar.gz}"
    pair_name="tinyassets-pair-${pair_ts}.json"
    if [[ -n "${BOX_REMOTE}" ]]; then
        pair_path="${DOWNLOAD_DIR}/${pair_name}"
        BOX_TAR_PATH="${DOWNLOAD_DIR}/${BOX_NAME}"
        if ! rclone copyto "${BACKUP_DEST}/${pair_name}" "$pair_path" \
                || ! rclone copyto "$BOX_REMOTE" "$BOX_TAR_PATH"; then
            log "ERROR: paired owner-box manifest or member download failed"
            exit 4
        fi
        BOX_REMOTE=""
    else
        pair_path="${PAIR_MANIFEST_FILE:-$(dirname "$TAR_PATH")/$pair_name}"
    fi
    if [[ -L "$pair_path" || ! -f "$pair_path" ]] \
            || ! python3 "$(dirname "$(realpath "$0")")/backup-pair.py" verify \
                "$TAR_PATH" "$BOX_TAR_PATH" "$pair_path"; then
        log "ERROR: owner-box generation manifest refused the archive pair"
        exit 4
    fi
fi
if [[ "$DISASTER_RESTORE" == 1 && "$staged_layout" != owner-box ]]; then
    log "ERROR: disaster restore requires a verified owner-box backup pair"
    exit 4
fi

if [[ -z "${BOX_NAME:-}" ]]; then
    log "ERROR: paired restore requires a readable live owner store and its backup member"
    exit 4
fi

if [[ -n "${BOX_NAME:-}" ]]; then
    archive_check="$(dirname "$(realpath "$0")")/box-store-archive.py"
    if [[ -n "${BOX_REMOTE}" ]]; then
        rclone cat "${BOX_REMOTE}" | python3 "${archive_check}" - || {
            log "ERROR: box-store archive validation failed"; exit 4;
        }
    elif ! python3 "${archive_check}" "${BOX_TAR_PATH}"; then
        log "ERROR: box-store archive validation failed"
        exit 4
    fi
    BOX_STAGE="$(mktemp -d "${BOX_STORE_DIR}/.tinyassets-box-restore-stage.XXXXXX")"
    if [[ -n "${BOX_REMOTE}" ]]; then
        rclone cat "${BOX_REMOTE}" | tar -xzf - -C "${BOX_STAGE}" \
            --strip-components=1 --numeric-owner --acls --xattrs || {
                log "ERROR: box-store extraction failed"; exit 4;
            }
    else
        if ! tar -xzf "${BOX_TAR_PATH}" -C "${BOX_STAGE}" --strip-components=1 \
                --numeric-owner --acls --xattrs; then
            log "ERROR: box-store extraction failed"
            exit 4
        fi
    fi
    quota_restore=0
    if [[ "$staged_layout" == owner-box ]]; then
        quota_restore=1
    else
        # Older paired backups can carry boxes without a box-layout marker.
        # Only an empty pre-move box archive can be restored without box_records.
        for tree in "${BOX_STAGE}"/[0-9]*; do
            if [[ -e "$tree" || -L "$tree" ]]; then
                quota_restore=1
                break
            fi
        done
    fi
    REGISTRY="${STAGE_DIR}/.broker/state/owner-identities.db"
    if ! python3 "$(dirname "$(realpath "$0")")/box-store-quotas.py" \
            --snapshot-data "${STAGE_DIR}"; then
        log "ERROR: staged owner-box layout is invalid"
        exit 4
    fi
fi

# ----- 4. stop every consumer, then swap with automatic rollback -------

if ! CONTAINER_OUTPUT="$(
    docker ps -q --filter "volume=${BACKUP_VOLUME}"
)"; then
    log "ERROR: failed to enumerate running volume consumers"
    exit 4
fi
containers=()
if [[ -n "${CONTAINER_OUTPUT}" ]]; then
    mapfile -t containers <<< "${CONTAINER_OUTPUT}"
fi

if [[ "${#containers[@]}" -gt 0 ]]; then
    log "stopping ${#containers[@]} container(s) mounting ${BACKUP_VOLUME}..."
    if ! docker stop "${containers[@]}"; then
        log "ERROR: failed to stop all running volume consumers"
        exit 4
    fi
fi
if ! CONTAINER_OUTPUT="$(
    docker ps -q --filter "volume=${BACKUP_VOLUME}"
)"; then
    log "ERROR: failed to verify stopped volume consumers"
    exit 4
fi
if [[ -n "${CONTAINER_OUTPUT}" ]]; then
    log "ERROR: running volume consumers remain after stop"
    exit 4
fi
if [[ -n "${BOX_STAGE}" ]]; then
    if ! systemctl stop boxhostd.service; then
        log "ERROR: failed to stop boxhostd before box-store swap"
        exit 4
    fi
    # Exclude service restarts on both sides of the volume rename. The staged
    # lock becomes the live lock; keep both descriptors until restore exits.
    # Bash <> includes O_CREAT: fs.protected_regular=2 rejects even root when
    # the sticky data root contains a service-owned lock. flock needs no write
    # access. Open existing inodes without O_CREAT; only create missing legacy
    # locks, exclusively, so we never truncate or replace a writer's lock.
    for layout_lock in "${VOLUME_DIR}/.layout.lock" "${STAGE_DIR}/.layout.lock"; do
        if [[ ! -e "$layout_lock" ]]; then
            (set -o noclobber; : > "$layout_lock")
        fi
        if [[ ! -f "$layout_lock" || -L "$layout_lock" ]]; then
            log "ERROR: layout lock is not a regular file"
            exit 4
        fi
    done
    exec 8<"${VOLUME_DIR}/.layout.lock"
    exec 7<"${STAGE_DIR}/.layout.lock"
    if ! flock -n -x 8 || ! flock -n -x 7; then
        log "ERROR: a storage writer still owns the live or staged layout"
        exit 4
    fi
    pair_ts="${TAR_NAME#tinyassets-data-}"
    pair_ts="${pair_ts#tinyassets-snapshot-}"
    pair_ts="${pair_ts%.tar.gz}"
    disaster_args=(--backup-time "$pair_ts")
    if [[ "$DISASTER_RESTORE" == 1 ]]; then
        disaster_args=(--disaster-restore --acknowledge-unrecoverable-facts --backup-time "$pair_ts")
        if [[ "$REHEARSAL_PROVIDER" == 1 ]]; then
            disaster_args+=(--rehearsal-provider)
        fi
    fi
    if ! python3 "$(dirname "$(realpath "$0")")/box-store-quotas.py" \
            --snapshot-data "$STAGE_DIR" --quiescent; then
        log "ERROR: staged owner-box receipt/survivor census refused restore"
        exit 4
    fi
    if [[ "$DISASTER_RESTORE" == 1 ]]; then
        log "WARN restoring newest backup with credential reset, operation fences and erasure reconciliation"
    else
        log "Validating exact pre-traffic paired restore"
    fi
    BOX_OLD="${BOX_STORE_DIR}/.tinyassets-box-restore-old.${BOX_STAGE##*.}"
    # Prepare exact restore or disaster safety changes while both layouts
    # are locked. Any failure leaves both pre-restore volumes intact.
    if ! fact_report="$(python3 "$(dirname "$(realpath "$0")")/box-store-quotas.py" \
            --snapshot-data "${STAGE_DIR}" --restore-from "${VOLUME_DIR}" \
            --cutover-store "${BOX_STORE_DIR}" --restored-store "${BOX_STAGE}" \
            --retained-store "${BOX_OLD}" \
            "${disaster_args[@]}")"; then
        log "ERROR: restore safety preparation refused"
        exit 4
    fi
    if [[ -n "$fact_report" ]]; then
        while IFS= read -r line; do log "$line"; done <<< "$fact_report"
    fi
    # A legacy empty pair still displaces quota-tagged trees after completed cutover.
    if python3 -c 'import json,sys; from pathlib import Path; p=Path(sys.argv[1]); sys.exit(not (p.exists() and json.loads(p.read_text()).get("box_layout") == "owner-box"))' "${VOLUME_DIR}/.layout.json"; then
        quota_restore=1
    fi
    # Credential roots, receipt fences and the report must survive power loss
    # before either prepared filesystem becomes live.
    sync -f "$STAGE_DIR"
    sync -f "$BOX_STAGE"
    mkdir -m 700 -- "$BOX_OLD"
    BOX_SWAP_ACTIVE=1
    for tree in "${BOX_STORE_DIR}"/[0-9]*; do
        [[ -d "$tree" && ! -L "$tree" ]] || continue
        name="$(basename "$tree")"
        [[ "$name" =~ ^[0-9]+$ ]] || continue
        mv -- "$tree" "${BOX_OLD}/"
        box_old_names+=("$name")
    done
    quota_script="$(dirname "$(realpath "$0")")/box-store-quotas.py"
    # A failed owner-split move may have written box trees before creating
    # box_records. The staged pre-move archive has no boxes to install, so
    # retain those trees and their quota state together for rollback/inspection.
    if [[ "$quota_restore" == 1 ]]; then
        if ! python3 "$quota_script" "${BOX_STORE_DIR}" "${BOX_OLD}" \
                "$REGISTRY" --clear; then
            log "ERROR: failed to clear retained box project IDs"
            exit 4
        fi
    fi
    for tree in "${BOX_STAGE}"/[0-9]*; do
        [[ -d "$tree" && ! -L "$tree" ]] || continue
        name="$(basename "$tree")"
        [[ "$name" =~ ^[0-9]+$ ]] || continue
        mv -- "$tree" "${BOX_STORE_DIR}/"
        box_new_names+=("$name")
    done
    if [[ "$quota_restore" == 1 ]]; then
        if ! python3 "$quota_script" "${BOX_STORE_DIR}" "${BOX_STORE_DIR}" "$REGISTRY"; then
            log "ERROR: box-store quota restoration failed"
            exit 4
        fi
    fi
    rmdir -- "$BOX_STAGE"
fi

OLD_DIR="$(mktemp -d "${VOLUME_PARENT}/.tinyassets-restore-old.XXXXXX")"
rmdir -- "${OLD_DIR}"
if ! mv -- "${VOLUME_DIR}" "${OLD_DIR}"; then
    log "ERROR: failed to retain current volume at ${OLD_DIR}"
    exit 4
fi
if ! mv -- "${STAGE_DIR}" "${VOLUME_DIR}"; then
    log "ERROR: failed to install staged volume; rolling original back"
    if ! mv -- "${OLD_DIR}" "${VOLUME_DIR}"; then
        log "CRITICAL: failed to restore original volume from ${OLD_DIR}"
    fi
    exit 4
fi
STAGE_DIR=""
BOX_SWAP_ACTIVE=0
BOX_STAGE=''
sync -f "$VOLUME_PARENT"
if [[ -n "${BOX_NAME:-}" ]]; then sync -f "$BOX_STORE_DIR"; fi

if [[ -n "${BOX_NAME:-}" ]]; then
    if ! python3 "$(dirname "$(realpath "$0")")/box-store-quotas.py" \
            --finish-erasures "$VOLUME_DIR" --cutover-store "$BOX_STORE_DIR"; then
        log "ERROR: published recovery cleanup pending; boxhostd will resume before admission"
        exit 5
    fi
fi

# ----- 5. done — caller verifies, starts, and later removes old data ----

log "restore complete. Data installed at ${VOLUME_DIR}."
log "pre-restore volume retained at ${OLD_DIR} until caller health verification."
if [[ -n "${BOX_NAME:-}" ]]; then
    log "pre-restore box trees retained at ${BOX_OLD} until caller health verification."
fi
log "NEXT — start only the intended service, probe it, then remove ${OLD_DIR}."
if [[ "$DISASTER_RESTORE" == 1 ]]; then
    log "WARN disaster restore completed with unrecoverable post-backup facts; exit status 6."
    exit 6
fi
exit 0
