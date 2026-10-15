#!/usr/bin/env bash
# Install the complete TinyAssets host-uptime systemd/runtime closure.

set -euo pipefail

SOURCE_ROOT="${TINYASSETS_SOURCE_ROOT:-/opt/tinyassets}"
RUNTIME_ROOT="${TINYASSETS_RUNTIME_ROOT:-/opt/tinyassets-host-uptime}"
SYSTEMD_DIR="${TINYASSETS_SYSTEMD_DIR:-/etc/systemd/system}"
JOURNALD_DIR="${TINYASSETS_JOURNALD_DIR:-/etc/systemd/journald.conf.d}"
SUDOERS_DIR="${TINYASSETS_SUDOERS_DIR:-/etc/sudoers.d}"
LOCK_DIR="${TINYASSETS_LOCK_DIR:-/run/lock}"
SOURCE_SHA="${TINYASSETS_SOURCE_SHA:-}"
ALLOW_TEST_ROOTS="${TINYASSETS_ALLOW_TEST_ROOTS:-0}"
ACTIVE_WAIT_SECONDS="${TINYASSETS_ACTIVE_WAIT_SECONDS:-180}"
LOCK_WAIT_SECONDS="${TINYASSETS_LOCK_WAIT_SECONDS:-300}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-/usr/bin/systemctl}"
VISUDO_BIN="${VISUDO_BIN:-/usr/sbin/visudo}"
TINYASSETS_USER="${TINYASSETS_USER:-tinyassets}"
PRINT_MANIFEST="${TINYASSETS_PRINT_MANIFEST:-0}"
# Runtime releases kept after a successful install, including the live one.
KEEP_RELEASES=5

TIMERS=(
    tinyassets-watchdog.timer
    daemon-watchdog.timer
    tinyassets-backup.timer
    tinyassets-prune.timer
    tinyassets-disk-watch.timer
    tinyassets-ship-logs.timer
)
SERVICES=(
    tinyassets-watchdog.service
    daemon-watchdog.service
    tinyassets-backup.service
    tinyassets-prune.service
    tinyassets-disk-watch.service
    tinyassets-ship-logs.service
)
UNIT_FILES=(
    tinyassets-watchdog.service tinyassets-watchdog.timer
    daemon-watchdog.service daemon-watchdog.timer
    tinyassets-backup.service tinyassets-backup.timer
    tinyassets-prune.service tinyassets-prune.timer
    tinyassets-disk-watch.service tinyassets-disk-watch.timer
    tinyassets-ship-logs.service tinyassets-ship-logs.timer
)
RUNTIME_FILES=(
    deploy/daemon-watchdog.sh
    deploy/backup.sh
    deploy/backup-owner-box.sh
    deploy/backup-pair.py
    deploy/box-store-check.sh
    deploy/box-store-archive.py
    deploy/box-store-quotas.py
    deploy/ship-logs.sh
    scripts/__init__.py
    scripts/_canary_common.py
    scripts/watchdog.py
    scripts/mcp_public_canary.py
    scripts/disk_watch.py
    scripts/disk_autoprune.py
    scripts/daemon_image_retention.py
    scripts/backup_snapshot_retention.py
    scripts/rotate_run_transcripts.py
    scripts/backup_ship_gh.py
    scripts/backup_prune.py
    tinyassets/__init__.py
    tinyassets/ttl_memo.py
    tinyassets/storage/__init__.py
    tinyassets/storage/rotation.py
)
# Release root plus deploy/, scripts/, tinyassets/, and tinyassets/storage/.
EXPECTED_RELEASE_DIRECTORY_COUNT=5
# journald drop-in. This is the retention policy for the host journal, which
# deploy/compose.yml makes the durable home of every container's output, so the
# installer owns it the same way it owns the units: converged on every install,
# checked by the idempotence gate, never left to a one-time bootstrap.
JOURNALD_DROPIN_SOURCE="deploy/journald-tinyassets.conf"
JOURNALD_DROPIN_NAME="tinyassets.conf"

if [[ "${PRINT_MANIFEST}" == "1" ]]; then
    printf '%s\n' deploy/install-host-uptime-services.sh
    for unit in "${UNIT_FILES[@]}"; do
        printf 'deploy/%s\n' "${unit}"
    done
    printf '%s\n' "${RUNTIME_FILES[@]}"
    printf '%s\n' "${JOURNALD_DROPIN_SOURCE}"
    exit 0
fi

log() {
    printf '[host-uptime-install] %s\n' "$*"
}

fail() {
    log "ERROR: $*"
    exit 1
}

for command in \
    awk chmod cmp date find flock grep install ln mktemp mv readlink realpath \
    rm sha256sum sleep sort stat wc
do
    command -v "${command}" >/dev/null 2>&1 || fail "missing command: ${command}"
done
[[ -x "${SYSTEMCTL_BIN}" ]] || fail "systemctl is not executable: ${SYSTEMCTL_BIN}"
[[ -x "${VISUDO_BIN}" ]] || fail "visudo is not executable: ${VISUDO_BIN}"
[[ "${SOURCE_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "TINYASSETS_SOURCE_SHA must be a full lowercase commit SHA"
[[ "${ACTIVE_WAIT_SECONDS}" =~ ^[0-9]+$ ]] || fail "TINYASSETS_ACTIVE_WAIT_SECONDS must be a non-negative integer"
[[ "${LOCK_WAIT_SECONDS}" =~ ^[0-9]+$ ]] || fail "TINYASSETS_LOCK_WAIT_SECONDS must be a non-negative integer"

for path in "${SOURCE_ROOT}" "${RUNTIME_ROOT}" "${SYSTEMD_DIR}" "${JOURNALD_DIR}" \
    "${SUDOERS_DIR}" "${LOCK_DIR}"
do
    [[ "${path}" == /* ]] || fail "all roots must be absolute: ${path}"
done

SOURCE_ROOT="$(realpath -e "${SOURCE_ROOT}")"
RUNTIME_ROOT="$(realpath -m "${RUNTIME_ROOT}")"
SYSTEMD_DIR="$(realpath -m "${SYSTEMD_DIR}")"
JOURNALD_DIR="$(realpath -m "${JOURNALD_DIR}")"
SUDOERS_DIR="$(realpath -m "${SUDOERS_DIR}")"
LOCK_DIR="$(realpath -m "${LOCK_DIR}")"
JOURNALD_DROPIN="${JOURNALD_DIR}/${JOURNALD_DROPIN_NAME}"
# Records the drop-in hash journald actually LOADED. Lives under the runtime
# root rather than in journald.conf.d, so systemd never sees it.
JOURNALD_STAMP="${RUNTIME_ROOT}/.journald-applied"

if [[ "${ALLOW_TEST_ROOTS}" != "1" ]]; then
    [[ "${EUID}" -eq 0 ]] || fail "production installation must run as root"
    [[ "${RUNTIME_ROOT}" == "/opt/tinyassets-host-uptime" ]] || fail "unsafe production runtime root"
    [[ "${SYSTEMD_DIR}" == "/etc/systemd/system" ]] || fail "unsafe production systemd root"
    [[ "${JOURNALD_DIR}" == "/etc/systemd/journald.conf.d" ]] \
        || fail "unsafe production journald drop-in root"
    [[ "${SUDOERS_DIR}" == "/etc/sudoers.d" ]] || fail "unsafe production sudoers root"
    [[ "${LOCK_DIR}" == "/run/lock" ]] || fail "unsafe production lock root"
else
    [[ "${SYSTEMCTL_BIN}" != "/usr/bin/systemctl" && "${SYSTEMCTL_BIN}" != "systemctl" ]] \
        || fail "test roots require a non-live systemctl binary"
fi

for unit in "${UNIT_FILES[@]}"; do
    source_file="${SOURCE_ROOT}/deploy/${unit}"
    [[ -f "${source_file}" && ! -L "${source_file}" ]] \
        || fail "missing or unsafe unit source: ${source_file}"
done
for relative in "${RUNTIME_FILES[@]}" "${JOURNALD_DROPIN_SOURCE}"; do
    source_file="${SOURCE_ROOT}/${relative}"
    [[ -f "${source_file}" && ! -L "${source_file}" ]] \
        || fail "missing or unsafe runtime source: ${source_file}"
done

JOURNALD_HASH="$(
    sha256sum "${SOURCE_ROOT}/${JOURNALD_DROPIN_SOURCE}" | awk '{print $1}'
)"
[[ -n "${JOURNALD_HASH}" ]] || fail "cannot hash the journald drop-in"

mkdir -p "${LOCK_DIR}"
lock_key="$(printf '%s' "${RUNTIME_ROOT}" | sha256sum | awk '{print $1}')"
LOCK_FILE="${LOCK_DIR}/tinyassets-host-uptime-${lock_key}.lock"
exec 9> "${LOCK_FILE}"
flock -w "${LOCK_WAIT_SECONDS}" 9 \
    || fail "timed out waiting for installer lock on ${RUNTIME_ROOT}"

mkdir -p "${RUNTIME_ROOT}/releases" "${SYSTEMD_DIR}" "${SUDOERS_DIR}"
TRANSACTION_DIR="$(mktemp -d "${RUNTIME_ROOT}/.install.XXXXXX")"
UNITS_MUTATED=0
POINTER_SWITCHED=0
RELEASE_BACKED_UP=0
TIMERS_PAUSED=0
SUCCESS=0
PREVIOUS_CURRENT=""
RELEASE_BACKUP=""
SUDOERS_TEMP=""
ABSENT_UNITS="${TRANSACTION_DIR}/absent-units"
mkdir -p "${TRANSACTION_DIR}/units" "${TRANSACTION_DIR}/unit-backup"
: > "${ABSENT_UNITS}"

restore_timers() {
    "${SYSTEMCTL_BIN}" enable --now "${TIMERS[@]}" >/dev/null 2>&1 || true
}

rollback() {
    local unit
    if [[ "${POINTER_SWITCHED}" -eq 1 ]]; then
        if [[ -n "${PREVIOUS_CURRENT}" ]]; then
            ln -s "${PREVIOUS_CURRENT}" "${RUNTIME_ROOT}/.current.rollback.$$"
            mv -Tf "${RUNTIME_ROOT}/.current.rollback.$$" "${RUNTIME_ROOT}/current"
        else
            rm -f "${RUNTIME_ROOT}/current"
        fi
    fi
    if [[ "${UNITS_MUTATED}" -eq 1 ]]; then
        for unit in "${UNIT_FILES[@]}"; do
            if [[ -f "${TRANSACTION_DIR}/unit-backup/${unit}" ]]; then
                install -m 0644 "${TRANSACTION_DIR}/unit-backup/${unit}" \
                    "${SYSTEMD_DIR}/.${unit}.rollback.$$"
                mv -f "${SYSTEMD_DIR}/.${unit}.rollback.$$" "${SYSTEMD_DIR}/${unit}"
            elif grep -qxF "${unit}" "${ABSENT_UNITS}"; then
                rm -f "${SYSTEMD_DIR}/${unit}"
            fi
        done
    fi
    if [[ "${RELEASE_BACKED_UP}" -eq 1 ]]; then
        rm -rf -- "${RELEASE_DIR}"
        mv -- "${RELEASE_BACKUP}" "${RELEASE_DIR}"
    fi
    "${SYSTEMCTL_BIN}" daemon-reload >/dev/null 2>&1 || true
    [[ "${TIMERS_PAUSED}" -eq 0 ]] || restore_timers
}

on_exit() {
    local rc=$?
    local unit
    trap - EXIT
    if [[ "${SUCCESS}" -ne 1 ]]; then
        rollback
    fi
    for unit in "${UNIT_FILES[@]}"; do
        rm -f -- \
            "${SYSTEMD_DIR}/.${unit}.new.$$" \
            "${SYSTEMD_DIR}/.${unit}.rollback.$$"
    done
    rm -f -- \
        "${RUNTIME_ROOT}/.current.new.$$" \
        "${RUNTIME_ROOT}/.current.rollback.$$" \
        "${JOURNALD_DIR}/.${JOURNALD_DROPIN_NAME}.new.$$" \
        "${JOURNALD_STAMP}.new.$$"
    [[ -z "${SUDOERS_TEMP}" ]] || rm -f -- "${SUDOERS_TEMP}"
    rm -rf -- "${TRANSACTION_DIR}"
    exit "${rc}"
}
trap on_exit EXIT

# ---------------------------------------------------------------------------
# Idempotence gate -- runs BEFORE the first mutation on purpose.
#
# Every merge used to run the whole transaction below, stopping every timer and
# restarting them at the end, even when it installed byte-identical files. That
# is not free. daemon-watchdog.timer declares OnBootSec=2min, an elapse long
# past on a host that has been up for days, so systemd fires
# daemon-watchdog.service the instant the timer is started again. That run
# lands seconds after the deploy recreated the daemon container, while the
# fresh container has not written a heartbeat yet -- and daemon-watchdog.sh
# restarts the daemon when the heartbeat is stale. Result: every merge killed
# in-flight user turns TWICE, once for the deploy's own recreate and once for a
# watchdog tick this script provoked.
#
# So: a merge that installs nothing must change nothing. Content is the test,
# not the commit sha -- releases are named with SOURCE_SHA, so a byte-identical
# bundle from a new merge is a new directory name and would otherwise always
# look like work to do.
# ---------------------------------------------------------------------------
# Content alone is NOT the test. Cross-family review of the first draft of this
# gate (#3989 round 1) reproduced four drifts that the transaction below repairs
# and a content-only gate declared "already current" forever:
#
#   * the sudoers file at 0666 instead of 0440, and the same file replaced by a
#     SYMLINK with identical content. `[[ -f ]]` follows a symlink, so both
#     passed. Either silently disables tinyassets-watchdog's `sudo -n systemctl
#     restart` path (scripts/watchdog.py:185) -- sudo refuses a rule that is
#     group/world-writable or not a regular file, so the daemon loses its
#     recovery lever with nothing in the logs;
#   * daemon-watchdog.service at 0666. That unit runs as root, so a
#     world-writable copy is a local-root foothold;
#   * `current` repointed at a directory OUTSIDE releases/. Content matched, so
#     the gate blessed a pointer the installer never wrote.
#
# So every property the transaction converges has to be a property the gate
# checks: content, mode, owner, regular-file-ness, and the pointer's shape. A
# missed property is not a missed optimisation -- it is drift that now persists
# forever, which is strictly worse than the redundant work this gate removes.
exact_file() { # exact_file <path> <expected-mode>
    local path="$1" expected="$2"
    # A symlink is never acceptable, whatever it points at: the installer writes
    # regular files, and -f alone would follow the link and compare the target.
    #
    # Belt and braces, stated because it is not obvious: the mode test below
    # ALSO rejects a symlink on its own, since GNU stat does not dereference and
    # a symlink's own mode is always 777. Mutation-checked -- removing only this
    # line leaves the symlink case caught, and removing only the mode line does
    # too; both have to go before it slips through. Keep both anyway: the
    # redundancy is free, and relying on 777-never-being-expected would be a
    # coincidence rather than a rule.
    [[ -f "${path}" && ! -L "${path}" ]] || return 1
    # Mode AND owner in one stat: `install -m` resets both, so a file the
    # transaction would rewrite must not read as already-exact. Owner drift has
    # no test -- chown to another uid needs root, which the harness does not
    # have. The expected values are pinned from the other side instead: if they
    # did not match what the transaction writes, the gate would never fire and
    # test_repeat_install_with_identical_content_stops_no_timer would fail.
    [[ "$(stat -c '%a %u' "${path}")" == "${expected} ${EUID}" ]] || return 1
}

# True when journald is running the policy this source tree declares. Compares
# the recorded HASH, not mtimes: reinstalling identical bytes must not look like
# a new policy, and a policy whose restart failed must not look applied.
journald_applied() {
    local recorded
    [[ -f "${JOURNALD_STAMP}" && ! -L "${JOURNALD_STAMP}" ]] || return 1
    recorded="$(cat -- "${JOURNALD_STAMP}" 2>/dev/null)" || return 1
    [[ "${recorded}" == "${JOURNALD_HASH}" ]]
}

current_release_is_exact() {
    local current_dir link_target relative mode installed_file unit timer

    # The pointer itself, before anything it points at: it must be the exact
    # relative form the installer writes, `releases/<40 hex>-<16 hex>`.
    #
    # Pinning the release-id SHAPE rather than a character class is the point. A
    # class of "safe-looking" characters still admits `releases/.` and
    # `releases/..` -- both are made only of accepted characters -- which resolve
    # to the releases directory and to RUNTIME_ROOT itself. Neither is a tree
    # this script ever wrote. The shape is knowable exactly: RELEASE_ID is
    # "${SOURCE_SHA}-${manifest_hash:0:16}" and SOURCE_SHA is already required to
    # be ^[0-9a-f]{40}$ above, so this can never reject a release the transaction
    # produced.
    [[ -L "${RUNTIME_ROOT}/current" ]] || return 1
    link_target="$(readlink -- "${RUNTIME_ROOT}/current")" || return 1
    [[ "${link_target}" =~ ^releases/[0-9a-f]{40}-[0-9a-f]{16}$ ]] || return 1
    current_dir="${RUNTIME_ROOT}/${link_target}"
    [[ -d "${current_dir}" && ! -L "${current_dir}" ]] || return 1

    for relative in "${RUNTIME_FILES[@]}"; do
        mode=644
        [[ "${relative}" == *.sh ]] && mode=755
        installed_file="${current_dir}/${relative}"
        exact_file "${installed_file}" "${mode}" || return 1
        cmp -s "${SOURCE_ROOT}/${relative}" "${installed_file}" || return 1
    done
    # Same exactness the repair path below demands: no extra files, no extra
    # directories, nothing that is neither.
    [[ "$(find "${current_dir}" -type f -print | wc -l)" -eq "${#RUNTIME_FILES[@]}" ]] \
        || return 1
    [[ "$(find "${current_dir}" -type d -print | wc -l)" \
        -eq "${EXPECTED_RELEASE_DIRECTORY_COUNT}" ]] || return 1
    if find "${current_dir}" ! -type f ! -type d -print -quit | grep -q .; then
        return 1
    fi

    # Units are installed 0644 and are read by root systemd.
    for unit in "${UNIT_FILES[@]}"; do
        exact_file "${SYSTEMD_DIR}/${unit}" 644 || return 1
        cmp -s "${SOURCE_ROOT}/deploy/${unit}" "${SYSTEMD_DIR}/${unit}" || return 1
    done

    # The journald drop-in decides how much of the host journal survives, and
    # the journal is where container output now lives (deploy/compose.yml).
    exact_file "${JOURNALD_DROPIN}" 644 || return 1
    cmp -s "${SOURCE_ROOT}/${JOURNALD_DROPIN_SOURCE}" "${JOURNALD_DROPIN}" || return 1
    # Bytes on disk are not the same thing as policy in effect. journald reads
    # this file at START, so an install whose restart failed leaves the correct
    # bytes and the OLD policy -- and a byte-only gate would then report
    # convergence forever, which is exactly what a cross-family review found
    # (output/codex-log-durability-review.md §2; the first version of this even
    # had a comment claiming the next install would retry, contradicted by its own
    # code). The stamp is written only after a restart that succeeded, so "not
    # applied" is drift the transaction below repairs.
    journald_applied || return 1

    # The sudoers rule is part of what this script installs, and sudo itself
    # rejects it unless it is a regular file at 0440.
    exact_file "${SUDOERS_DIR}/tinyassets-watchdog" 440 || return 1
    printf '%s ALL=(root) NOPASSWD:/usr/bin/systemctl restart tinyassets-daemon.service\n' \
        "${TINYASSETS_USER}" \
        | cmp -s - "${SUDOERS_DIR}/tinyassets-watchdog" || return 1

    # A disabled or dead timer is drift the transaction must repair, even when
    # every file already matches. Both halves are load-bearing: `enable --now`
    # converges enabled AND active, so a timer that is enabled but stopped is
    # still not what this script leaves behind.
    for timer in "${TIMERS[@]}"; do
        "${SYSTEMCTL_BIN}" is-enabled "${timer}" >/dev/null 2>&1 || return 1
        "${SYSTEMCTL_BIN}" is-active "${timer}" >/dev/null 2>&1 || return 1
    done
    return 0
}

if current_release_is_exact; then
    log "already current: runtime, units, sudoers and timers all match; nothing touched"
    SUCCESS=1
    exit 0
fi

TIMERS_PAUSED=1
for timer in "${TIMERS[@]}"; do
    load_state="$(
        "${SYSTEMCTL_BIN}" show --property=LoadState --value "${timer}"
    )" || fail "cannot inspect timer load state: ${timer}"
    case "${load_state}" in
        loaded|masked)
            "${SYSTEMCTL_BIN}" stop "${timer}"
            ;;
        not-found)
            ;;
        *)
            fail "unsafe timer load state for ${timer}: ${load_state}"
            ;;
    esac
done
wait_started="$(date +%s)"
while true; do
    active=0
    for service in "${SERVICES[@]}"; do
        active_state="$(
            "${SYSTEMCTL_BIN}" show --property=ActiveState --value "${service}"
        )" || fail "cannot inspect service active state: ${service}"
        case "${active_state}" in
            active|activating|reloading|deactivating)
                active=1
                ;;
            inactive|failed)
                ;;
            *)
                fail "unsafe service active state for ${service}: ${active_state}"
                ;;
        esac
    done
    [[ "${active}" -eq 0 ]] && break
    now="$(date +%s)"
    (( now - wait_started < ACTIVE_WAIT_SECONDS )) \
        || fail "active uptime service did not quiesce within ${ACTIVE_WAIT_SECONDS}s"
    sleep 1
done

manifest_hash="$(
    cd "${SOURCE_ROOT}"
    sha256sum "${RUNTIME_FILES[@]}" | sha256sum | awk '{print $1}'
)"
RELEASE_ID="${SOURCE_SHA}-${manifest_hash:0:16}"
RELEASE_DIR="${RUNTIME_ROOT}/releases/${RELEASE_ID}"
if [[ -d "${RELEASE_DIR}" ]]; then
    release_valid=1
    for relative in "${RUNTIME_FILES[@]}"; do
        mode=644
        [[ "${relative}" == *.sh ]] && mode=755
        installed_file="${RELEASE_DIR}/${relative}"
        if [[ ! -f "${installed_file}" || -L "${installed_file}" ]] \
            || ! cmp -s "${SOURCE_ROOT}/${relative}" "${installed_file}" \
            || [[ "$(stat -c %a "${installed_file}")" != "${mode}" ]]; then
            release_valid=0
            break
        fi
    done
    installed_file_count="$(
        find "${RELEASE_DIR}" -type f -print | wc -l
    )"
    [[ "${installed_file_count}" -eq "${#RUNTIME_FILES[@]}" ]] \
        || release_valid=0
    installed_dir_count="$(
        find "${RELEASE_DIR}" -type d -print | wc -l
    )"
    [[ "${installed_dir_count}" -eq "${EXPECTED_RELEASE_DIRECTORY_COUNT}" ]] \
        || release_valid=0
    if find "${RELEASE_DIR}" ! -type f ! -type d -print -quit | grep -q .; then
        release_valid=0
    fi
    if [[ "${release_valid}" -ne 1 ]]; then
        RELEASE_BACKUP="${TRANSACTION_DIR}/release-backup"
        mv -- "${RELEASE_DIR}" "${RELEASE_BACKUP}"
        RELEASE_BACKED_UP=1
    fi
fi
if [[ ! -d "${RELEASE_DIR}" ]]; then
    RELEASE_STAGE="${TRANSACTION_DIR}/release"
    for relative in "${RUNTIME_FILES[@]}"; do
        mode=0644
        [[ "${relative}" == *.sh ]] && mode=0755
        install -D -m "${mode}" "${SOURCE_ROOT}/${relative}" "${RELEASE_STAGE}/${relative}"
    done
    mv -- "${RELEASE_STAGE}" "${RELEASE_DIR}"
    RELEASE_BACKED_UP=0
fi

for unit in "${UNIT_FILES[@]}"; do
    install -m 0644 "${SOURCE_ROOT}/deploy/${unit}" "${TRANSACTION_DIR}/units/${unit}"
    if [[ -f "${SYSTEMD_DIR}/${unit}" ]]; then
        install -m 0644 "${SYSTEMD_DIR}/${unit}" "${TRANSACTION_DIR}/unit-backup/${unit}"
    else
        printf '%s\n' "${unit}" >> "${ABSENT_UNITS}"
    fi
done

SUDOERS_CANDIDATE="${TRANSACTION_DIR}/tinyassets-watchdog.sudoers"
printf '%s ALL=(root) NOPASSWD:/usr/bin/systemctl restart tinyassets-daemon.service\n' \
    "${TINYASSETS_USER}" > "${SUDOERS_CANDIDATE}"
chmod 0440 "${SUDOERS_CANDIDATE}"
"${VISUDO_BIN}" -cf "${SUDOERS_CANDIDATE}" >/dev/null

for unit in "${UNIT_FILES[@]}"; do
    install -m 0644 "${TRANSACTION_DIR}/units/${unit}" "${SYSTEMD_DIR}/.${unit}.new.$$"
    mv -f "${SYSTEMD_DIR}/.${unit}.new.$$" "${SYSTEMD_DIR}/${unit}"
    UNITS_MUTATED=1
done

# journald retention. Installed atomically like the units; journald is only
# restarted when the bytes actually changed, for the same reason the idempotence
# gate exists -- a restart per deploy is churn, and journald drops whatever is in
# flight across one.
JOURNALD_CHANGED=0
if ! cmp -s "${SOURCE_ROOT}/${JOURNALD_DROPIN_SOURCE}" "${JOURNALD_DROPIN}" \
    || [[ -L "${JOURNALD_DROPIN}" ]] \
    || [[ "$(stat -c %a "${JOURNALD_DROPIN}" 2>/dev/null || echo none)" != "644" ]]
then
    mkdir -p "${JOURNALD_DIR}" || fail "cannot create journald drop-in dir ${JOURNALD_DIR}"
    install -m 0644 "${SOURCE_ROOT}/${JOURNALD_DROPIN_SOURCE}" \
        "${JOURNALD_DIR}/.${JOURNALD_DROPIN_NAME}.new.$$" \
        || fail "cannot stage journald drop-in"
    mv -f "${JOURNALD_DIR}/.${JOURNALD_DROPIN_NAME}.new.$$" "${JOURNALD_DROPIN}" \
        || fail "cannot install journald drop-in"
    JOURNALD_CHANGED=1
    log "journald drop-in installed: ${JOURNALD_DROPIN}"
fi

SUDOERS_TEMP="$(mktemp "${SUDOERS_DIR}/.tinyassets-watchdog.XXXXXX")"
install -m 0440 "${SUDOERS_CANDIDATE}" "${SUDOERS_TEMP}"
"${VISUDO_BIN}" -cf "${SUDOERS_TEMP}" >/dev/null
mv -f "${SUDOERS_TEMP}" "${SUDOERS_DIR}/tinyassets-watchdog"

if [[ -L "${RUNTIME_ROOT}/current" ]]; then
    PREVIOUS_CURRENT="$(readlink "${RUNTIME_ROOT}/current")"
elif [[ -e "${RUNTIME_ROOT}/current" ]]; then
    fail "runtime current path exists but is not a symlink"
fi
ln -s "releases/${RELEASE_ID}" "${RUNTIME_ROOT}/.current.new.$$"
mv -Tf "${RUNTIME_ROOT}/.current.new.$$" "${RUNTIME_ROOT}/current"
POINTER_SWITCHED=1

for relative in "${RUNTIME_FILES[@]}"; do
    installed_file="${RUNTIME_ROOT}/current/${relative}"
    cmp -s "${SOURCE_ROOT}/${relative}" "${installed_file}" \
        || fail "installed runtime hash mismatch: ${relative}"
done
for unit in "${UNIT_FILES[@]}"; do
    cmp -s "${SOURCE_ROOT}/deploy/${unit}" "${SYSTEMD_DIR}/${unit}" \
        || fail "installed unit hash mismatch: ${unit}"
done

"${SYSTEMCTL_BIN}" daemon-reload
"${SYSTEMCTL_BIN}" enable --now "${TIMERS[@]}"
for timer in "${TIMERS[@]}"; do
    "${SYSTEMCTL_BIN}" is-enabled "${timer}" >/dev/null
    "${SYSTEMCTL_BIN}" is-active "${timer}" >/dev/null
done

# Apply the journald policy. Not fatal: journald keeps running on its previous
# configuration if this fails, so the timers converged above are worth more than
# this restart, and the next install retries it (the gate compares bytes on
# disk, so a failed restart still reads as converged -- which is why this logs
# loudly rather than silently).
if [[ "${JOURNALD_CHANGED}" -eq 1 ]] || ! journald_applied; then
    if "${SYSTEMCTL_BIN}" restart systemd-journald; then
        # Stamp AFTER the restart, never before: it asserts "journald has loaded
        # these bytes", and writing it on the strength of having copied a file
        # would make the claim unfalsifiable.
        if printf '%s\n' "${JOURNALD_HASH}" > "${JOURNALD_STAMP}.new.$$" \
            && mv -f "${JOURNALD_STAMP}.new.$$" "${JOURNALD_STAMP}"
        then
            log "journald restarted and applied ${JOURNALD_DROPIN_NAME}"
        else
            rm -f -- "${JOURNALD_STAMP}.new.$$"
            log "WARNING: journald restarted but the applied-stamp was not written"
        fi
    else
        # Drop any stale stamp, so the next install retries instead of inheriting
        # a claim that this policy is live.
        rm -f -- "${JOURNALD_STAMP}"
        log "WARNING: systemd-journald restart FAILED; the drop-in is on disk but"
        log "WARNING: journald still runs its previous policy. The next install"
        log "WARNING: retries it, because the applied-stamp was not written."
    fi
fi

TIMERS_PAUSED=0
SUCCESS=1
log "converged ${#TIMERS[@]} timers at ${RELEASE_ID}"

# Bound the content-addressed release history. Every deploy installs a new
# release directory (257 of them on production on 2026-09-24). Keep the live
# target plus the newest releases for a manual pointer rollback; touch only
# directories whose names have the release-id shape. Best-effort: the install
# above already succeeded, so a prune failure is logged, never fatal.
prune_old_releases() {
    local live name path kept=0 pruned=0
    local -a ordered=()
    live="$(readlink "${RUNTIME_ROOT}/current")"
    live="${live#releases/}"
    while IFS= read -r name; do
        [[ -n "${name}" ]] && ordered+=("${name}")
    done < <(
        for path in "${RUNTIME_ROOT}/releases"/*; do
            name="${path##*/}"
            [[ -d "${path}" && ! -L "${path}" ]] || continue
            [[ "${name}" =~ ^[0-9a-f]{40}-[0-9a-f]{16}$ ]] || continue
            printf '%s %s\n' "$(stat -c %Y "${path}")" "${name}"
        done | sort -rn | awk '{print $2}'
    )
    for name in "${ordered[@]}"; do
        [[ "${name}" == "${live}" || "${name}" == "${RELEASE_ID}" ]] && continue
        if (( kept < KEEP_RELEASES - 1 )); then
            kept=$((kept + 1))
            continue
        fi
        rm -rf -- "${RUNTIME_ROOT:?}/releases/${name}" && pruned=$((pruned + 1))
    done
    log "pruned ${pruned} old runtime release(s); kept current plus ${kept} newest"
}
prune_old_releases || log "WARNING: runtime release pruning failed; install is unaffected"
