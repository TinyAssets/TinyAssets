#!/usr/bin/env bash
# deploy/deploy_host.sh — the host-side deploy logic that deploy-prod.yml and
# the live-like gate (.github/workflows/live-like-gate.yml) share.
#
# One phase per invocation:
#
#   bash deploy/deploy_host.sh <phase>
#
# Every phase body is the production step it replaced, unchanged, except that
# the step's `ssh -i ~/.ssh/do_deploy -o BatchMode=yes "$DO_SSH_USER@$DO_DROPLET_HOST"`
# became `remote` and its `scp ... host:dest` became `copy_to`/`copy_from`.
# Values the workflow used to interpolate with `${{ }}` arrive as environment
# variables, named in each phase. GitHub's own default variables
# (GITHUB_RUN_ID, GITHUB_OUTPUT, RUNNER_TEMP, ...) are read directly.
#
# Target (transport and URLs), from the environment or DEPLOY_TARGET_ENV:
#   DEPLOY_TARGET        ssh (default) | local
#   DO_SSH_USER, DO_DROPLET_HOST, DEPLOY_SSH_KEY (default ~/.ssh/do_deploy)
#   DEPLOY_MCP_URL       default https://tinyassets.io/mcp
#   DEPLOY_APP_BASE_URL  default https://tinyassets.io
#   DEPLOY_EXPECTED_INSTANCE_FILE  default platform-expected-instance.json
#
# `local` runs the same remote command strings on this machine through
# `bash -c` (commands carry their own `sudo`, as they do over SSH) and copies
# files with `cp`. Authority (secrets, the DigitalOcean lookup, GHCR, the
# production environment) never lives here: it stays in the caller.
#
# tests/test_deploy_host_driver.py records every remote command a phase issues
# and keeps deploy-prod.yml and the gate calling the same phases in the same
# order.
set -euo pipefail

PHASE="${1:-}"
if [ -z "$PHASE" ]; then
  echo "::error::usage: deploy_host.sh <phase>" >&2
  exit 2
fi
shift

if [ -n "${DEPLOY_TARGET_ENV:-}" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$DEPLOY_TARGET_ENV"
  set +a
fi
DEPLOY_TARGET="${DEPLOY_TARGET:-ssh}"
DEPLOY_SSH_KEY="${DEPLOY_SSH_KEY:-$HOME/.ssh/do_deploy}"
DEPLOY_MCP_URL="${DEPLOY_MCP_URL:-https://tinyassets.io/mcp}"
DEPLOY_APP_BASE_URL="${DEPLOY_APP_BASE_URL:-https://tinyassets.io}"
DEPLOY_EXPECTED_INSTANCE_FILE="${DEPLOY_EXPECTED_INSTANCE_FILE:-platform-expected-instance.json}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

case "$DEPLOY_TARGET" in
  ssh) : "${DO_SSH_USER:?DO_SSH_USER required for DEPLOY_TARGET=ssh}" \
         "${DO_DROPLET_HOST:?DO_DROPLET_HOST required for DEPLOY_TARGET=ssh}" ;;
  local) DO_SSH_USER="${DO_SSH_USER:-$(id -un)}"; DO_DROPLET_HOST="${DO_DROPLET_HOST:-localhost}" ;;
  *) echo "::error::DEPLOY_TARGET must be ssh or local (got ${DEPLOY_TARGET})" >&2; exit 2 ;;
esac
export DO_SSH_USER DO_DROPLET_HOST

# remote <command...>: one command line on the host, stdin passed through.
# REMOTE_TIMEOUT (e.g. 25s) bounds the call; REMOTE_SSH_OPTS adds ssh options.
remote() {
  local -a prefix=()
  [ -z "${REMOTE_TIMEOUT:-}" ] || prefix=(timeout "$REMOTE_TIMEOUT")
  if [ "$DEPLOY_TARGET" = local ]; then
    ${prefix[@]+"${prefix[@]}"} bash -c "$*"
  else
    # shellcheck disable=SC2086
    ${prefix[@]+"${prefix[@]}"} ssh -i "$DEPLOY_SSH_KEY" -o BatchMode=yes ${REMOTE_SSH_OPTS:-} \
      "${DO_SSH_USER}@${DO_DROPLET_HOST}" "$@"
  fi
}
# copy_to <local file>... <host path>
copy_to() {
  local dest="${*: -1}"
  local -a sources=("${@:1:$#-1}")
  if [ "$DEPLOY_TARGET" = local ]; then
    cp "${sources[@]}" "$dest"
  else
    scp -i "$DEPLOY_SSH_KEY" -o BatchMode=yes "${sources[@]}" "${DO_SSH_USER}@${DO_DROPLET_HOST}:${dest}"
  fi
}
# copy_from <host file>... <local dir>
copy_from() {
  local dest="${*: -1}"
  local -a sources=("${@:1:$#-1}")
  local -a prefix=()
  [ -z "${REMOTE_TIMEOUT:-}" ] || prefix=(timeout "$REMOTE_TIMEOUT")
  if [ "$DEPLOY_TARGET" = local ]; then
    ${prefix[@]+"${prefix[@]}"} cp "${sources[@]}" "$dest"
  else
    local -a remote_sources=()
    local source
    for source in "${sources[@]}"; do
      remote_sources+=("${DO_SSH_USER}@${DO_DROPLET_HOST}:${source}")
    done
    # shellcheck disable=SC2086
    ${prefix[@]+"${prefix[@]}"} scp -i "$DEPLOY_SSH_KEY" -o BatchMode=yes ${REMOTE_SSH_OPTS:-} \
      "${remote_sources[@]}" "$dest"
  fi
}

# ---------------------------------------------------------------------------
# Phases, in deploy-prod.yml order.
# ---------------------------------------------------------------------------

phase_capture_current_image() {
  set -euo pipefail
  # What is RUNNING, not what the env file says: after a failed deploy
  # the env file already names the failed image. Fall back to the env
  # ref only when no daemon container exists.
  # Captured as an immutable repo digest (the rollback script requires
  # an @sha256: ref); `.Config.Image` could be a mutable tag.
  prev="$(remote \
         "docker inspect -f '{{.Image}}' tinyassets-daemon 2>/dev/null | xargs -r docker image inspect -f '{{index .RepoDigests 0}}' 2>/dev/null")"
  case "$prev" in
    *@sha256:*) ;;
    *) prev="$(remote \
             "grep -E '^TINYASSETS_IMAGE=' /etc/tinyassets/env | head -1 | cut -d= -f2-")" ;;
  esac
  echo "prev_image=${prev}" >> "$GITHUB_OUTPUT"
  echo "current prod image (running, digest): ${prev:-<none>}"
}

phase_live_preflight() {
  # Retire a completed old-layout rollback before the read-only store check.
  # Stream this candidate's control code: the installed harness may predate
  # finish-rollback. Its dependencies come from the retained recovery harness.
  # No receipt means no imports and no mutation on an ordinary deploy.
  local retry
  retry="$(cat <<'PY'
import os, subprocess, sys
from pathlib import Path
state = Path(os.environ.get("BUNDLE_STATE_DIR", "/var/lib/tinyassets-deploy")) / "cutover"
if not os.path.lexists(state):
    sys.exit(0)
harness = Path(os.environ.get("BOX_HARNESS_ROOT", "/opt/tinyassets/boxhostd"))
sys.path.insert(0, str(harness / "harness-current/app"))
control = {"__name__": "cutover_retry"}
exec(compile(sys.stdin.read(), "cutover_control.py", "exec"), control)
run = control["_run"]
root = Path(run("docker", "volume", "inspect", "tinyassets-data", "--format", "{{ .Mountpoint }}"))
try:
    image_id = run("docker", "inspect", "-f", "{{.Image}}", "tinyassets-daemon")
    image = run("docker", "image", "inspect", "-f", "{{index .RepoDigests 0}}", image_id)
except subprocess.CalledProcessError:
    image = ""
if "@sha256:" not in image:
    env = Path(os.environ.get("ENV_FILE", "/etc/tinyassets/env"))
    image = next((line.split("=", 1)[1] for line in env.read_text().splitlines()
                  if line.startswith("TINYASSETS_IMAGE=")), "")
control["finish_rollback"](
    state, root, old_image=image,
    store=Path(os.environ.get("BOX_STORE_DIR", "/var/lib/boxhostd/owners")),
    host_state=Path(os.environ.get("BOX_HOST_STATE", "/var/lib/boxhostd/state")),
    runsc=Path(os.environ.get("BOX_RUNSC", "/opt/tinyassets/boxhostd/current/runsc")))
print("previous rollback cleaned and archived before live preflight")
PY
)"
  remote "sudo flock -w 120 '${LOCK_FILE:-/var/lock/tinyassets-host-mutation.lock}' python3 -c $(printf '%q' "$retry")" \
    < tinyassets/cutover_control.py
  # The remaining preflight is read-only. Recovery above changes only attempt
  # artifacts and the deployment receipt, never data or backups.
  remote "sudo env CORE_CANARY_PROVISIONED='${CORE_CANARY_PROVISIONED:-}' python3 - --data-volume tinyassets-data --box-store /var/lib/boxhostd/owners --harness-root /opt/tinyassets/boxhostd --env-file /etc/tinyassets/env" \
    < scripts/live_preflight.py
}

phase_install_request_hmac() {
  # Env: TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY, REQUEST_HMAC_INSTALL_MODE.
  set -euo pipefail
  case "${REQUEST_HMAC_INSTALL_MODE}" in
    set-once|set) ;;
    *)
      echo "::error::invalid request HMAC install mode"
      exit 1
      ;;
  esac
  if [ "${REQUEST_HMAC_INSTALL_MODE}" = "set" ] && [ "${GITHUB_EVENT_NAME}" != "workflow_dispatch" ]; then
    echo "::error::request HMAC rotation is manual-dispatch only"
    exit 1
  fi
  copy_to deploy/install-tinyassets-env.sh /tmp/install-tinyassets-env.sh
  if [ "${REQUEST_HMAC_INSTALL_MODE}" = "set" ]; then
    # Rotating while the shared env still carries a copy would leave the
    # old key live there; refuse first.
    if ! remote \
        "sudo env TINYASSETS_ENV_FILE=/etc/tinyassets/env bash /tmp/install-tinyassets-env.sh assert-absent TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY"; then
      echo "::error::rotation prerequisite failed: shared env exposes request admission authority"
      exit 1
    fi
  fi
  printf '%s' "${TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY}" | \
      remote \
      "sudo env TINYASSETS_ENV_FILE=/etc/tinyassets/request-idempotency.env TINYASSETS_LEGACY_ENV_FILE=/etc/tinyassets/no-request-idempotency-legacy bash /tmp/install-tinyassets-env.sh '${REQUEST_HMAC_INSTALL_MODE}' TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY"
}

phase_install_oauth_credentials() {
  # Env: TINYASSETS_OAUTH_GOOGLE_CLIENT_ID, TINYASSETS_OAUTH_GOOGLE_CLIENT_SECRET,
  #      OAUTH_ACTION, PREV_IMAGE.
  set +x
  set -euo pipefail
  # Decisions and image references are the only remote arguments.
  case "${OAUTH_ACTION}" in install|skip|remove) ;; *) exit 1 ;; esac
  if [[ -n "${PREV_IMAGE}" && ! "${PREV_IMAGE}" =~ ^[a-zA-Z0-9./_-]+@sha256:[0-9a-f]{64}$ ]]; then
    echo "::warning::unrecognized rollback image; removing OAuth keys"
    OAUTH_ACTION=remove
    PREV_IMAGE=''
  fi
  copy_to deploy/install-oauth-credentials.sh /tmp/install-oauth-credentials.sh
  if [ "${OAUTH_ACTION}" != install ]; then
    TINYASSETS_OAUTH_GOOGLE_CLIENT_ID=''
    TINYASSETS_OAUTH_GOOGLE_CLIENT_SECRET=''
  fi
  # The HMAC step installed the env helper. The lock covers the rollback
  # proof and the whole-pair write/delete, including daemon.env rendering.
  printf '%s\n' "${TINYASSETS_OAUTH_GOOGLE_CLIENT_ID}" "${TINYASSETS_OAUTH_GOOGLE_CLIENT_SECRET}" | \
    remote \
      "sudo flock -w 120 /var/lock/tinyassets-host-mutation.lock bash /tmp/install-oauth-credentials.sh '${OAUTH_ACTION}' '${PREV_IMAGE}'"
}

phase_install_interchange_hmac() {
  # Env: TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY.
  set -euo pipefail
  printf '%s' "${TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY}" | \
      remote \
      "sudo env TINYASSETS_ENV_FILE=/etc/tinyassets/agent-interchange.env TINYASSETS_LEGACY_ENV_FILE=/etc/tinyassets/no-agent-interchange-legacy bash /tmp/install-tinyassets-env.sh set TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY"
}

phase_validate_host_hmac_pair() {
  set -euo pipefail
  remote \
      "sudo python3 -" < scripts/validate_host_runtime_hmac_pair.py
}

phase_scrub_env_overrides() {
  set -euo pipefail
  remote \
      "sudo bash -s -- delete TINYASSETS_WIKI_PATH TINYASSETS_UNIVERSE TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY WORKFLOW_IMAGE WORKFLOW_DATA_DIR WORKFLOW_MCP_CANARY_URL WORKFLOW_ALLOW_API_KEY_PROVIDERS WORKFLOW_NODE_ENQUEUE_ENABLED WORKFLOW_SOUL_LOOP_DISPATCH WORKFLOW_BUG_INVESTIGATION_BACKFILL WORKFLOW_BUG_INVESTIGATION_BRANCH_DEF_ID WORKFLOW_BUG_INVESTIGATION_GOAL_ID GITHUB_OAUTH_CLIENT_ID GITHUB_OAUTH_CLIENT_SECRET" \
      < deploy/install-tinyassets-env.sh
  remote \
      "sudo env TINYASSETS_ENV_FILE=/etc/tinyassets/env bash /tmp/install-tinyassets-env.sh assert-absent TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY" || {
    echo "::error::shared env still contains request admission minting authority"
    exit 1
  }
  # The GitHub OAuth pair was retired on 2026-10-03: nothing reads it,
  # no route serves the callback the template used to describe, and
  # deploy/tinyassets-env.template no longer declares it (#4422).
  # Withholding it from the daemon is handled at the renderer, by
  # RETIRED_ENV in deploy/install-tinyassets-env.sh (#4267) -- that
  # covers every writer of daemon.env, not just this workflow. This
  # step is the other half: withholding keeps the key out of the
  # daemon, DELETING makes it actually go away. The delete above runs
  # first and this proves it took, so a stale assignment cannot sit on
  # the host indefinitely.
  remote \
      "sudo env TINYASSETS_ENV_FILE=/etc/tinyassets/env bash /tmp/install-tinyassets-env.sh assert-absent GITHUB_OAUTH_CLIENT_SECRET" || {
    echo "::error::shared env still contains the retired GITHUB_OAUTH_CLIENT_SECRET"
    exit 1
  }
}

phase_stage_bundle() {
  set -euo pipefail
  # Per-run directory, not a shared /tmp/tinyassets-bundle. A shared path
  # is populated here, BEFORE the script's host-mutation lock exists, so
  # a concurrent deploy or a manual run could swap the bytes between the
  # script's validation and its install (Codex round 2). The script also
  # copies this into a private directory under the lock; the per-run name
  # keeps two workflow runs from clobbering each other's stage first.
  stage="/tmp/tinyassets-bundle-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
  echo "stage=${stage}" >> "$GITHUB_OUTPUT"
  remote \
      "sudo bash -s '${DO_SSH_USER}' '${stage}'" <<'SH'
set -euo pipefail
owner="$1"
stage="$2"
case "${stage}" in
  /tmp/tinyassets-bundle-*) : ;;
  *) echo "refusing to stage into an unexpected path: ${stage}" >&2; exit 1 ;;
esac
rm -rf "${stage}"
mkdir -p "${stage}"
chown "${owner}:" "${stage}"
chmod 0750 "${stage}"
# Sweep stages older than a day so a failed run does not accumulate.
find /tmp -maxdepth 1 -name 'tinyassets-bundle-*' -type d -mtime +1 \
  -exec rm -rf {} + 2>/dev/null || true
echo "staged bundle directory ${stage} (owner ${owner})"
SH
  copy_to \
      deploy/compose.yml \
      deploy/vector.yaml \
      deploy/vector-betterstack.yaml \
      deploy/vector-entrypoint.sh \
      deploy/tinyassets-daemon.service \
      "${stage}/"
  remote "ls -l '${stage}/'"
}

phase_prepare_data_volume() {
  remote \
      'sudo bash -s' <<'SH'
set -euo pipefail

VOLUME_NAME="tinyassets-data"
TINYASSETS_UID=1001
TINYASSETS_GID=1001

docker volume create "$VOLUME_NAME" >/dev/null
VOLUME_DIR="$(docker volume inspect "$VOLUME_NAME" --format '{{ .Mountpoint }}')"

# OAuth registration state lives directly at /data/.auth.db.
# The volume root must be writable by the container's uid 1001;
# otherwise every public MCP initialize request fails in
# OptionalOAuthProvider with sqlite3 "unable to open database file".
chown "$TINYASSETS_UID:$TINYASSETS_GID" "$VOLUME_DIR"
chmod 755 "$VOLUME_DIR"
find "$VOLUME_DIR" -maxdepth 1 -type f \
  \( -name '.auth.db' -o -name '.auth.db-*' \) \
  -exec chown "$TINYASSETS_UID:$TINYASSETS_GID" {} + \
  -exec chmod 600 {} + || true
SH
}

phase_sync_canary_bearer() {
  # Env: TINYASSETS_WIKI_CANARY_TOKEN.
  set -euo pipefail
  if [ "${#TINYASSETS_WIKI_CANARY_TOKEN}" -lt 32 ]; then
    echo "::error::TINYASSETS_WIKI_CANARY_TOKEN is unset or under 32 bytes. The protected pulse healthcheck and authenticated MCP probes require this service-principal credential."
    exit 1
  fi
  copy_to deploy/install-tinyassets-env.sh /tmp/install-tinyassets-env.sh
  printf '%s' "${TINYASSETS_WIKI_CANARY_TOKEN}" \
    | remote \
        "sudo bash /tmp/install-tinyassets-env.sh set TINYASSETS_WIKI_CANARY_TOKEN"
  echo "canary bearer synced"
}

phase_sync_canary_door() {
  # Env: TINYASSETS_CANARY_DOOR_KEY, TINYASSETS_CANARY_USER_ID.
  set -euo pipefail
  if [ -z "${TINYASSETS_CANARY_DOOR_KEY}" ] || [ -z "${TINYASSETS_CANARY_USER_ID}" ]; then
    remote \
        "sudo bash /tmp/install-tinyassets-env.sh delete TINYASSETS_CANARY_DOOR_KEY TINYASSETS_CANARY_USER_ID"
    echo "::warning::canary sign-in door not configured (needs secret TINYASSETS_CANARY_DOOR_KEY and variable TINYASSETS_CANARY_USER_ID); any host copies were removed, so the route is 404 and the core canary cannot sign in"
    exit 0
  fi
  if [ "${#TINYASSETS_CANARY_DOOR_KEY}" -lt 32 ]; then
    echo "::error::TINYASSETS_CANARY_DOOR_KEY is under 32 bytes; the daemon refuses a short door key"
    exit 1
  fi
  printf '%s\n%s\n' "${TINYASSETS_CANARY_DOOR_KEY}" "${TINYASSETS_CANARY_USER_ID}" \
    | remote \
        "sudo bash /tmp/install-tinyassets-env.sh set-pair TINYASSETS_CANARY_DOOR_KEY TINYASSETS_CANARY_USER_ID"
  echo "canary sign-in door synced"
}

phase_install_expected_instance() {
  # The caller prepared DEPLOY_EXPECTED_INSTANCE_FILE (production: the
  # DigitalOcean lookup in deploy-prod.yml). This installs it on the host.
  set -euo pipefail
  copy_to "${DEPLOY_EXPECTED_INSTANCE_FILE}" /tmp/platform-expected-instance.json
  remote 'bash -s' <<'REMOTE'
set -euo pipefail
vol="$(sudo docker volume inspect tinyassets-data --format '{{ .Mountpoint }}')"
[ -n "$vol" ] && [ "${vol#/}" != "$vol" ] && sudo test -d "$vol"
staged="$(sudo mktemp "${vol}/.platform-expected-instance.XXXXXX")"
trap 'sudo rm -f -- "$staged"' EXIT
sudo install -m 0644 -o root -g root /tmp/platform-expected-instance.json "$staged"
sudo mv -f -- "$staged" "${vol}/platform-expected-instance.json"
REMOTE
  echo "expected-instance state installed before candidate startup"
}

phase_disk_prepare() {
  set -euo pipefail
  helper="/tmp/daemon-image-retention-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}.py"
  snapshot_helper="/tmp/backup-snapshot-retention-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}.py"
  copy_to scripts/backup_snapshot_retention.py "${snapshot_helper}"
  copy_to scripts/daemon_image_retention.py "${helper}"
  remote \
      "sudo systemd-run --quiet --wait --pipe --collect -p EnvironmentFile=/etc/tinyassets/env /usr/bin/python3 ${snapshot_helper} --apply"
  remote \
      "sudo systemd-run --quiet --wait --pipe --collect -p EnvironmentFile=/etc/tinyassets/env /usr/bin/python3 ${helper} --apply --prepare-pull"
  echo "helper=${helper}" >> "$GITHUB_OUTPUT"
}

phase_wait_for_turns() {
  # Env: TARGET_REVISION, IMAGE_REF, TURN_WAIT_CAP_S, TURN_POLL_S, RUN_URL,
  #      GH_TOKEN, YIELD_WORKFLOWS. The script reaches the host itself and
  #      honours DEPLOY_TARGET=local.
  DEPLOY_TARGET="$DEPLOY_TARGET" DEPLOY_SSH_KEY="$DEPLOY_SSH_KEY" bash deploy/wait_for_turns.sh
}

phase_disk_recheck() {
  # Env: RETENTION_HELPER, IMAGE_REF.
  set -euo pipefail
  remote \
      "sudo systemd-run --quiet --wait --pipe --collect -p EnvironmentFile=/etc/tinyassets/env /usr/bin/python3 ${RETENTION_HELPER} --check-headroom --image-ref ${IMAGE_REF}"
}

phase_install_boxhostd() {
  set -euo pipefail
  list="${RUNNER_TEMP}/boxhostd-install-files.txt"
  python scripts/boxhostd_install_files.py > "$list"
  [[ -s "$list" ]]
  mapfile -t boxhostd_files < "$list"
  bundle="${RUNNER_TEMP}/boxhostd-install.tar"
  tar -cf "$bundle" "${boxhostd_files[@]}"
  copy_to "$bundle" /tmp/boxhostd-install.tar
  remote 'bash -s' <<'REMOTE_BOX'
set -euo pipefail
stage=$(mktemp -d /tmp/boxhostd-install.XXXXXXXX)
trap 'rm -rf -- "$stage"' EXIT
tar -xf /tmp/boxhostd-install.tar -C "$stage"
sudo flock -w 120 /var/lock/tinyassets-host-mutation.lock bash "$stage/deploy/install-boxhostd-preflight.sh"
REMOTE_BOX
}

phase_fail_safe_deploy() {
  # Env: IMAGE_REF, BUNDLE_DIR, RETENTION_HELPER, DEPLOY_STARTED_EPOCH,
  #      CORE_CANARY_PROVISIONED.
  set -euo pipefail
  probe="/tmp/turns_in_flight.${GITHUB_RUN_ID}.${GITHUB_RUN_ATTEMPT}.py"
  verifier="/tmp/verify_deploy_backup.py"
  copy_to \
      deploy/deploy_fail_safe.sh deploy/install-tinyassets-env.sh \
      deploy/snapshot_candidate_evidence.sh \
      /tmp/
  copy_to \
      scripts/turns_in_flight.py \
      "${probe}"
  copy_to \
      scripts/verify_deploy_backup.py \
      "${verifier}"
  set +e
  out="$(remote \
        "sudo HEALTH_TIMEOUT=180 DISK_HEADROOM_HELPER='${RETENTION_HELPER}' BUNDLE_DIR='${BUNDLE_DIR}' DEPLOY_STARTED_EPOCH='${DEPLOY_STARTED_EPOCH}' TURNS_PROBE='${probe}' VERIFY_BACKUP_PY='${verifier}' CORE_CANARY_PROVISIONED='${CORE_CANARY_PROVISIONED}' bash /tmp/deploy_fail_safe.sh '${IMAGE_REF}'")"
  rc=$?
  set -e
  echo "${out}"
  result="$(printf '%s\n' "${out}" | awk -F= '/^deploy_result=/{print $2}' | tail -1)"
  deployed="$(printf '%s\n' "${out}" | awk -F= '/^deployed_image=/{print $2}' | tail -1)"
  echo "rc=${rc}" >> "$GITHUB_OUTPUT"
  echo "result=${result}" >> "$GITHUB_OUTPUT"
  echo "deployed_image=${deployed}" >> "$GITHUB_OUTPUT"
  pending="$(printf '%s\n' "${out}" | awk -F= '/^cutover_admission_pending=/{print $2}' | tail -1)"
  echo "cutover_admission_pending=${pending}" >> "$GITHUB_OUTPUT"
  # rc 0 = deployed new, 2 = rolled back to previous (prod still up), 1/3 = hard fail.
  if [ "${rc}" -eq 0 ]; then
    echo "::notice::deployed ${deployed}"
  elif [ "${rc}" -eq 2 ]; then
    echo "::error::new image unhealthy; prod rolled back to ${deployed}"; exit 1
  else
    echo "::error::deploy failed (rc=${rc}); see log above"; exit 1
  fi
}

phase_public_mcp_canary() {
  # Env: TINYASSETS_WIKI_CANARY_TOKEN.
  set -euo pipefail
  python scripts/mcp_public_canary.py --url "${DEPLOY_MCP_URL}" --assert-handles
}

phase_public_app_probe() {
  set -euo pipefail
  python scripts/probe_app_surface.py --base-url "${DEPLOY_APP_BASE_URL}" --verbose
}

phase_roll_owner_boxes() {
  # Env: IMAGE_REF, EXPECTED_SHA, TINYASSETS_CANARY_* (read by the script).
  set -euo pipefail
  if [ "$DEPLOY_TARGET" = local ]; then
    python scripts/roll_owner_boxes.py --image-ref "${IMAGE_REF}" --local \
      --origin "${DEPLOY_APP_BASE_URL}" \
      --expected-sha "${EXPECTED_SHA}" --receipt "${RUNNER_TEMP}/owner-box-roll.json"
  else
    python scripts/roll_owner_boxes.py --image-ref "${IMAGE_REF}" \
      --ssh-target "${DO_SSH_USER}@${DO_DROPLET_HOST}" --ssh-key "${DEPLOY_SSH_KEY}" \
      --origin "${DEPLOY_APP_BASE_URL}" \
      --expected-sha "${EXPECTED_SHA}" --receipt "${RUNNER_TEMP}/owner-box-roll.json"
  fi
}

phase_open_router_traffic() {
  remote \
    "sudo python3 -" < scripts/open_router_traffic.py
}

phase_first_platform_backup() {
  remote \
    "sudo systemctl start tinyassets-backup.service"
}

phase_snapshot_candidate_evidence() {
  set +e
  REMOTE_TIMEOUT=25s REMOTE_SSH_OPTS="-o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=2" \
    remote "sudo bash /tmp/snapshot_candidate_evidence.sh"
  echo "ssh_rc=$?" >> "$GITHUB_OUTPUT"
  exit 0
}

phase_rollback_public_canary() {
  # Env: PREV_IMAGE.
  set -euo pipefail
  echo "::error::a public surface (MCP canary, app probe, or core canary) went red after a fresh deploy — rolling back to the captured previous image"
  if [ -z "${PREV_IMAGE}" ]; then
    echo "::error::no previous image captured; manual rollback required"; exit 3
  fi
  # --restore-bundle, not a plain invocation: the forward deploy installed
  # a new compose.yml / vector config / systemd unit inside its
  # transaction, so converging the previous IMAGE against the new CONFIG
  # would roll back half the change. This reinstalls the snapshot named by
  # /var/lib/tinyassets-deploy/bundle-previous first, then converges
  # PREV_IMAGE (and force-recreates tinyassets-logs if the restore moved a
  # vector input). It never installs the staged bundle — that is what it
  # is undoing.
  remote \
    "sudo HEALTH_TIMEOUT=180 bash /tmp/deploy_fail_safe.sh --restore-bundle '${PREV_IMAGE}'"
}

phase_sanitize_candidate_diagnostics() {
  # Env: TARGET_IMAGE_REF, TARGET_REVISION.
  set +e
  evidence_dir="${RUNNER_TEMP}/candidate-startup-diagnostics"
  state_raw="${RUNNER_TEMP}/ta-candidate.state"
  raw_log="${RUNNER_TEMP}/ta-candidate.log"
  cid_file="${RUNNER_TEMP}/ta-candidate.cid"
  mkdir -p "${evidence_dir}"
  printf '{"candidate_identity_match":false,"capture":"unavailable"}\n' \
      > "${evidence_dir}/daemon-state.json"
  : > "${raw_log}"
  : > "${cid_file}"

  REMOTE_TIMEOUT=40s REMOTE_SSH_OPTS="-o ConnectTimeout=10" copy_from \
      /tmp/ta-candidate.cid \
      /tmp/ta-candidate.state \
      /tmp/ta-candidate.log \
      "${RUNNER_TEMP}/" 2>/dev/null

  container_id="$(head -c 128 "${cid_file}" 2>/dev/null | tr -dc 'a-f0-9')"
  candidate_identity_match=false
  if [ -n "${container_id}" ] && [ -s "${state_raw}" ]; then
    if python scripts/sanitize_startup_diagnostics.py \
        --state \
        --target-revision "${TARGET_REVISION}" \
        --target-image-ref "${TARGET_IMAGE_REF}" \
        < "${state_raw}" \
        > "${evidence_dir}/daemon-state.json" 2>/dev/null; then
      candidate_identity_match=true
    fi
  fi
  if [ "${candidate_identity_match}" != "true" ]; then
    : > "${raw_log}"
  fi
  python scripts/sanitize_startup_diagnostics.py \
      < "${raw_log}" \
      > "${evidence_dir}/daemon-signals.json"
  rm -f "${raw_log}" "${state_raw}" "${cid_file}"

  CONTAINER_ID="${container_id}" python - \
      "${evidence_dir}/manifest.json" \
      "${evidence_dir}/daemon-state.json" <<'PY'
import datetime
import json
import os
import pathlib
import re
import sys

revision = os.environ["TARGET_REVISION"]
if not re.fullmatch(r"[0-9a-f]{40}", revision):
    revision = "unavailable"
container_id = os.environ.get("CONTAINER_ID") or ""
if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
    container_id = "unavailable"
state = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
manifest = {
    "schema_version": 2,
    "run_id": int(os.environ["GITHUB_RUN_ID"]),
    "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
    "target_revision": revision,
    "container_id": container_id,
    "candidate_identity_match": state.get("candidate_identity_match") is True,
    "captured_utc": datetime.datetime.now(datetime.UTC).isoformat(),
}
pathlib.Path(sys.argv[1]).write_text(
    json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY
  exit 0
}

phase_publish_release_receipt() {
  # Env: GIT_SHA, IMAGE_TAG, IMAGE_DIGEST, IMAGE_REF, DEPLOYED_IMAGE, PREV_IMAGE,
  #      BUILD_RUN_ID; GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_ACTOR,
  #      GITHUB_EVENT_NAME from the runner.
  set -euo pipefail
  now="$(date -u +%Y-%m-%dT%H:%M:%S.000000Z)"
  cat > release-state.json <<EOF
{"receipt_available":true,"git_sha":"${GIT_SHA}","image_tag":"${IMAGE_TAG}","image_digest":"${IMAGE_DIGEST}","build_run_id":"${BUILD_RUN_ID}","build_run_url":"https://github.com/${GITHUB_REPOSITORY}/actions/runs/${BUILD_RUN_ID}","deploy_run_id":"${GITHUB_RUN_ID}","deploy_run_url":"https://github.com/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}","schema_migration_rev":"not_applicable","canary_bundle_status":"not_run","deployed_at":"${now}","rollback_target":"${PREV_IMAGE}","actor":"${GITHUB_ACTOR}","repository":"${GITHUB_REPOSITORY}","workflow_event":"${GITHUB_EVENT_NAME}","extra":{"active_git_sha":"${GIT_SHA}","active_identity_status":"running_healthy","active_image_digest":"${IMAGE_DIGEST}","active_image_ref":"${DEPLOYED_IMAGE}","active_source_provenance":"digest_revision_label","image_ref":"${IMAGE_REF}","image_mutation_started":true,"outcome":"succeeded","production_mutation_started":true,"release_state_version":2,"rollback_attempted":false,"rollback_reason":"not_needed","rollback_result":"not_attempted","running_image_ref":"${DEPLOYED_IMAGE}","forward_canary_status":"passed","forward_deploy_status":"succeeded","terminal_at":"${now}"}}
EOF
  python scripts/release_containment.py release-state.json
  copy_to release-state.json /tmp/release-state.json
  remote \
    "vol=\$(sudo docker volume inspect tinyassets-data --format '{{ .Mountpoint }}'); \
     sudo install -m 0644 -o root -g root /tmp/release-state.json \"\${vol}/release-state.json\""
  echo "published release-state git_sha=${GIT_SHA}"
}

phase_verify_deployed_receipt() {
  # Env: GIT_SHA, TINYASSETS_WIKI_CANARY_TOKEN.
  set -euo pipefail
  # --report-provenance is a record-only diagnostic printed from the same
  # pulse response this assertion already fetches (openspec change
  # cloud-only-runtime-admission, task 5). It does not affect the exit
  # code, and an `unknown` verdict is the absence of an observation --
  # never a pass of cloud acceptance.
  python scripts/deployed_sha.py \
    --url "${DEPLOY_MCP_URL}" \
    --report-provenance \
    --assert-contains "${GIT_SHA}"
}

phase_enforce_image_retention() {
  # Env: RETENTION_HELPER.
  set -euo pipefail
  remote \
      "sudo systemd-run --quiet --wait --pipe --collect -p EnvironmentFile=/etc/tinyassets/env /usr/bin/python3 ${RETENTION_HELPER} --apply"
}

phase_retire_platform_logins() {
  set -euo pipefail
  copy_to \
      deploy/retire_platform_llm_logins.sh deploy/install-tinyassets-env.sh \
      /tmp/
  remote \
    "sudo bash /tmp/retire_platform_llm_logins.sh /tmp/install-tinyassets-env.sh"
}

# Phase names, in production order. tests/test_deploy_host_driver.py reads
# this list and checks both workflows against it.
PHASES=(
  capture-current-image
  live-preflight
  install-request-hmac
  install-oauth-credentials
  install-interchange-hmac
  validate-host-hmac-pair
  scrub-env-overrides
  stage-bundle
  prepare-data-volume
  sync-canary-bearer
  sync-canary-door
  install-expected-instance
  disk-prepare
  wait-for-turns
  disk-recheck
  install-boxhostd
  fail-safe-deploy
  public-mcp-canary
  public-app-probe
  roll-owner-boxes
  open-router-traffic
  first-platform-backup
  snapshot-candidate-evidence
  rollback-public-canary
  sanitize-candidate-diagnostics
  publish-release-receipt
  verify-deployed-receipt
  enforce-image-retention
  retire-platform-logins
)

if [ "$PHASE" = --list ]; then
  printf '%s\n' "${PHASES[@]}"
  exit 0
fi
for known in "${PHASES[@]}"; do
  if [ "$known" = "$PHASE" ]; then
    "phase_${PHASE//-/_}" "$@"
    exit $?
  fi
done
echo "::error::unknown deploy phase '${PHASE}'" >&2
exit 2
