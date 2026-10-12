#!/usr/bin/env bash
# run-tunnel.sh — start a Cloudflare tunnel pointing at the local TinyAssets daemon.
#
# Per openspec/specs/uptime-and-alarms/spec.md Row C.
# Provider-agnostic: runs on any Linux host (Docker, systemd, direct exec).
#
# Supports both Cloudflare tunnel auth shapes:
#   (a) Token-based (dashboard-managed ingress rules)
#   (b) Config-file-based (ingress rules in deploy/cloudflared.yml)
#
# Select the shape at invocation time by which env vars are set. If both
# are present, token-based wins (matches production 2026-04-19 shape);
# supply only what you want.
#
# Required env (shape a — token):
#     TUNNEL_TOKEN              Cloudflare-issued tunnel token (get from dashboard).
#
# Required env (shape b — config file):
#     TUNNEL_ID                 UUID of the tunnel (cloudflared tunnel list).
#     TUNNEL_CREDENTIALS_FILE   Path to the downloaded tunnel credentials JSON.
#     HOSTNAME                  Public hostname the tunnel serves (e.g. mcp.tinyassets.io).
#
# Optional env (both shapes):
#     ORIGIN_PORT               Local daemon port (default 8001).
#     CLOUDFLARED_BIN           Path to cloudflared binary (default 'cloudflared' on PATH).
#
# Exit codes:
#     0   Tunnel exited cleanly (only reachable on SIGTERM — cloudflared runs until killed).
#     1   Config validation failed — see stderr for which env var is missing.
#     2   cloudflared binary not found.
#
# Notes:
#  - Never logs TUNNEL_TOKEN or credentials-file contents. Token is passed
#    via --token on the CLI (already in the process command line visible
#    to local uid-matching processes; that's cloudflared's own design).
#  - Intended for systemd units, Docker entrypoints, or interactive ops.
#    Don't fork to background; supervisors want foreground PID control.

set -euo pipefail

ORIGIN_PORT="${ORIGIN_PORT:-8001}"
CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-cloudflared}"

# ----- binary check -------------------------------------------------------

if ! command -v "${CLOUDFLARED_BIN}" >/dev/null 2>&1; then
  echo "run-tunnel: cloudflared binary not found (looked for '${CLOUDFLARED_BIN}')." >&2
  echo "  Install from https://github.com/cloudflare/cloudflared/releases or" >&2
  echo "  via 'brew install cloudflared' / your distro package manager." >&2
  exit 2
fi

# ----- auth shape selection ----------------------------------------------

if [[ -n "${TUNNEL_TOKEN:-}" ]]; then
  echo "run-tunnel: token-based auth detected; dashboard-managed ingress."
  echo "run-tunnel: origin port ${ORIGIN_PORT} (informational; ingress routed via dashboard)."
  exec "${CLOUDFLARED_BIN}" tunnel run --token "${TUNNEL_TOKEN}"
fi

# Shape (b) — config-file auth.
# Note: bash defines HOSTNAME implicitly as the machine's hostname.
# Don't trust the non-empty default; require an explicit caller-set value
# (check against the machine's hostname OR demand a per-invocation set).
missing=()
[[ -z "${TUNNEL_ID:-}" ]] && missing+=("TUNNEL_ID")
[[ -z "${TUNNEL_CREDENTIALS_FILE:-}" ]] && missing+=("TUNNEL_CREDENTIALS_FILE")
# HOSTNAME specifically — force the caller to set it rather than
# silently using the bash-default machine hostname.
if [[ -z "${TINYASSETS_PUBLIC_HOSTNAME:-}" ]] && [[ "${HOSTNAME:-}" == "$(hostname 2>/dev/null || echo "")" ]]; then
  missing+=("HOSTNAME (or TINYASSETS_PUBLIC_HOSTNAME — bash's default HOSTNAME is the machine name, not the public URL)")
fi
# Prefer the explicit var if provided; fall back to HOSTNAME if the
# caller did set it to a domain (e.g. mcp.tinyassets.io).
if [[ -n "${TINYASSETS_PUBLIC_HOSTNAME:-}" ]]; then
  HOSTNAME="${TINYASSETS_PUBLIC_HOSTNAME}"
fi

if (( ${#missing[@]} > 0 )); then
  echo "run-tunnel: neither TUNNEL_TOKEN (shape a) nor the full shape-b env set." >&2
  echo "  Missing for shape b: ${missing[*]}" >&2
  echo "  Set TUNNEL_TOKEN for dashboard-managed tunnels, OR set TUNNEL_ID +" >&2
  echo "  TUNNEL_CREDENTIALS_FILE + HOSTNAME for config-file-managed tunnels." >&2
  exit 1
fi

if [[ ! -r "${TUNNEL_CREDENTIALS_FILE}" ]]; then
  echo "run-tunnel: TUNNEL_CREDENTIALS_FILE '${TUNNEL_CREDENTIALS_FILE}' is not readable." >&2
  exit 1
fi

# Substitute env vars into the config template and write to a temp file.
# envsubst keeps the template clean in version control; the temp file is
# what cloudflared actually reads. Cleanup on exit.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${REPO_ROOT}/deploy/cloudflared.yml"

if [[ ! -r "${TEMPLATE}" ]]; then
  echo "run-tunnel: template '${TEMPLATE}' missing or unreadable." >&2
  exit 1
fi

if ! command -v envsubst >/dev/null 2>&1; then
  echo "run-tunnel: envsubst not found (install gettext-base on Debian/Ubuntu)." >&2
  exit 2
fi

RENDERED="$(mktemp --suffix=.yml)"
trap 'rm -f "${RENDERED}"' EXIT

# Only substitute the known vars — don't let stray $ in comments get expanded.
export TUNNEL_ID TUNNEL_CREDENTIALS_FILE HOSTNAME ORIGIN_PORT
envsubst '${TUNNEL_ID} ${TUNNEL_CREDENTIALS_FILE} ${HOSTNAME} ${ORIGIN_PORT}' \
  < "${TEMPLATE}" > "${RENDERED}"

echo "run-tunnel: config-file auth."
echo "run-tunnel: tunnel=${TUNNEL_ID} hostname=${HOSTNAME} origin=http://localhost:${ORIGIN_PORT}"
exec "${CLOUDFLARED_BIN}" tunnel --config "${RENDERED}" run "${TUNNEL_ID}"
