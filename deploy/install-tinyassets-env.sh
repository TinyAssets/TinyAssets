#!/usr/bin/env bash
# install-tinyassets-env.sh — atomic edit of /etc/tinyassets/env.
#
# Replaces every existing `sudo sed -i ... /etc/tinyassets/env && sudo
# chown root:tinyassets ... && sudo chmod 640 ...` chain (CI workflows +
# any future call site) with a single atomic write that cannot leave
# the file in a wrong-perm state.
#
# Background — `/etc/tinyassets/env` mode-flip class
# ------------------------------------------------
# `sed -i` writes a temp file then `rename(2)`s it over the target. The
# new inode is created with default ownership (root:root) and umask-
# derived mode (typically 0600). Any error or signal between the `sed`
# and the follow-up `chown + chmod` leaves the file unreadable by the
# `tinyassets` user → systemd unit's `ExecStartPre=test -r` fails →
# docker compose silently crash-loops → cloudflared never starts →
# public endpoint dark. Root cause of the 2026-04-21 P0 outage.
#
# `install -m 640 -o root -g tinyassets` writes the new file at the
# target path with the correct owner and perms in a single syscall
# sequence, leaving NO intermediate state with wrong perms. Closes
# RC-1 from `docs/audits/2026-04-25-etc-workflow-env-mode-flip.md`.
#
# Usage (from a CI workflow over SSH)
# -----------------------------------
#   # Set/replace a key — value comes from stdin (multi-line OK):
#   echo "ghcr.io/tinyassets/tinyassets-daemon:abc123" \
#     | ssh "$DROPLET" 'sudo bash -s -- set TINYASSETS_IMAGE' \
#     < deploy/install-tinyassets-env.sh
#
#   # Delete one or more keys (no stdin needed):
#   ssh "$DROPLET" 'sudo bash -s -- delete TINYASSETS_WIKI_PATH' \
#     < deploy/install-tinyassets-env.sh
#
#   # Set an immutable key once; a different existing value fails closed:
#   printf '%s' "$SECRET" | sudo bash install-tinyassets-env.sh set-once KEY
#
#   # Re-render the daemon's copy after a hand edit of /etc/tinyassets/env
#   # (deploy_fail_safe.sh installs this script as /usr/local/sbin/tinyassets-env):
#   sudo tinyassets-env render-daemon-env
#
# The daemon's own env file
# -------------------------
# /etc/tinyassets/env is the box's whole secret store and compose's
# interpolation source: the tunnel token and the log-shipping token reach their
# sidecars from it, and an account-wide DigitalOcean token has lived in it. The
# daemon must hold none of those (docs/concerns/
# 2026-10-02-platform-secrets-in-daemon-env.md), so it loads
# /etc/tinyassets/daemon.env instead: the same file minus DAEMON_FORBIDDEN_ENV.
# Every `set` / `set-once` / `delete` of the main file re-renders it, so the two
# cannot drift through this helper; a hand edit needs `render-daemon-env`.
#
# Idempotency
# -----------
# `set` is idempotent — running twice with the same value is a no-op
# from the file's perspective (same content, same mode, same owner).
# `set-once` accepts an absent/empty key or the exact existing value and
# refuses replacement. It is for persisted HMAC roots that require a
# versioned migration before rotation.
# `delete` is idempotent — deleting an already-absent key is a no-op.
#
# Required privilege
# ------------------
# Must run as root (`sudo bash -s --` over SSH). The script `exec`s
# `install(1)` which needs root to chown to root:tinyassets.
#
# Exit codes
# ----------
#   0  success — file rewritten with new content (or no-op for absent
#      delete targets), owner=root:tinyassets, mode=640, readable by the
#      tinyassets user (post-write assert passes).
#   1  bad invocation (unknown subcommand, missing args, missing key
#      name, value containing forbidden chars).
#   2  env bootstrap failed before install(1).
#   3  transaction staging, sync, or atomic replacement failed.
#   4  post-write readability assert failed (tinyassets user cannot read).
#   5  set-once refused replacement of an existing non-empty value.
#   6  assert-absent found a Compose-recognized target assignment or could
#      not read the target file.
#   7  the daemon's env file could not be rendered exactly (unreadable source,
#      a forbidden value spanning lines, a quoted value followed by more text,
#      an unclosed quote). On `set`/`delete` this is checked before the source
#      is written, so the source is left unchanged.

set -euo pipefail

ENV_FILE="${TINYASSETS_ENV_FILE-/etc/tinyassets/env}"
LEGACY_ENV_FILE="${TINYASSETS_LEGACY_ENV_FILE-/etc/workflow/env}"
ENV_OWNER="${TINYASSETS_ENV_OWNER-root:tinyassets}"
ENV_MODE="${TINYASSETS_ENV_MODE-640}"
ENV_READ_USER="${TINYASSETS_ENV_READ_USER-tinyassets}"
ENV_READ_USER_HOME="${TINYASSETS_ENV_READ_USER_HOME-/opt/tinyassets}"
ENV_READ_USER_SHELL="${TINYASSETS_ENV_READ_USER_SHELL-/usr/sbin/nologin}"
DAEMON_ENV_FILE="${TINYASSETS_DAEMON_ENV_FILE-/etc/tinyassets/daemon.env}"
DAEMON_ENV_SOURCE="${TINYASSETS_DAEMON_ENV_SOURCE-/etc/tinyassets/env}"
COMPOSE_ASSIGNMENT=""
COMPOSE_TRIMMED=""
ATOMIC_TEMP=""

# Platform secrets the daemon container must never receive. Nothing under
# tinyassets/ reads them; the host, a sidecar, or nobody does. This is the shell
# copy of tinyassets/platform_secrets.py DAEMON_FORBIDDEN_ENV, and
# tests/test_platform_secret_scope.py holds the two equal. deploy_fail_safe.sh
# reads it through `daemon-forbidden-names` rather than keeping a third copy.
DAEMON_FORBIDDEN_ENV=(
    DO_API_TOKEN
    CLOUDFLARE_TUNNEL_TOKEN
    BETTERSTACK_SOURCE_TOKEN
    SUPABASE_DB_URL
    SUPABASE_SERVICE_ROLE_KEY
)

usage() {
    cat >&2 <<'EOF'
Usage:
  install-tinyassets-env.sh set <KEY>           # value on stdin
  install-tinyassets-env.sh set-once <KEY>      # immutable value on stdin
  install-tinyassets-env.sh set-pair <KEY> <KEY> # two newline-framed values on stdin
  install-tinyassets-env.sh delete <KEY> [KEY...]
  install-tinyassets-env.sh assert-absent <KEY> # read-only Compose-aware check
  install-tinyassets-env.sh render-daemon-env   # daemon.env = env minus platform secrets
  install-tinyassets-env.sh daemon-forbidden-names
EOF
    exit 1
}

# Validate KEY: env var name shape (letters, digits, underscore; not
# starting with a digit). Refusing odd characters keeps the sed
# expression safe under any caller.
validate_key() {
    local key="$1"
    if [[ ! "${key}" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
        echo "::error::invalid env key: ${key}" >&2
        exit 1
    fi
}

# Match Docker Compose's accepted env-declaration whitespace. Bash [[:space:]]
# under C.UTF-8 omits much of Unicode's White_Space property, so enumerate the
# non-ASCII code points Compose accepts instead of depending on the host locale.
strip_compose_leading_space() {
    local input="$1"
    while true; do
        case "${input}" in
            ' '*) input="${input# }" ;;
            $'\t'*) input="${input#$'\t'}" ;;
            $'\r'*) input="${input#$'\r'}" ;;
            $'\v'*) input="${input#$'\v'}" ;;
            $'\f'*) input="${input#$'\f'}" ;;
            $'\u0085'*) input="${input#$'\u0085'}" ;;
            $'\u00A0'*) input="${input#$'\u00A0'}" ;;
            $'\u1680'*) input="${input#$'\u1680'}" ;;
            $'\u2000'*) input="${input#$'\u2000'}" ;;
            $'\u2001'*) input="${input#$'\u2001'}" ;;
            $'\u2002'*) input="${input#$'\u2002'}" ;;
            $'\u2003'*) input="${input#$'\u2003'}" ;;
            $'\u2004'*) input="${input#$'\u2004'}" ;;
            $'\u2005'*) input="${input#$'\u2005'}" ;;
            $'\u2006'*) input="${input#$'\u2006'}" ;;
            $'\u2007'*) input="${input#$'\u2007'}" ;;
            $'\u2008'*) input="${input#$'\u2008'}" ;;
            $'\u2009'*) input="${input#$'\u2009'}" ;;
            $'\u200A'*) input="${input#$'\u200A'}" ;;
            $'\u2028'*) input="${input#$'\u2028'}" ;;
            $'\u2029'*) input="${input#$'\u2029'}" ;;
            $'\u202F'*) input="${input#$'\u202F'}" ;;
            $'\u205F'*) input="${input#$'\u205F'}" ;;
            $'\u3000'*) input="${input#$'\u3000'}" ;;
            *) break ;;
        esac
    done
    COMPOSE_TRIMMED="${input}"
}

# Docker Compose accepts optional leading Unicode whitespace, an optional
# `export` prefix, whitespace before the delimiter, either `=` or `:`, and an
# optional UTF-8 BOM at the beginning of the file. Normalize only enough to
# identify the declared key; preserve the original line for unrelated entries.
compose_line_assigns_key() {
    local line="$1"
    local key="$2"
    local normalized="${line}"
    strip_compose_leading_space "${normalized}"
    normalized="${COMPOSE_TRIMMED}"
    normalized="${normalized#$'\xEF\xBB\xBF'}"
    strip_compose_leading_space "${normalized}"
    normalized="${COMPOSE_TRIMMED}"
    if [[ "${normalized}" == export* ]]; then
        local after_export="${normalized#export}"
        strip_compose_leading_space "${after_export}"
        if [ "${COMPOSE_TRIMMED}" != "${after_export}" ]; then
            normalized="${COMPOSE_TRIMMED}"
        fi
    fi
    [[ "${normalized}" == "${key}"* ]] || return 1
    local remainder="${normalized#"${key}"}"
    strip_compose_leading_space "${remainder}"
    remainder="${COMPOSE_TRIMMED}"
    [[ -z "${remainder}" || "${remainder}" == =* || "${remainder}" == :* ]] || return 1
    COMPOSE_ASSIGNMENT="${key}${remainder}"
}

env_owner_user() {
    printf '%s' "${ENV_OWNER%%:*}"
}

env_owner_group() {
    if [[ "${ENV_OWNER}" == *:* ]]; then
        printf '%s' "${ENV_OWNER#*:}"
    else
        id -gn "$(env_owner_user)" 2>/dev/null || id -gn
    fi
}

owner_label() {
    if [ -n "${ENV_OWNER}" ]; then
        printf '%s' "${ENV_OWNER}"
    else
        printf '<current-user>'
    fi
}

cleanup_atomic_temp() {
    if [ -n "${ATOMIC_TEMP}" ]; then
        rm -f -- "${ATOMIC_TEMP}" 2>/dev/null || true
        ATOMIC_TEMP=""
    fi
}

handle_atomic_signal() {
    local signal="$1"
    cleanup_atomic_temp
    trap - "${signal}"
    kill -s "${signal}" "$$"
}

clear_atomic_traps() {
    trap - EXIT HUP INT TERM
}

install_atomic_traps() {
    trap cleanup_atomic_temp EXIT
    trap 'handle_atomic_signal HUP' HUP
    trap 'handle_atomic_signal INT' INT
    trap 'handle_atomic_signal TERM' TERM
}

prepare_atomic_temp() {
    local env_dir
    local env_name
    env_dir="$(dirname -- "${ENV_FILE}")"
    env_name="$(basename -- "${ENV_FILE}")"
    install_atomic_traps
    if ! ATOMIC_TEMP="$(umask 077; mktemp "${env_dir}/.${env_name}.tmp.XXXXXX")"; then
        clear_atomic_traps
        echo "::error::failed creating sibling transaction for ${ENV_FILE}" >&2
        exit 3
    fi
}

commit_atomic_temp() {
    ensure_owner_principals
    if ! chmod "${ENV_MODE}" "${ATOMIC_TEMP}"; then
        echo "::error::failed setting mode on transaction for ${ENV_FILE}" >&2
        exit 3
    fi
    if [ -n "${ENV_OWNER}" ] && ! chown "$(env_owner_user):$(env_owner_group)" "${ATOMIC_TEMP}"; then
        echo "::error::failed setting owner on transaction for ${ENV_FILE}" >&2
        exit 3
    fi
    if command -v sync >/dev/null 2>&1 && ! sync -f "${ATOMIC_TEMP}"; then
        echo "::error::failed syncing transaction for ${ENV_FILE}" >&2
        exit 3
    fi
    if ! mv -fT -- "${ATOMIC_TEMP}" "${ENV_FILE}"; then
        echo "::error::failed atomically replacing ${ENV_FILE}" >&2
        exit 3
    fi
    ATOMIC_TEMP=""
    clear_atomic_traps
}

ensure_group_exists() {
    local group="$1"
    [ -n "${group}" ] || return
    if getent group "${group}" >/dev/null 2>&1; then
        return
    fi
    if ! command -v groupadd >/dev/null 2>&1; then
        echo "::error::group ${group} missing and groupadd unavailable" >&2
        exit 2
    fi
    echo "::notice::creating system group ${group}" >&2
    groupadd --system "${group}"
}

ensure_read_user_exists() {
    [ -n "${ENV_READ_USER}" ] || return 0
    if id -u "${ENV_READ_USER}" >/dev/null 2>&1; then
        return
    fi
    if ! command -v useradd >/dev/null 2>&1; then
        echo "::error::user ${ENV_READ_USER} missing and useradd unavailable" >&2
        exit 2
    fi

    local primary_group=""
    if [ -n "${ENV_OWNER}" ]; then
        primary_group="$(env_owner_group)"
        ensure_group_exists "${primary_group}"
    fi

    local user_args=(
        --system
        --home "${ENV_READ_USER_HOME}"
        --create-home
        --shell "${ENV_READ_USER_SHELL}"
        --comment "TinyAssets daemon service account"
    )
    if [ -n "${primary_group}" ]; then
        user_args+=(--gid "${primary_group}")
    fi
    echo "::notice::creating system user ${ENV_READ_USER}" >&2
    useradd "${user_args[@]}" "${ENV_READ_USER}"
}

ensure_docker_membership() {
    [ -n "${ENV_READ_USER}" ] || return 0
    id -u "${ENV_READ_USER}" >/dev/null 2>&1 || return 0
    getent group docker >/dev/null 2>&1 || return 0
    if id -nG "${ENV_READ_USER}" | grep -qw docker; then
        return
    fi
    if command -v usermod >/dev/null 2>&1; then
        usermod -aG docker "${ENV_READ_USER}" || true
    fi
}

ensure_owner_principals() {
    if [ -n "${ENV_OWNER}" ]; then
        ensure_group_exists "$(env_owner_group)"
    fi
    ensure_read_user_exists
    ensure_docker_membership
    return 0
}

# Confirm the tinyassets user can actually read the file. This is the
# canary that the systemd unit's ExecStartPre would have tripped on.
assert_readable() {
    if [ -n "${ENV_READ_USER}" ]; then
        if ! sudo -u "${ENV_READ_USER}" test -r "${ENV_FILE}"; then
            echo "::error::ENV-UNREADABLE: ${ENV_FILE} not readable by user ${ENV_READ_USER} after install" >&2
            ls -l "${ENV_FILE}" >&2 || true
            exit 4
        fi
    elif ! test -r "${ENV_FILE}"; then
        echo "::error::ENV-UNREADABLE: ${ENV_FILE} not readable after install" >&2
        ls -l "${ENV_FILE}" >&2 || true
        exit 4
    fi
}

# Read current file content. If the renamed env file is missing on a
# pre-cutover host, bootstrap it from /etc/workflow/env once. If there is
# no legacy file, create an empty env file so the deploy can write the new
# image pin and secrets through the same atomic helper.
ensure_env_file() {
    if [ -f "${ENV_FILE}" ]; then
        return
    fi

    local env_dir
    env_dir="$(dirname "${ENV_FILE}")"
    if ! mkdir -p "${env_dir}"; then
        echo "::error::failed to create ${env_dir}" >&2
        exit 2
    fi
    ensure_owner_principals
    if [ -n "${ENV_OWNER}" ]; then
        chown "$(env_owner_user):$(env_owner_group)" "${env_dir}" || true
        chmod 750 "${env_dir}" || true
    fi

    local src="/dev/null"
    if [ -f "${LEGACY_ENV_FILE}" ]; then
        src="${LEGACY_ENV_FILE}"
        echo "::notice::${ENV_FILE} missing; bootstrapping from ${LEGACY_ENV_FILE}" >&2
    else
        echo "::notice::${ENV_FILE} missing; creating empty env file" >&2
    fi

    atomic_install_from_file "${src}"
    assert_readable
}

# Copy a source into a same-directory transaction and rename it over ENV_FILE.
# A failed copy, metadata operation, sync, or rename leaves the live file
# untouched; the EXIT/signal traps remove the incomplete sibling.
atomic_install_from_file() {
    local src="$1"
    prepare_atomic_temp
    if ! cp -- "${src}" "${ATOMIC_TEMP}"; then
        echo "::error::failed staging ${ENV_FILE}" >&2
        exit 3
    fi
    commit_atomic_temp
}

# Atomic write of an in-process buffer to ENV_FILE with correct owner + mode.
# The transaction is a mode-0600 same-directory target candidate, not a
# secret-only value staging file. The protected value never enters child argv
# or environment, and the live file changes only at the final rename.
atomic_install() {
    local content="$1"
    prepare_atomic_temp
    if ! printf '%s' "${content}" > "${ATOMIC_TEMP}"; then
        echo "::error::failed writing transaction for ${ENV_FILE}" >&2
        exit 3
    fi
    commit_atomic_temp
}

cmd_set() {
    local key="$1"
    local immutable="${2-false}"
    validate_key "${key}"
    ensure_env_file

    # Read new value from stdin (verbatim, including any trailing
    # newline the caller piped in — but we strip a single trailing
    # newline so `echo "foo" | ...` produces `KEY=foo` not `KEY=foo\n`
    # appearing as `KEY=foo` followed by a blank entry line).
    local value=""
    install_atomic_traps
    if IFS= read -r -d '' value; then
        echo "::error::protected input cannot contain NUL for ${key}" >&2
        exit 1
    fi
    value="${value%$'\n'}"

    if [ "${immutable}" = "true" ]; then
        if [[ "${value}" == *$'\n'* || "${value}" == *$'\r'* ]]; then
            echo "::error::set-once requires a single-line value for ${key}" >&2
            exit 1
        fi
        local existing=""
        local assignment_count=0
        local line
        while IFS= read -r line || [ -n "${line}" ]; do
            if compose_line_assigns_key "${line}" "${key}"; then
                assignment_count=$((assignment_count + 1))
                if [ "${assignment_count}" -gt 1 ]; then
                    echo "::error::set-once refused duplicate assignments for ${key}" >&2
                    exit 5
                fi
                local remainder="${COMPOSE_ASSIGNMENT#"${key}"}"
                strip_compose_leading_space "${remainder}"
                remainder="${COMPOSE_TRIMMED}"
                if [[ "${remainder}" == =* || "${remainder}" == :* ]]; then
                    local delimiter="${remainder:0:1}"
                    existing="${remainder:1}"
                    if [ "${delimiter}" = ":" ]; then
                        strip_compose_leading_space "${existing}"
                        existing="${COMPOSE_TRIMMED}"
                    fi
                else
                    existing=""
                fi
            fi
        done < "${ENV_FILE}"
        if [ -n "${existing}" ]; then
            if [ "${existing}" != "${value}" ]; then
                echo "::error::set-once refused rotation for ${key}; versioned migration required" >&2
                exit 5
            fi
        fi
    fi

    # Build new content in this shell: replace every Compose-recognized target
    # assignment with one canonical line, or append when absent. The protected
    # value never enters child argv/environment or a secret-only value file.
    local new_content=""
    local found="false"
    while IFS= read -r line || [ -n "${line}" ]; do
        if compose_line_assigns_key "${line}" "${key}"; then
            if [ "${found}" = "false" ]; then
                new_content+="${key}=${value}"$'\n'
                found="true"
            fi
        else
            new_content+="${line}"$'\n'
        fi
    done < "${ENV_FILE}"
    if [ "${found}" = "false" ]; then
        new_content+="${key}=${value}"$'\n'
    fi

    # A source the daemon copy cannot be rendered from is refused BEFORE it is
    # committed: committing first would leave daemon.env stale, and the unit
    # refuses to start on a stale copy.
    if is_daemon_env_source; then
        render_daemon_content "${new_content}"
    fi
    atomic_install "${new_content}"
    assert_readable
    echo "set ${key} (${ENV_FILE} $(owner_label) ${ENV_MODE})"
    if is_daemon_env_source; then
        write_daemon_env
    fi
}

cmd_set_pair() {
    local first="$1" second="$2" value1 value2 extra line new_content=""
    validate_key "${first}"
    validate_key "${second}"
    [ "${first}" != "${second}" ] || exit 1
    # Read and validate the entire pair before changing either env file.
    if ! IFS= read -r value1 || ! IFS= read -r value2 ||
        IFS= read -r extra || [ -n "${extra}" ]; then
        echo "::error::set-pair requires exactly two single-line values for ${first} ${second}" >&2
        exit 1
    fi
    if [[ ! "${value1}" =~ ^[A-Za-z0-9._~:/+=,@%-]+$ ||
          ! "${value2}" =~ ^[A-Za-z0-9._~:/+=,@%-]+$ ]]; then
        echo "::error::set-pair requires portable unquoted values for ${first} ${second}" >&2
        exit 1
    fi
    ensure_env_file
    while IFS= read -r line || [ -n "${line}" ]; do
        if ! compose_line_assigns_key "${line}" "${first}" &&
            ! compose_line_assigns_key "${line}" "${second}"; then
            new_content+="${line}"$'\n'
        fi
    done < "${ENV_FILE}"
    new_content+="${first}=${value1}"$'\n'"${second}=${value2}"$'\n'
    if is_daemon_env_source; then
        render_daemon_content "${new_content}"
    fi
    # Each rename contains BOTH keys. Even a failed daemon render commit leaves
    # each consumer with a whole old/new pair, never a half-written pair.
    atomic_install "${new_content}"
    assert_readable
    if is_daemon_env_source; then
        write_daemon_env
    fi
    echo "set pair ${first} ${second}"
}

cmd_delete() {
    local key
    ensure_env_file
    for key in "$@"; do
        validate_key "${key}"
    done

    local new_content=""
    local drop
    local line
    while IFS= read -r line || [ -n "${line}" ]; do
        drop="false"
        for key in "$@"; do
            if compose_line_assigns_key "${line}" "${key}"; then
                drop="true"
                break
            fi
        done
        if [ "${drop}" = "false" ]; then
            new_content+="${line}"$'\n'
        fi
    done < "${ENV_FILE}"

    if is_daemon_env_source; then
        render_daemon_content "${new_content}"
    fi
    atomic_install "${new_content}"
    assert_readable
    echo "deleted: $* (${ENV_FILE} $(owner_label) ${ENV_MODE})"
    if is_daemon_env_source; then
        write_daemon_env
    fi
}

cmd_assert_absent() {
    local key="$1"
    validate_key "${key}"
    if [ ! -r "${ENV_FILE}" ]; then
        echo "::error::cannot assert ${key} absence: ${ENV_FILE} is unreadable" >&2
        exit 6
    fi
    local line
    while IFS= read -r line || [ -n "${line}" ]; do
        if compose_line_assigns_key "${line}" "${key}"; then
            echo "::error::Compose-recognized assignment for ${key} remains in ${ENV_FILE}" >&2
            exit 6
        fi
    done < "${ENV_FILE}"
    echo "absent ${key} (${ENV_FILE})"
}

# --- the daemon's env file ---------------------------------------------
#
# A line filter over a dotenv file is only safe if it knows where values
# begin and end, so this reads the file the way Compose's dotenv parser does as
# far as that matters here: an optional BOM, leading whitespace and `export`,
# a key, `=` or `:`, and a value that is unquoted (to end of line), single- or
# double-quoted on one line (optionally followed by a comment), or a quoted
# value that continues onto later lines. Anything it cannot place exactly is
# REFUSED rather than guessed at (Codex on the first draft: a quoted value
# followed by a second assignment, and a kept multi-line value containing a
# line that looked like a forbidden assignment, were both mishandled).
#
# Names are reported; values never are.

ENV_KEY=""
ENV_VALUE=""
QUOTE_OPEN=""
RENDERED_CONTENT=""
RENDER_REMOVED=""

# Parse one line as a declaration; sets ENV_KEY and ENV_VALUE. A bare name
# (`DO_API_TOKEN`, `export DO_API_TOKEN`) is a declaration too: Compose fills it
# from the project environment, which is the host env file itself (Codex round
# 2), so it is removed exactly like an assignment.
parse_env_assignment() {
    local normalized rest after_export
    strip_compose_leading_space "$1"
    normalized="${COMPOSE_TRIMMED#$'\xEF\xBB\xBF'}"
    strip_compose_leading_space "${normalized}"
    normalized="${COMPOSE_TRIMMED}"
    if [[ "${normalized}" == export* ]]; then
        after_export="${normalized#export}"
        strip_compose_leading_space "${after_export}"
        if [ "${COMPOSE_TRIMMED}" != "${after_export}" ]; then
            normalized="${COMPOSE_TRIMMED}"
        fi
    fi
    [[ "${normalized}" =~ ^([A-Za-z_][A-Za-z0-9_.-]*)(.*)$ ]] || return 1
    ENV_KEY="${BASH_REMATCH[1]}"
    rest="${BASH_REMATCH[2]}"
    strip_compose_leading_space "${rest}"
    rest="${COMPOSE_TRIMMED}"
    if [[ -z "${rest//[[:space:]]/}" ]]; then
        ENV_VALUE=""
        return 0
    fi
    [[ "${rest}" == =* || "${rest}" == :* ]] || return 1
    strip_compose_leading_space "${rest:1}"
    ENV_VALUE="${COMPOSE_TRIMMED}"
}

# Classify ENV_VALUE. Returns 0 when it is understood (QUOTE_OPEN set to the
# quote character when the value continues onto the next line), 2 when a
# quoted value is followed by anything but a comment.
classify_env_value() {
    local re_dq_closed='^"([^"\\]|\\.)*"[[:space:]]*(#.*)?$'
    local re_dq_open='^"([^"\\]|\\.)*\\?$'
    # Compose honours a backslash escape inside single quotes too (`'it\'s'`).
    local re_sq_closed="^'([^'\\\\]|\\\\.)*'[[:space:]]*(#.*)?\$"
    local re_sq_open="^'([^'\\\\]|\\\\.)*(\\\\)?\$"
    QUOTE_OPEN=""
    case "${ENV_VALUE}" in
        \"*)
            if [[ "${ENV_VALUE}" =~ ${re_dq_closed} ]]; then return 0; fi
            if [[ "${ENV_VALUE}" =~ ${re_dq_open} ]]; then QUOTE_OPEN='"'; return 0; fi
            return 2
            ;;
        \'*)
            if [[ "${ENV_VALUE}" =~ ${re_sq_closed} ]]; then return 0; fi
            if [[ "${ENV_VALUE}" =~ ${re_sq_open} ]]; then QUOTE_OPEN="'"; return 0; fi
            return 2
            ;;
    esac
    return 0
}

# Does this continuation line close the open quote? Returns 0 if it closes
# cleanly, 1 if the value continues, 2 if text other than a comment follows.
closes_open_quote() {
    local re_dq_close='^([^"\\]|\\.)*"(.*)$'
    local re_sq_close="^([^'\\\\]|\\\\.)*'(.*)\$"
    local re_tail='^[[:space:]]*(#.*)?$'
    local tail
    if [ "${QUOTE_OPEN}" = '"' ]; then
        [[ "$1" =~ ${re_dq_close} ]] || return 1
        tail="${BASH_REMATCH[2]}"
    else
        [[ "$1" =~ ${re_sq_close} ]] || return 1
        tail="${BASH_REMATCH[2]}"
    fi
    [[ "${tail}" =~ ${re_tail} ]] || return 2
    return 0
}

# Names RETIRED from the platform: no code reads them, the template no longer
# declares them, and they must never reach the daemon even if a stale
# assignment is still sitting in the shared env on some host.
#
# This is enforced HERE, at the renderer, not in deploy-prod.yml, because the
# workflow is not the only way daemon.env gets written (Codex, 2026-10-03):
# every `set` renders it when the target is the shared env, and
# apply-daemon-env.yml, deploy/hetzner-bootstrap.sh and p0-outage-triage.yml
# all reach the daemon through this helper without passing the deploy's
# scrub-and-assert. Enforcing at the shared boundary covers all of them.
#
# Kept separate from DAEMON_FORBIDDEN_ENV on purpose: that list is the live
# platform secrets the daemon must not hold, and tests hold it equal to the
# Python copy in tinyassets/platform_secrets.py. A retired name has no Python
# counterpart -- it is gone from the product, not guarded within it -- so
# listing it there would both re-add a channel-specific mention to the user
# substrate and misdescribe it as live.
#
# Delete an entry only once no host can still carry the key.
RETIRED_ENV=(
    GITHUB_OAUTH_CLIENT_ID
    GITHUB_OAUTH_CLIENT_SECRET
)

is_daemon_forbidden() {
    local name
    for name in "${DAEMON_FORBIDDEN_ENV[@]}" "${RETIRED_ENV[@]}"; do
        [ "$1" = "${name}" ] && return 0
    done
    return 1
}

refuse_render() {
    echo "::error::cannot render ${DAEMON_ENV_FILE}: $1" >&2
    exit 7
}

# Render $1 (the source's text) into RENDERED_CONTENT, every assignment of a
# DAEMON_FORBIDDEN_ENV name removed; RENDER_REMOVED lists them. Works on a
# string so a write can be checked BEFORE the source is committed.
render_daemon_content() {
    local rest="$1" line out="" first=1 rc removed=() closing
    out+="# GENERATED from ${DAEMON_ENV_SOURCE} by install-tinyassets-env.sh"$'\n'
    out+="# render-daemon-env. Edit the source, never this file. Removed names:"$'\n'
    out+="# ${DAEMON_FORBIDDEN_ENV[*]} ${RETIRED_ENV[*]}"$'\n'
    QUOTE_OPEN=""
    while [ -n "${rest}" ]; do
        if [[ "${rest}" == *$'\n'* ]]; then
            line="${rest%%$'\n'*}"
            rest="${rest#*$'\n'}"
        else
            line="${rest}"
            rest=""
        fi
        if [ "${first}" = "1" ]; then
            # Compose ignores a BOM only at byte zero, and the header above
            # moves this line off byte zero.
            line="${line#$'\xEF\xBB\xBF'}"
            first=0
        fi
        if [ -n "${QUOTE_OPEN}" ]; then
            # Inside a kept multi-line value: copy verbatim, never filter.
            out+="${line}"$'\n'
            rc=0
            closes_open_quote "${line}" || rc=$?
            if [ "${rc}" = "0" ]; then
                QUOTE_OPEN=""
            elif [ "${rc}" = "2" ]; then
                refuse_render "a multi-line quoted value is followed by more text on its closing line"
            fi
            continue
        fi
        if ! parse_env_assignment "${line}"; then
            out+="${line}"$'\n'
            continue
        fi
        rc=0
        classify_env_value || rc=$?
        if [ "${rc}" = "2" ]; then
            refuse_render "${ENV_KEY} has a quoted value followed by more text; Compose may read a second assignment there"
        fi
        if is_daemon_forbidden "${ENV_KEY}"; then
            if [ -n "${QUOTE_OPEN}" ]; then
                refuse_render "${ENV_KEY} has a quoted value that continues past its line; edit it onto one line"
            fi
            removed+=("${ENV_KEY}")
            continue
        fi
        out+="${line}"$'\n'
    done
    if [ -n "${QUOTE_OPEN}" ]; then
        refuse_render "a quoted value is never closed"
    fi
    RENDERED_CONTENT="${out}"
    RENDER_REMOVED="${removed[*]:-none}"
}

# Read a whole file into READ_CONTENT, trailing newlines included. A failed
# read is a failure: `cat ...; printf x` reported success with empty content
# when the read failed, which would have installed an empty daemon file.
READ_CONTENT=""
read_file_exactly() {
    local text
    text="$(cat -- "$1" && printf x)" || return 1
    READ_CONTENT="${text%x}"
}

# Write RENDERED_CONTENT to DAEMON_ENV_FILE through the same transaction, owner
# and mode as the source, then read back what landed.
write_daemon_env() {
    local ENV_FILE="${DAEMON_ENV_FILE}"
    atomic_install "${RENDERED_CONTENT}"
    read_file_exactly "${DAEMON_ENV_FILE}" \
        || refuse_render "${DAEMON_ENV_FILE} unreadable after write"
    render_daemon_content "${READ_CONTENT}"
    if [ "${RENDER_REMOVED}" != "none" ]; then
        refuse_render "${RENDER_REMOVED} survived into ${DAEMON_ENV_FILE}"
    fi
    assert_readable
    echo "rendered ${DAEMON_ENV_FILE} from ${DAEMON_ENV_SOURCE}"
}

cmd_render_daemon_env() {
    local removed
    if [ ! -r "${DAEMON_ENV_SOURCE}" ]; then
        refuse_render "${DAEMON_ENV_SOURCE} is unreadable"
    fi
    read_file_exactly "${DAEMON_ENV_SOURCE}" \
        || refuse_render "${DAEMON_ENV_SOURCE} is unreadable"
    render_daemon_content "${READ_CONTENT}"
    removed="${RENDER_REMOVED}"
    write_daemon_env
    echo "removed: ${removed}"
}

# Writes to the main env file keep the daemon's copy current. Any other target
# (request-idempotency.env, agent-interchange.env, a test's temp file) is not
# the daemon's source and renders nothing.
is_daemon_env_source() {
    [ -n "${DAEMON_ENV_FILE}" ] && [ "${ENV_FILE}" = "${DAEMON_ENV_SOURCE}" ]
}

[ $# -ge 1 ] || usage
subcmd="$1"
shift

case "${subcmd}" in
    set)
        [ $# -eq 1 ] || usage
        cmd_set "$1"
        ;;
    set-once)
        [ $# -eq 1 ] || usage
        cmd_set "$1" true
        ;;
    set-pair)
        [ $# -eq 2 ] || usage
        cmd_set_pair "$@"
        ;;
    delete)
        [ $# -ge 1 ] || usage
        cmd_delete "$@"
        ;;
    assert-absent)
        [ $# -eq 1 ] || usage
        cmd_assert_absent "$1"
        ;;
    render-daemon-env)
        [ $# -eq 0 ] || usage
        cmd_render_daemon_env
        ;;
    daemon-forbidden-names)
        [ $# -eq 0 ] || usage
        printf '%s\n' "${DAEMON_FORBIDDEN_ENV[@]}"
        ;;
    *)
        usage
        ;;
esac
