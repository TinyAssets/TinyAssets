#!/usr/bin/env bash
# Save a failed candidate's evidence to host temp files, fast, and nothing else.
#
# Called by deploy-prod.yml BEFORE the public-canary rollback. The rollback must
# not wait for sanitizing or artifact work (Codex P1 on #4367: a failed public
# probe left the broken candidate serving while evidence was collected), so this
# script does one thing: write the raw bytes to /tmp. The workflow fetches,
# sanitizes and uploads them AFTER the rollback.
#
# Every read is bound to the container ID resolved here, once (Codex P2): the
# name `tinyassets-daemon` is mutable, so inspecting the name and then reading
# logs by name over a second connection can hand back a REPLACEMENT container's
# logs while the manifest still claims the candidate matched. Resolving the id
# and reading `inspect` and `logs` by that id makes the two reads the same
# container or no container.
#
# Writes, all world-readable so the workflow can scp them as the deploy user:
#   /tmp/ta-candidate.cid     the container id the evidence came from
#   /tmp/ta-candidate.state   one inspect line, the shape the sanitizer parses
#   /tmp/ta-candidate.log     the tail of that container's logs
#
# Exit status is NOT the workflow's gate: a vanished container is a legitimate
# outcome (the rollback may already have replaced it), and the workflow records
# that as unavailable evidence rather than failing the deploy. Every docker call
# is `timeout`-bounded so a wedged daemon cannot hold the rollback.

set -u

CONTAINER="${CONTAINER:-tinyassets-daemon}"
STATE_BYTES=16385
LOG_BYTES=131072
LOG_LINES=200
DOCKER_TIMEOUT="${DOCKER_TIMEOUT:-10}"

CID_FILE=/tmp/ta-candidate.cid
STATE_FILE=/tmp/ta-candidate.state
LOG_FILE=/tmp/ta-candidate.log

# Start from a clean slate: a stale file from an earlier deploy must never be
# fetched and reported as this run's evidence.
rm -f "${CID_FILE}" "${STATE_FILE}" "${LOG_FILE}"

cid="$(timeout "${DOCKER_TIMEOUT}s" docker inspect --type container \
        --format '{{.Id}}' "${CONTAINER}" 2>/dev/null)" || cid=""
if [ -z "${cid}" ]; then
    echo "snapshot: ${CONTAINER} is gone; no candidate evidence to take" >&2
    exit 0
fi
printf '%s\n' "${cid}" > "${CID_FILE}"

# The field order IS the sanitizer's input contract
# (scripts/sanitize_startup_diagnostics.py --state).
timeout "${DOCKER_TIMEOUT}s" docker inspect --type container "${cid}" \
    --format '{{.State.Status}}|{{.State.Running}}|{{.State.Restarting}}|{{.State.ExitCode}}|{{.State.OOMKilled}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}|{{index .Config.Labels "org.opencontainers.image.revision"}}|{{.Config.Image}}|{{json .State.Error}}' \
    2>/dev/null | head -c "${STATE_BYTES}" > "${STATE_FILE}"

timeout "${DOCKER_TIMEOUT}s" docker logs --tail "${LOG_LINES}" "${cid}" 2>&1 \
    | tail -c "${LOG_BYTES}" > "${LOG_FILE}"

chmod 0644 "${CID_FILE}" "${STATE_FILE}" "${LOG_FILE}" 2>/dev/null || true
echo "snapshot: took evidence from ${cid}" >&2
exit 0
