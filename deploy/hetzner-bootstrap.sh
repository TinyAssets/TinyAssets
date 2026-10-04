#!/usr/bin/env bash
# hetzner-bootstrap.sh — idempotent provisioning for a fresh Debian 12/13 Linux VM.
#
# Provider-neutral despite the file name (name kept for git history).
# Verified on: Hetzner Cloud CX22, DigitalOcean Basic Droplet, Linode 1 GB,
# Vultr Cloud Compute. Debian 13 x64 was reverified on a fresh DigitalOcean
# Droplet by DR run 30066361115 (2026-07-24). Any Debian 12/13 VM with root
# SSH + outbound internet works. Bootstrap is idempotent; provider dashboards
# (Hetzner Cloud Console vs DO Droplets UI vs etc.) do not change the
# script's execution.
#
# Current production target (2026-04-20): DigitalOcean Basic Droplet.
# Pivoted from Hetzner mid-cutover due to a Hetzner US individual-signup
# form bug; Hetzner remains the documented fallback per exec plan §2.
#
# Self-host migration Row D per
# docs/exec-plans/active/2026-04-20-selfhost-uptime-migration.md.
#
# Idempotent: safe to re-run. Skips steps whose end-state is already
# reached. No destructive actions; existing /opt/tinyassets or
# /etc/tinyassets content is preserved.
#
# Usage (on the target box, as root):
#   curl -fsSL https://raw.githubusercontent.com/TinyAssets/TinyAssets/main/deploy/hetzner-bootstrap.sh -o /tmp/bootstrap.sh
#   sudo bash /tmp/bootstrap.sh
#
# OR (local-clone):
#   sudo bash deploy/hetzner-bootstrap.sh
#
# Post-bootstrap host action:
#   1. Fill /etc/tinyassets/env with real secrets (CLOUDFLARE_TUNNEL_TOKEN,
#      SUPABASE_*).
#   2. systemctl start tinyassets-daemon
#   3. Verify: python3 /opt/tinyassets-host-uptime/current/scripts/mcp_public_canary.py
#      --url https://tinyassets.io/mcp --verbose

set -euo pipefail

# Must run as root.
if [[ "${EUID}" -ne 0 ]]; then
    echo "bootstrap: must run as root (try: sudo bash $0)" >&2
    exit 1
fi

TINYASSETS_USER="tinyassets"
TINYASSETS_UID=1001
TINYASSETS_HOME="/opt/tinyassets"
ENV_DIR="/etc/tinyassets"
REPO_URL="https://github.com/TinyAssets/TinyAssets.git"
REPO_REF="main"

log() { echo "[bootstrap] $*"; }

service_repo_git() {
    sudo -u "${TINYASSETS_USER}" -- git -C "${TINYASSETS_HOME}" "$@"
}

# ----- 1. apt baseline ----------------------------------------------------

# Fresh DO/Hetzner/Linode Droplets run cloud-init + unattended-upgrades
# at first boot. Racing them produces
#   "Could not get lock /var/lib/apt/lists/lock. It is held by process N"
# and the bootstrap exits 100. Wait for both to release before we touch apt.
if command -v cloud-init >/dev/null 2>&1; then
    log "waiting for cloud-init to finish..."
    cloud-init status --wait >/dev/null 2>&1 || true
fi
log "waiting for apt/dpkg locks to clear..."
for i in $(seq 1 180); do
    if ! pgrep -x apt-get >/dev/null 2>&1 \
         && ! pgrep -x dpkg >/dev/null 2>&1 \
         && ! pgrep -x unattended-upgr >/dev/null 2>&1; then
        break
    fi
    if [[ "${i}" -eq 180 ]]; then
        log "WARN: apt/dpkg still running after 180s — proceeding anyway"
    fi
    sleep 1
done

log "apt update + base packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    ca-certificates \
    curl \
    git \
    gnupg \
    python3 \
    python3-pip \
    jq \
    rclone \
    sudo \
    util-linux \
    zstd

# ----- 2. Docker CE + compose plugin --------------------------------------

if ! command -v docker >/dev/null 2>&1; then
    log "installing Docker CE..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg \
        -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    codename="$(. /etc/os-release && echo "${VERSION_CODENAME}")"
    cat > /etc/apt/sources.list.d/docker.list <<EOF
deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian ${codename} stable
EOF
    apt-get update -qq
    apt-get install -y -qq \
        docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
    log "Docker installed: $(docker --version)"
else
    log "Docker already installed: $(docker --version)"
fi

# ----- 2b. Docker log-rotation --------------------------------------------

# Without log-rotation docker containers write unbounded JSON logs to
# /var/lib/docker/containers/*/. On a 960 MB / 25 GB droplet this fills
# disk within days of normal daemon activity. Matches live prod config
# applied 2026-04-21.
DOCKER_DAEMON_JSON="/etc/docker/daemon.json"
DOCKER_LOG_CONFIG='{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"}}'
if [[ ! -f "${DOCKER_DAEMON_JSON}" ]] || \
   ! python3 -c "
import json, sys
d = json.load(open('${DOCKER_DAEMON_JSON}'))
lo = d.get('log-opts', {})
assert d.get('log-driver') == 'json-file'
assert lo.get('max-size') == '10m'
assert lo.get('max-file') == '3'
" 2>/dev/null; then
    log "writing ${DOCKER_DAEMON_JSON} (log-rotation: 10m × 3)..."
    echo "${DOCKER_LOG_CONFIG}" > "${DOCKER_DAEMON_JSON}"
    # Reload Docker to pick up the new log config. Skip if daemon not yet
    # running (first-boot before `systemctl enable --now docker` above).
    if systemctl is-active --quiet docker; then
        systemctl reload-or-restart docker
    fi
else
    log "${DOCKER_DAEMON_JSON} already configured correctly"
fi

# ----- 3. tinyassets user ---------------------------------------------------

if ! id -u "${TINYASSETS_USER}" >/dev/null 2>&1; then
    log "creating ${TINYASSETS_USER} user (uid ${TINYASSETS_UID})..."
    useradd --system \
            --uid "${TINYASSETS_UID}" \
            --home "${TINYASSETS_HOME}" \
            --create-home \
            --shell /usr/sbin/nologin \
            --comment "TinyAssets daemon service account" \
            "${TINYASSETS_USER}"
else
    log "${TINYASSETS_USER} user already exists"
fi

# Add tinyassets user to docker group so it can issue docker compose
# commands via the systemd unit.
if ! id -nG "${TINYASSETS_USER}" | grep -qw docker; then
    log "adding ${TINYASSETS_USER} to docker group..."
    usermod -aG docker "${TINYASSETS_USER}"
fi

# ----- 4a. ensure deploy dir exists before any SCP / git-clone step --------
# DR drill SCPs backup-restore.sh into /opt/tinyassets/deploy/ before git-clone
# runs; the target must exist independently of the clone completing first.
mkdir -p "${TINYASSETS_HOME}/deploy"

# ----- 4. repo checkout at /opt/tinyassets ----------------------------------

if [[ ! -d "${TINYASSETS_HOME}/.git" ]]; then
    log "cloning repo into ${TINYASSETS_HOME}..."
    # Wipe any pre-existing non-git content (e.g. useradd created
    # /opt/tinyassets as home, leaving it empty). Safe because the
    # directory only exists if we just created it.
    rm -rf "${TINYASSETS_HOME}"
    git clone --branch "${REPO_REF}" --depth 1 "${REPO_URL}" "${TINYASSETS_HOME}"
    TINYASSETS_CHECKOUT_SHA="$(git -C "${TINYASSETS_HOME}" rev-parse HEAD)"
else
    log "repo already present at ${TINYASSETS_HOME}; fetching latest..."
    # A prior fresh run may have been interrupted after root cloned the
    # checkout but before its final ownership transfer. Converge ownership
    # before any repeat Git operation so Git always runs as the checkout owner.
    chown -R "${TINYASSETS_USER}:${TINYASSETS_USER}" "${TINYASSETS_HOME}"
    service_repo_git fetch --depth 1 origin "${REPO_REF}"
    service_repo_git reset --hard "origin/${REPO_REF}"
    TINYASSETS_CHECKOUT_SHA="$(service_repo_git rev-parse HEAD)"
fi
if [[ ! "${TINYASSETS_CHECKOUT_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
    echo "bootstrap: checkout HEAD is not a full lowercase commit SHA" >&2
    exit 1
fi
chown -R "${TINYASSETS_USER}:${TINYASSETS_USER}" "${TINYASSETS_HOME}"

# Make the compose.yml reachable at the path the systemd unit expects.
if [[ ! -f "${TINYASSETS_HOME}/compose.yml" ]]; then
    ln -sf "${TINYASSETS_HOME}/deploy/compose.yml" "${TINYASSETS_HOME}/compose.yml"
fi

# ----- 5. /etc/tinyassets env directory -------------------------------------

mkdir -p "${ENV_DIR}"
chown "root:${TINYASSETS_USER}" "${ENV_DIR}"
chmod 750 "${ENV_DIR}"

if [[ ! -f "${ENV_DIR}/env" ]]; then
    log "creating ${ENV_DIR}/env from template (DO NOT FORGET TO FILL IN)..."
    cp "${TINYASSETS_HOME}/deploy/tinyassets-env.template" "${ENV_DIR}/env"
    chown "root:${TINYASSETS_USER}" "${ENV_DIR}/env"
    chmod 640 "${ENV_DIR}/env"
    log "  → edit ${ENV_DIR}/env and fill in CLOUDFLARE_TUNNEL_TOKEN + SUPABASE_* before starting the service"
else
    log "${ENV_DIR}/env already present; leaving contents alone"
fi
# The daemon loads a copy of env with the platform's own secrets removed. The
# helper that renders it is installed where every deploy re-installs it.
install -m 0755 -o root -g root \
    "${TINYASSETS_HOME}/deploy/install-tinyassets-env.sh" /usr/local/sbin/tinyassets-env
/usr/local/sbin/tinyassets-env render-daemon-env

if [[ ! -f "${ENV_DIR}/agent-interchange.env" ]]; then
    log "creating ${ENV_DIR}/agent-interchange.env from daemon-only template..."
    cp "${TINYASSETS_HOME}/deploy/agent-interchange-env.template" \
        "${ENV_DIR}/agent-interchange.env"
    log "  → fill ${ENV_DIR}/agent-interchange.env with a unique canonical-base64 key before starting the service"
else
    log "${ENV_DIR}/agent-interchange.env already present; leaving contents alone"
fi
chown "root:${TINYASSETS_USER}" "${ENV_DIR}/agent-interchange.env"
chmod 640 "${ENV_DIR}/agent-interchange.env"

if [[ ! -f "${ENV_DIR}/request-idempotency.env" ]]; then
    log "creating ${ENV_DIR}/request-idempotency.env from daemon-only template..."
    cp "${TINYASSETS_HOME}/deploy/request-idempotency-env.template" \
        "${ENV_DIR}/request-idempotency.env"
    log "  → fill ${ENV_DIR}/request-idempotency.env once with a distinct canonical-base64 key before starting the service"
else
    log "${ENV_DIR}/request-idempotency.env already present; leaving contents alone"
fi
chown "root:${TINYASSETS_USER}" "${ENV_DIR}/request-idempotency.env"
chmod 640 "${ENV_DIR}/request-idempotency.env"

# ----- 6. systemd unit install --------------------------------------------

SYSTEMD_UNIT="/etc/systemd/system/tinyassets-daemon.service"
if [[ ! -f "${SYSTEMD_UNIT}" ]] \
   || ! cmp -s "${TINYASSETS_HOME}/deploy/tinyassets-daemon.service" "${SYSTEMD_UNIT}"; then
    log "installing tinyassets-daemon.service..."
    cp "${TINYASSETS_HOME}/deploy/tinyassets-daemon.service" "${SYSTEMD_UNIT}"
    systemctl daemon-reload
    systemctl enable tinyassets-daemon
    log "  daemon unit installed + enabled (NOT started — fill env first)"
else
    log "tinyassets-daemon.service already current"
fi

# Install both watchdogs plus backup, prune, and disk-pressure layers from one
# manifest. The installer always repairs disabled/inactive timers even when
# every file is already current.
TINYASSETS_SOURCE_ROOT="${TINYASSETS_HOME}" \
TINYASSETS_SOURCE_SHA="${TINYASSETS_CHECKOUT_SHA}" \
    bash "${TINYASSETS_HOME}/deploy/install-host-uptime-services.sh"

# ----- 7. swap file -------------------------------------------------------

# 960 MB RAM droplets OOM-kill under peak load without swap. 2 GB swapfile
# at /swapfile with vm.swappiness=10 matches live prod config applied
# 2026-04-21. Idempotent: skips each step whose end-state is already reached.
SWAPFILE="/swapfile"
SWAP_SIZE_MB=2048

if [[ ! -f "${SWAPFILE}" ]]; then
    log "creating ${SWAPFILE} (${SWAP_SIZE_MB} MB)..."
    fallocate -l "${SWAP_SIZE_MB}M" "${SWAPFILE}" || \
        dd if=/dev/zero of="${SWAPFILE}" bs=1M count="${SWAP_SIZE_MB}" status=none
    chmod 600 "${SWAPFILE}"
    mkswap "${SWAPFILE}"
    swapon "${SWAPFILE}"
    log "  swap active: $(free -h | grep Swap)"
else
    log "swapfile already exists ($(du -sh "${SWAPFILE}" | cut -f1))"
    # Activate if not already on (e.g. if box rebooted without fstab entry).
    if ! swapon --show | grep -q "${SWAPFILE}"; then
        swapon "${SWAPFILE}"
        log "  swapon ${SWAPFILE} (was off)"
    fi
fi

# Persist in /etc/fstab — idempotent guard prevents duplicate lines.
FSTAB_ENTRY="${SWAPFILE} none swap sw 0 0"
if ! grep -qF "${FSTAB_ENTRY}" /etc/fstab; then
    log "adding swap entry to /etc/fstab..."
    echo "${FSTAB_ENTRY}" >> /etc/fstab
else
    log "/etc/fstab swap entry already present"
fi

# Reduce swappiness so RAM is preferred. Write to sysctl.d for persistence.
SYSCTL_SWAP="/etc/sysctl.d/99-workflow-swap.conf"
if [[ ! -f "${SYSCTL_SWAP}" ]]; then
    echo "vm.swappiness=10" > "${SYSCTL_SWAP}"
    sysctl -p "${SYSCTL_SWAP}" >/dev/null
    log "vm.swappiness=10 set + persisted"
else
    log "vm.swappiness already configured"
fi

log "bootstrap complete."
cat <<EOF

Next steps (host action required):

  1. Fill in secrets, then render the daemon's copy (the unit refuses to
     start on a copy older than the source):
       sudo nano ${ENV_DIR}/env
       sudo tinyassets-env render-daemon-env
     (See deploy/DEPLOY.md for which values go where.)

  2. Generate the daemon-only agent interchange key:
       follow Step 3 in deploy/DEPLOY.md for ${ENV_DIR}/agent-interchange.env

  3. Generate the daemon-only request-admission key (immutable by default):
       follow Step 3 in deploy/DEPLOY.md for ${ENV_DIR}/request-idempotency.env

  4. Start the service:
       sudo systemctl start tinyassets-daemon

  5. Tail logs:
       sudo journalctl -u tinyassets-daemon -f

  4. Verify canary green:
       python3 ${TINYASSETS_HOME}/scripts/mcp_public_canary.py \\
           --url https://tinyassets.io/mcp --verbose

  If the canary comes back exit 0 with [canary] OK, the self-host
  deploy is live. Host machine can now power off + tinyassets.io/mcp
  stays up.

EOF
