#!/usr/bin/env bash
# Hold a deploy while the live daemon is running someone's work. Runs on the
# GitHub runner, NOT the droplet: it reaches the droplet over ssh.
#
# A deploy recreates the daemon, and that kills every turn running in it: on
# 2026-10-02 it cut the founder's live village turn twice. Each poll runs
# scripts/turns_in_flight.py on the droplet host against the daemon's data
# volume (so it works whatever image is live) and asks whether anything is in
# flight: seats a live process holds, or graph runs a live process owns. The
# loop ends on
#   idle                    nothing in flight; the deploy may continue (exit 0)
#   daemon_not_serving      container absent, stopped or unhealthy: nothing to
#                           protect, and the deploy may be its fix (exit 0)
#   check_unavailable       three unanswerable polls in a row. "Cannot tell" is
#                           never read as idle and is not a pass (exit 1)
#   yield_to_host_mutation  a recovery workflow (YIELD_WORKFLOWS) is queued behind
#                           this run. Fail this deploy so the repair can run
#                           without this swap cutting the turn (exit 1)
#   cap_reached             TURN_WAIT_CAP_S elapsed with work still in flight.
#                           Refuse the swap; do not cut the turn (exit 1)
#
# "idle" is a moment, not a promise: a turn can start between the last poll and
# the swap, and the 20s drain bound then cuts it. IMAGE_REF is pulled BEFORE the
# wait so the fail-safe's own pull is a no-op, which leaves the candidate proof
# plus the converge as the window rather than a 2.7 GB download. Closing it
# entirely needs an admission hold, and that ships only together with
# persist-and-replay of the held messages.
#
# Every remote call is bounded (CHECK_TIMEOUT_S plus ssh keepalives): a hung ssh
# or probe counts as "cannot tell"; it must not stall past the cap. The cap
# includes the prefetch, so the step timeout (cap + one worst-case poll +
# cleanup) always outlasts it.
#
# Env: DO_SSH_USER DO_DROPLET_HOST TURN_WAIT_CAP_S TURN_POLL_S GITHUB_OUTPUT;
#      optional TARGET_REVISION RUN_URL IMAGE_REF CHECK_TIMEOUT_S DATA_VOLUME, and
#      YIELD_WORKFLOWS with GH_TOKEN + GITHUB_REPOSITORY.
# Writes outcome / waited_s / polls to GITHUB_OUTPUT. Exits 0 only for idle and
# daemon_not_serving. Every other outcome fails the deploy before the swap.
#
# `set +e` on purpose: every poll's exit status is data (10 busy, 20 not
# serving, 2 unknown), and the step's `bash -e` must not turn "busy" into a
# failed deploy.
set +e
set -uo pipefail
: "${DO_SSH_USER:?}" "${DO_DROPLET_HOST:?}" "${TURN_WAIT_CAP_S:?}" "${TURN_POLL_S:?}"
: "${GITHUB_OUTPUT:?}"
TARGET_REVISION="${TARGET_REVISION:-}"
RUN_URL="${RUN_URL:-}"
CHECK_TIMEOUT_S="${CHECK_TIMEOUT_S:-90}"
DATA_VOLUME="${DATA_VOLUME:-tinyassets-data}"
case "${DATA_VOLUME}" in *[!A-Za-z0-9_.-]*|'') echo "::error::bad DATA_VOLUME"; exit 1 ;; esac
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
probe="${here}/../scripts/turns_in_flight.py"
host="${DO_SSH_USER}@${DO_DROPLET_HOST}"
SSH_OPTS=(-i ~/.ssh/do_deploy -o BatchMode=yes -o ConnectTimeout=15
          -o ServerAliveInterval=10 -o ServerAliveCountMax=3)

remote() { timeout "${CHECK_TIMEOUT_S}" ssh "${SSH_OPTS[@]}" "${host}" "$@"; }

# The cap counts from here, so the prefetch below spends it rather than adding
# to it: the step's own timeout must never fire before the cap does.
start="$(date +%s)"
deadline=$(( start + TURN_WAIT_CAP_S ))

# A per-run name plus a checksum the host verifies before every run: a failed
# or partial upload must read as "cannot tell", never execute a stale or empty
# file (an empty `python3 -` exits 0, which is "idle").
remote_probe="/tmp/turns_in_flight.${GITHUB_RUN_ID:-local}.${GITHUB_RUN_ATTEMPT:-0}.py"
probe_sha="$(sha256sum "${probe}" | cut -d' ' -f1)"
timeout "${CHECK_TIMEOUT_S}" scp "${SSH_OPTS[@]}" "${probe}" "${host}:${remote_probe}" \
  || echo "::warning::could not ship the in-flight probe; polls will report unknown"

if [ -n "${IMAGE_REF:-}" ]; then
  # Adds only the image the fail-safe would pull anyway; touches no container.
  # Bounded by what is left of the cap. A failure is the fail-safe's to report.
  budget=$(( deadline - $(date +%s) ))
  [ "${budget}" -gt 900 ] && budget=900
  if [ "${budget}" -le 0 ]; then
    echo "prefetch skipped: no time left under the cap"
  elif timeout "${budget}" ssh "${SSH_OPTS[@]}" "${host}" \
       "sudo docker pull -q $(printf '%q' "${IMAGE_REF}")" >/dev/null; then
    echo "prefetched ${IMAGE_REF}"
  else
    echo "::warning::prefetch of ${IMAGE_REF} failed; the fail-safe pulls it itself"
  fi
fi

# The probe runs in a throwaway sibling container: the daemon's own image, as
# root, the data volume mounted read-only, no network. Root is required because
# /data/.broker is 1002:1101 mode 2700: the daemon uid's stat of
# /data/.broker/.runs.db raises PermissionError and used to fail the whole
# observation. The volume stays read-only and the probe copies sqlite into a
# private temp dir, so this root process creates no -wal/-shm on the volume.
# The pending marker is written on the host, against the volume's mountpoint,
# because the read-only mount cannot hold it. Never `docker exec` into the
# daemon (every repo exec into it goes through ta-op's closed mode table;
# scripts/check_drop_first_exec.py). The liveness locks are kernel flocks on
# the volume's inodes, taken on a read-only descriptor.
#
# $@ = the probe's arguments. All generated here (a sha, epochs, our own run
# URL), never user text; printf %q keeps each one word anyway.
check() {
  local mark=0
  local -a probe_args=()
  while [ "$#" -gt 0 ]; do
    if [ "$1" = "--mark-pending" ]; then
      mark=1
    else
      probe_args+=("$1")
    fi
    shift
  done
  local args=""
  if [ "${#probe_args[@]}" -gt 0 ]; then
    args="$(printf ' %q' "${probe_args[@]}")"
  fi
  # --clear-pending removes the marker on the host. The read-only probe cannot.
  if [ "$mark" = 0 ] && [ "${probe_args[0]:-}" = "--clear-pending" ]; then
    remote "echo '${probe_sha}  ${remote_probe}' | sha256sum -c --status - || { echo 'probe checksum mismatch'; exit 2; }; \
      vol=\$(sudo docker volume inspect -f '{{.Mountpoint}}' ${DATA_VOLUME}) || { echo 'volume mountpoint unreadable'; exit 2; }; \
      python3 ${remote_probe} --clear-pending --data-dir \"\$vol\""
    return
  fi
  local marker=""
  if [ "$mark" = 1 ]; then
    # The flag stays in this remote command: the host writes the marker from
    # the probe's JSON. The container mount is read-only.
    marker="vol=\$(sudo docker volume inspect -f '{{.Mountpoint}}' ${DATA_VOLUME}) || { echo 'volume mountpoint unreadable'; exit 2; }; \
      printf '%s\n' \"\$probe_out\" | python3 ${remote_probe} --write-marker-from-report --mark-pending --data-dir \"\$vol\"${args} >/dev/null || echo '::warning::could not refresh the deploy-pending marker';"
  fi
  remote "state=\$(sudo docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{.State.StartedAt}}|{{.Image}}' tinyassets-daemon 2>/dev/null); \
    case \"\${state%%|*}\" in running/healthy|running/) ;; *) echo \"daemon_state=\${state:-absent}\"; exit 20;; esac; \
    rest=\${state#*|}; image=\${rest#*|}; boot=\$(date -d \"\${rest%%|*}\" +%s 2>/dev/null) || boot=''; \
    echo '${probe_sha}  ${remote_probe}' | sha256sum -c --status - || { echo 'probe checksum mismatch'; exit 2; }; \
    probe_out=\$(sudo docker run --rm -i --network none --memory 256m --user 0:0 --read-only --tmpfs /tmp \
      -v ${DATA_VOLUME}:/data:ro --entrypoint python \"\$image\" \
      -I - --data-dir /data \${boot:+--boot-epoch \$boot}${args} < ${remote_probe}); \
    probe_rc=\$?; \
    ${marker} \
    printf '%s\n' \"\$probe_out\"; \
    exit \$probe_rc"
}

# Is a host-mutating recovery run queued behind us? "Cannot tell" counts as no.
recovery_waiting() {
  [ -n "${YIELD_WORKFLOWS:-}" ] && [ -n "${GITHUB_REPOSITORY:-}" ] || return 1
  local wf n
  for wf in ${YIELD_WORKFLOWS}; do
    n="$(timeout 30 gh api "repos/${GITHUB_REPOSITORY}/actions/workflows/${wf}/runs?per_page=10" \
          --jq '[.workflow_runs[] | select(.status == "queued" or .status == "pending" or .status == "waiting" or .status == "requested")] | length' \
          2>/dev/null)" || continue
    if [ "${n:-0}" -gt 0 ] 2>/dev/null; then
      echo "::notice::${wf} is queued behind this deploy; not making it wait"
      return 0
    fi
  done
  return 1
}

unknown=0
outcome=""
polls=0
while :; do
  polls=$(( polls + 1 ))
  out="$(check --mark-pending --target "${TARGET_REVISION}" \
           --waiting-since "${start}" --deadline "${deadline}" \
           --ttl $(( TURN_POLL_S * 4 )) --run-url "${RUN_URL}")"
  rc=$?
  printf '%s\n' "${out}"
  case "${rc}" in
    0)  outcome="idle"; break ;;
    20) outcome="daemon_not_serving"; break ;;
    10) unknown=0
        if recovery_waiting; then outcome="yield_to_host_mutation"; break; fi ;;
    *)  unknown=$(( unknown + 1 ))
        echo "::warning::in-flight check could not answer (rc=${rc}, ${unknown}/3)"
        if [ "${unknown}" -ge 3 ]; then outcome="check_unavailable"; break; fi ;;
  esac
  # Read after the recovery query, which can itself take time.
  if [ "$(date +%s)" -ge "${deadline}" ]; then outcome="cap_reached"; break; fi
  sleep "${TURN_POLL_S}"
done
waited=$(( $(date +%s) - start ))
# The marker says "an update is waiting". Clear it whatever the outcome; a
# stale one also expires on its own (--ttl).
check --clear-pending >/dev/null 2>&1 || true
{
  echo "outcome=${outcome}"
  echo "waited_s=${waited}"
  echo "polls=${polls}"
} >> "${GITHUB_OUTPUT}"
case "${outcome}" in
  idle)
    echo "::notice::no work in flight after ${waited}s"
    exit 0
    ;;
  daemon_not_serving)
    echo "::notice::daemon not serving; nothing to protect"
    exit 0
    ;;
  check_unavailable)
    echo "::error::in-flight check unavailable; refusing to deploy without proof that no turn is running"
    exit 1
    ;;
  yield_to_host_mutation)
    echo "::error::yielding to a queued recovery workflow; refusing to cut off in-flight work"
    exit 1
    ;;
  cap_reached)
    echo "::error::work still in flight after the ${TURN_WAIT_CAP_S}s cap; refusing to cut the turn off"
    exit 1
    ;;
esac
echo "::error::in-flight check ended without an outcome"
exit 1
