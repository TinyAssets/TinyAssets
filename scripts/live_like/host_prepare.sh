#!/usr/bin/env bash
# scripts/live_like/host_prepare.sh — make this machine a droplet-like host for
# the live-like deploy gate (.github/workflows/live-like-gate.yml).
#
#   sudo bash scripts/live_like/host_prepare.sh <caller-inputs.env>
#
# Leaves the host in the state the droplet is in between deploys: the
# tinyassets service account, /etc/tinyassets with every env file the stack
# loads, /opt/tinyassets holding the runtime bundle, the daemon unit, the
# host-uptime services (backup, prune, watchdogs and their timers) installed by
# their production installer, and a loop-backed XFS box store configured the
# way install-boxhostd-preflight.sh accepts one. It installs nothing the
# deploy itself installs (gVisor, boxhostd, the env helper): those are the
# deploy's job, and the gate must see it do them.
#
# The inputs file holds caller-owned values only, all synthetic and generated
# per run by the gate: TINYASSETS_IMAGE (the image the host is "running"),
# BACKUP_DEST (from scripts/live_like/s3_remote.sh up), the HMAC keys, the
# canary bearer and door, the tunnel token, the app-ingress key. Production
# values never reach this script; the gate binds no environment.
#
# Steps follow deploy/hetzner-bootstrap.sh (user, /opt/tinyassets, /etc/tinyassets,
# unit, host-uptime installer) and install-host-services.yml (rclone config and
# BACKUP_DEST), in that order. Throwaway hosts only: refuses unless
# LIVE_LIKE_HOST=1, which only the gate sets.
set -euo pipefail

[ "$(id -u)" = 0 ] || { echo 'host_prepare: root required' >&2; exit 1; }
[ "${LIVE_LIKE_HOST:-}" = 1 ] || { echo 'host_prepare: refusing; LIVE_LIKE_HOST=1 marks a throwaway host' >&2; exit 1; }
inputs="${1:?usage: host_prepare.sh <caller-inputs.env>}"
# shellcheck disable=SC1090
set -a; . "$inputs"; set +a
: "${TINYASSETS_IMAGE:?}" "${BACKUP_DEST:?}" "${CLOUDFLARE_TUNNEL_TOKEN:?}"
: "${TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY:?}" "${TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY:?}"
: "${TINYASSETS_WIKI_CANARY_TOKEN:?}" "${TINYASSETS_APP_INGRESS_HMAC_KEY:?}"

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
log() { printf '[host-prepare] %s\n' "$*"; }

# --- packages the droplet has --------------------------------------------
need=()
command -v mkfs.xfs >/dev/null || need+=(xfsprogs)
command -v rclone >/dev/null || need+=(rclone)
command -v sqlite3 >/dev/null || need+=(sqlite3)
if [ "${#need[@]}" -gt 0 ]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${need[@]}" >/dev/null
fi
docker compose version >/dev/null
systemctl is-system-running >/dev/null 2>&1 || systemctl is-system-running || true

# --- the tinyassets service account (uid/gid 1001, as on the droplet) ----
# A GitHub runner's own account already holds 1001. The droplet's names must
# resolve, so tinyassets is added as a second name for 1001 and listed first
# in /etc/group: `stat %G` then prints tinyassets, which the daemon unit's
# ExecStartPre checks. /etc/passwd keeps the runner first so its sudo works.
if ! getent group tinyassets >/dev/null; then
  if getent group 1001 >/dev/null; then
    groupadd -o -g 1001 tinyassets
    python3 - <<'PY'
from pathlib import Path
p = Path("/etc/group")
lines = p.read_text().splitlines()
mine = [l for l in lines if l.startswith("tinyassets:")]
p.write_text("\n".join(mine + [l for l in lines if not l.startswith("tinyassets:")]) + "\n")
PY
  else
    groupadd -g 1001 tinyassets
  fi
fi
if ! id -u tinyassets >/dev/null 2>&1; then
  useradd -o --system --uid 1001 --gid 1001 --home /opt/tinyassets --no-create-home \
    --shell /usr/sbin/nologin --comment "TinyAssets daemon service account" tinyassets
fi
usermod -aG docker tinyassets
log "tinyassets resolves to uid $(id -u tinyassets) gid $(getent group tinyassets | cut -d: -f3)"

# --- /opt/tinyassets: the runtime bundle the live image was deployed with
#     (BASE_REVISION's), laid out as deploy_fail_safe.sh's BUNDLE_MAP installs it
: "${BASE_REVISION:?}"
bundle="$(mktemp -d)"
trap 'rm -rf -- "$bundle"' EXIT
for f in compose.yml vector.yaml vector-betterstack.yaml vector-entrypoint.sh tinyassets-daemon.service; do
  git -C "$repo" show "${BASE_REVISION}:deploy/$f" > "$bundle/$f"
done
install -d -m 0755 -o tinyassets -g tinyassets /opt/tinyassets /opt/tinyassets/deploy
install -m 0644 -o tinyassets -g tinyassets "$bundle/compose.yml" /opt/tinyassets/compose.yml
for f in compose.yml vector.yaml vector-betterstack.yaml; do
  install -m 0644 -o tinyassets -g tinyassets "$bundle/$f" "/opt/tinyassets/deploy/$f"
done
install -m 0755 -o tinyassets -g tinyassets "$bundle/vector-entrypoint.sh" /opt/tinyassets/deploy/vector-entrypoint.sh
install -m 0644 -o root -g root "$bundle/tinyassets-daemon.service" /etc/systemd/system/tinyassets-daemon.service

# --- /etc/tinyassets ------------------------------------------------------
install -d -m 0750 -o root -g tinyassets /etc/tinyassets
if [ ! -f /etc/tinyassets/env ]; then
  install -m 0640 -o root -g tinyassets "$repo/deploy/tinyassets-env.template" /etc/tinyassets/env
fi
helper="$repo/deploy/install-tinyassets-env.sh"
setenv() { printf '%s' "$2" | bash "$helper" set "$1" >/dev/null; }
setenv TINYASSETS_IMAGE "$TINYASSETS_IMAGE"
setenv CLOUDFLARE_TUNNEL_TOKEN "$CLOUDFLARE_TUNNEL_TOKEN"
setenv TINYASSETS_WIKI_CANARY_TOKEN "$TINYASSETS_WIKI_CANARY_TOKEN"
setenv TINYASSETS_MCP_CANARY_URL "http://127.0.0.1:8001/mcp"
setenv BACKUP_DEST "$BACKUP_DEST"
# The deploy installs these two set-once; the host already holds the same
# values from its earlier deploys, exactly as the droplet does.
for pair in "agent-interchange:TINYASSETS_AGENT_INTERCHANGE_HMAC_KEY" \
            "request-idempotency:TINYASSETS_REQUEST_IDEMPOTENCY_HMAC_KEY"; do
  file="/etc/tinyassets/${pair%%:*}.env"; key="${pair#*:}"
  printf '%s' "${!key}" | TINYASSETS_ENV_FILE="$file" \
    TINYASSETS_LEGACY_ENV_FILE="/etc/tinyassets/no-${pair%%:*}-legacy" \
    bash "$helper" set-once "$key" >/dev/null
  chown root:tinyassets "$file"; chmod 0640 "$file"
done
printf 'TINYASSETS_APP_INGRESS_HMAC_KEY=%s\n' "$TINYASSETS_APP_INGRESS_HMAC_KEY" > /etc/tinyassets/app-ingress.env
chown root:tinyassets /etc/tinyassets/app-ingress.env; chmod 0640 /etc/tinyassets/app-ingress.env
# The broker's rclone file is bind-mounted with create_host_path: false.
[ -f /etc/tinyassets/broker-rclone.conf ] || install -m 0640 -o root -g tinyassets /dev/null /etc/tinyassets/broker-rclone.conf
bash "$helper" render-daemon-env >/dev/null
install -m 0755 -o root -g root "$helper" /usr/local/sbin/tinyassets-env

# The loop store install-boxhostd-preflight.sh accepts by explicit choice: the
# droplet's attached volume does not exist here.
if [ ! -e /etc/tinyassets/box-store.conf ]; then
  printf 'BOX_STORE_DEVICE=%s\n' /var/lib/boxhostd/boxes.img > /etc/tinyassets/box-store.conf
  chmod 0600 /etc/tinyassets/box-store.conf
fi
install -d -m 0755 /var/lib/boxhostd /var/lib/tinyassets-deploy /var/backups/tinyassets

systemctl daemon-reload
systemctl enable tinyassets-daemon >/dev/null 2>&1

# --- host-uptime services, by their production installer -----------------
TINYASSETS_SOURCE_ROOT="$repo" TINYASSETS_SOURCE_SHA="$(git -C "$repo" rev-parse HEAD)" \
  bash "$repo/deploy/install-host-uptime-services.sh"

# --- the backup remote is root's, as install-host-services.yml leaves it ---
cfg=/root/.config/rclone/rclone.conf
[ -f "$cfg" ] && [ "$(stat -c '%U:%G %a' "$cfg")" = "root:root 600" ] \
  || { echo "host_prepare: ${cfg} must be root:root 600 (s3_remote.sh up writes it)" >&2; exit 1; }
rclone lsf "$BACKUP_DEST" --max-depth 1 >/dev/null
log "host ready: image ${TINYASSETS_IMAGE}, backups at ${BACKUP_DEST}"
