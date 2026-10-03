#!/usr/bin/env bash
# Recover the TinyAssets daemon when the systemd unit, compose containers, or
# daemon heartbeat stop advancing. Intended for systemd timer execution.

set -euo pipefail

COMPOSE_FILE="${TINYASSETS_COMPOSE_FILE:-/opt/tinyassets/compose.yml}"
SERVICE_UNIT="${TINYASSETS_DAEMON_UNIT:-tinyassets-daemon.service}"
DATA_VOLUME="${TINYASSETS_DATA_VOLUME:-tinyassets-data}"
# Empty default -> auto-discover the freshest worker-supervisor heartbeat
# (see heartbeat_path). Set TINYASSETS_HEARTBEAT_RELATIVE to a
# "<universe>/<file>" path under the data volume to pin a specific one.
HEARTBEAT_RELATIVE="${TINYASSETS_HEARTBEAT_RELATIVE:-}"
HEARTBEAT_MAX_AGE_SECONDS="${TINYASSETS_HEARTBEAT_MAX_AGE_SECONDS:-900}"
# Headroom on top of the staleness threshold before a young container's
# heartbeat is allowed to condemn it (see within_heartbeat_grace).
HEARTBEAT_GRACE_MARGIN_SECONDS="${TINYASSETS_HEARTBEAT_GRACE_MARGIN_SECONDS:-120}"
LOCK_FILE="${TINYASSETS_DAEMON_WATCHDOG_LOCK:-/run/tinyassets-daemon-watchdog.lock}"
# The lock deploy/deploy_fail_safe.sh holds for its whole run (its LOCK_FILE).
HOST_MUTATION_LOCK="${TINYASSETS_HOST_MUTATION_LOCK:-/var/lock/tinyassets-host-mutation.lock}"
LOG_TAG="daemon-watchdog"

log() {
    printf '%s [%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$LOG_TAG" "$*"
}

seconds_or_default() {
    # Replace a threshold bash would misread with its default instead of dying
    # on it, or worse, quietly obeying it.
    #
    # These two feed `(( ... ))`, where a value that is merely "all digits" is
    # not safe. Measured on this host, four distinct wrong outcomes:
    #
    #   "1+", "5 "  arithmetic syntax error. `(( ))` returns non-zero, so inside
    #               an `if` it reads FALSE -- nothing is ever stale. In a sum it
    #               leaves the target unset and `set -u` ABORTS the script.
    #   "08"        a leading zero makes it OCTAL, and 8 is not an octal digit:
    #               "value too great for base". As the max age that reads FALSE,
    #               so a hung container logs "healthy" and is never restarted.
    #               As the margin it aborts. Either way recovery is gone.
    #   "010"       valid octal, so it is silently EIGHT, not ten.
    #   20 digits   overflows a signed 64-bit integer and WRAPS; 99999999999999999999
    #               came out as 7766279631452241979.
    #
    # So the pattern is the decimal shape, not "digits": no leading zero (except
    # a bare 0) and at most nine of them, which caps the window near 31 years and
    # cannot overflow. Aborting is the one outcome a recovery tool must never
    # have -- an operator typo would silence auto-recovery entirely, and they
    # would see a unit exiting non-zero rather than a daemon never restarted. A
    # bad threshold is an operator mistake; refusing to run is an outage.
    #
    # Names the ENV var in the message, not the internal one: the operator set
    # TINYASSETS_*, and that is what they have to go and fix.
    local name="$1" default="$2" env_name="$3" value="${!1}"
    [[ "${value}" =~ ^(0|[1-9][0-9]{0,8})$ ]] && return 0
    log "ignoring ${env_name}='${value}': not a non-negative integer; using ${default}"
    printf -v "${name}" '%s' "${default}"
}

seconds_or_default HEARTBEAT_MAX_AGE_SECONDS 900 TINYASSETS_HEARTBEAT_MAX_AGE_SECONDS
seconds_or_default HEARTBEAT_GRACE_MARGIN_SECONDS 120 \
    TINYASSETS_HEARTBEAT_GRACE_MARGIN_SECONDS

restart_daemon() {
    local reason="$1"
    # Fail-closed by design: every restart here targets the SAME cloud service
    # and container on this droplet. There is no other host in this script's
    # repertoire, so it cannot fail work over anywhere. If the daemon refuses
    # platform admission (`platform_not_cloud`), failed unit/container checks
    # or an existing stale heartbeat may restart it; each new process must
    # pass admission again. Missing heartbeat alone is not heartbeat_stale.
    # Repeated refusal is the intended fail-closed outcome, not serving — fix
    # admission or the droplet identity, never widen what may serve and never
    # add a local/personal-desktop fallback.
    # Daemon-scoped: restart the daemon CONTAINER, never the whole unit. The
    # unit's restart used to run `compose down`, taking the tunnel and logs
    # down with the daemon on every watchdog fire (2026-08-21 Codex review).
    # If the container does not exist at all, re-converge the unit instead.
    log "restarting daemon container: ${reason}"
    if docker inspect tinyassets-daemon >/dev/null 2>&1; then
        # A hung-but-"healthy" daemon needs an actual container restart;
        # `up -d` alone would leave an unchanged container running.
        # -t 20: the same drain ceiling as deploy_fail_safe.sh. A plain restart
        # would use the container's create-time StopTimeout, which is 180s for a
        # container a rollback recreated from a pre-2026-10-01 bundle.
        docker restart -t 20 tinyassets-daemon || true
    fi
    # Then re-converge the unit: with no ExecStop this is a safe `up -d` of
    # the three production services, which (re)starts the tunnel/logs if they
    # are down and restores an inactive unit so the timer does not loop.
    systemctl reset-failed "${SERVICE_UNIT}" >/dev/null 2>&1 || true
    systemctl restart "${SERVICE_UNIT}"
}

container_running() {
    local name="$1"
    local state
    state="$(docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null || true)"
    [[ "$state" == "true" ]]
}

heartbeat_path() {
    local mountpoint
    mountpoint="$(docker volume inspect "$DATA_VOLUME" --format '{{ .Mountpoint }}' 2>/dev/null || true)"
    if [[ -z "$mountpoint" ]]; then
        return 1
    fi
    # Operator-pinned path wins.
    if [[ -n "$HEARTBEAT_RELATIVE" ]]; then
        printf '%s/%s\n' "$mountpoint" "$HEARTBEAT_RELATIVE"
        return 0
    fi
    # Auto-discover: the active universe rewrites
    # <universe>/.worker_supervisor*.json every ~15s. Check the FRESHEST one so
    # a restart fires only when the ENTIRE fleet has gone silent — not a single
    # dormant universe. The legacy default (earthos/heartbeat) pointed at a path
    # that never existed, so heartbeat detection was effectively dead.
    local freshest
    freshest="$(find "$mountpoint" -name '.worker_supervisor*.json' -printf '%T@ %p\n' 2>/dev/null \
        | sort -rn | head -1 | cut -d' ' -f2-)"
    if [[ -z "$freshest" ]]; then
        return 1
    fi
    printf '%s\n' "$freshest"
}

container_age_seconds() {
    # Seconds since the container last started, or non-zero if unknowable.
    local name="$1" started epoch
    started="$(docker inspect -f '{{.State.StartedAt}}' "$name" 2>/dev/null || true)"
    [[ -n "$started" ]] || return 1
    epoch="$(date -u -d "$started" +%s 2>/dev/null || true)"
    [[ -n "$epoch" ]] || return 1
    printf '%s\n' "$(( $(date -u +%s) - epoch ))"
}

within_heartbeat_grace() {
    # A container recreated moments ago has not written a heartbeat yet, and the
    # heartbeat lives on the DATA VOLUME -- so the freshest file on disk is the
    # one the PREVIOUS container left behind. Every deploy recreates the daemon,
    # so without this the watchdog read that inherited file as proof the new
    # container was dead and restarted it, killing an in-flight user turn a
    # second time (measured 2026-09-25: deploy at ~23:02, watchdog restart at
    # ~23:04). The container cannot refresh a heartbeat it has not had time to
    # write.
    #
    # Only the heartbeat signal is graced. An inactive unit or a
    # not-running container still restarts immediately, and both are checked
    # before this. A real hang in an OLD container is still caught, because the
    # window closes once the container is older than the threshold it is judged
    # against.
    #
    # Unknowable age does NOT grant grace: a watchdog that cannot tell must
    # still recover.
    local name="$1" age grace
    age="$(container_age_seconds "$name")" || return 1
    grace=$(( HEARTBEAT_MAX_AGE_SECONDS + HEARTBEAT_GRACE_MARGIN_SECONDS ))
    # A NEGATIVE age means the container reports having started in the future:
    # the host clock stepped back (NTP correction, a VM restored from a
    # snapshot). That is unknowable age, not youth, and it took the grace branch
    # unconditionally -- every negative number is less than the window -- which
    # would have suppressed recovery for as long as the skew lasted. Treated
    # like an unreadable timestamp: no grace.
    (( age >= 0 && age < grace )) || return 1
    log "heartbeat grace: ${name} started ${age}s ago (< ${grace}s), too young to have refreshed the heartbeat"
    return 0
}

heartbeat_stale() {
    local path="$1"
    [[ -f "$path" ]] || return 1

    local now mtime age
    now="$(date +%s)"
    mtime="$(stat -c %Y "$path")"
    age=$(( now - mtime ))
    if (( age > HEARTBEAT_MAX_AGE_SECONDS )); then
        log "heartbeat stale: ${path} age=${age}s max=${HEARTBEAT_MAX_AGE_SECONDS}s"
        return 0
    fi
    return 1
}

main() {
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        log "another watchdog run is active; exiting"
        exit 0
    fi

    # Stand down while a deploy holds the host-mutation lock. A deploy's own
    # recreate leaves the unit inactive and the container briefly absent, which
    # reads as dead here. On 2026-10-01 this script restarted the container
    # mid-deploy and helped kill the new image and fail the rollback
    # (docs/audits/2026-10-01-deploy-drain-repro/INCIDENT.md).
    # This unit runs as root, the lock's owner, so `>>` may create it. Creating
    # it here closes the window right after boot where a lockless check could
    # race a deploy that takes the lock a moment later. fd 8 stays held through
    # any restart below, so a deploy waits for us in turn.
    if ! exec 8>>"$HOST_MUTATION_LOCK"; then
        log "cannot open ${HOST_MUTATION_LOCK}; proceeding unlocked"
    elif ! flock -n 8; then
        log "a deploy holds ${HOST_MUTATION_LOCK}; standing down"
        exit 0
    fi

    if ! command -v docker >/dev/null 2>&1; then
        log "docker is unavailable"
        exit 1
    fi

    if ! systemctl is-active --quiet "$SERVICE_UNIT"; then
        restart_daemon "systemd unit is not active"
        exit 0
    fi

    if ! container_running tinyassets-daemon; then
        restart_daemon "tinyassets-daemon container is not running"
        exit 0
    fi

    if [[ -f "$COMPOSE_FILE" ]]; then
        docker compose -f "$COMPOSE_FILE" ps >/dev/null
    fi

    local hb_path=""
    if hb_path="$(heartbeat_path)"; then
        if heartbeat_stale "$hb_path"; then
            if within_heartbeat_grace tinyassets-daemon; then
                log "healthy: unit active, tinyassets-daemon running, heartbeat within start-up grace"
                exit 0
            fi
            restart_daemon "heartbeat stale"
            exit 0
        fi
    else
        log "heartbeat volume ${DATA_VOLUME} is not available yet; relying on unit/container checks"
    fi

    log "healthy: unit active, tinyassets-daemon running"
}

main "$@"
