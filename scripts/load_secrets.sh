#!/usr/bin/env bash
# load_secrets.sh — pull workflow secrets from a vault into the current shell.
#
# Source (don't execute) this so exports land in the caller's shell:
#   set -a; source scripts/load_secrets.sh; set +a
#
# OR explicit:
#   eval "$(scripts/load_secrets.sh --emit-exports)"
#
# Vendor selection
# ----------------
# TINYASSETS_SECRETS_VENDOR chooses the backend. Supported:
#   1password       — uses `op` CLI. Default. Best UX.
#   bitwarden       — uses `bw` CLI. OSS-friendly alternative.
#   plaintext       — reads $HOME/workflow-secrets.env. Migration-period
#                     opt-out ONLY; to be removed once vault is cut over.
#
# Migration opt-out
# -----------------
# During the vault cutover period, set TINYASSETS_SECRETS_PLAINTEXT_FALLBACK=1
# to accept the plaintext file when the chosen vendor CLI isn't installed.
# Remove this opt-out after the host confirms the vault works.
#
# Errors
# ------
# Fails loudly if:
#   - the requested vendor CLI isn't installed (no silent plaintext drift)
#   - the vendor session isn't authenticated
#   - any required key from scripts/secrets_keys.txt is missing from the vault
#
# Exit codes (when invoked as a script, not sourced):
#   0  all keys exported successfully
#   10 vendor CLI missing
#   11 vendor session not authenticated
#   12 secrets_keys.txt missing or empty
#   13 one or more keys not found in vault
#   14 plaintext fallback requested but file missing / unreadable

set -u

_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_KEYS_FILE="${_REPO_ROOT}/scripts/secrets_keys.txt"
if [ "${TINYASSETS_SECRETS_PROFILE:-}" = "canary" ]; then
  _KEYS_FILE="${_REPO_ROOT}/scripts/canary_secret_keys.txt"
fi
_PLAINTEXT_PATH="${TINYASSETS_SECRETS_PLAINTEXT_PATH:-${HOME}/workflow-secrets.env}"
_VENDOR="${TINYASSETS_SECRETS_VENDOR:-1password}"
_VAULT_NAME="${TINYASSETS_SECRETS_VAULT:-workflow}"
_EMIT_EXPORTS=0

for arg in "$@"; do
  case "$arg" in
    --emit-exports) _EMIT_EXPORTS=1 ;;
    --help|-h)
      sed -n '2,40p' "${BASH_SOURCE[0]}"
      return 0 2>/dev/null || exit 0
      ;;
  esac
done

_fail() {
  # Exit codes only matter when this script is *executed*; when sourced,
  # `return` is the right verb and `exit` would kill the caller shell.
  echo "[load_secrets] ERROR: $2" >&2
  return "$1" 2>/dev/null || exit "$1"
}

_read_keys() {
  if [ ! -f "${_KEYS_FILE}" ]; then
    _fail 12 "keys file missing: ${_KEYS_FILE}"
    return $?
  fi
  grep -v -E '^\s*(#|$)' "${_KEYS_FILE}" | awk '{print $1}'
}

_emit_or_export() {
  # _emit_or_export KEY VALUE
  local k="$1"
  local v="$2"
  if [ "${_EMIT_EXPORTS}" = "1" ]; then
    # Single-quote-safe shell escaping.
    printf "export %s='%s'\n" "$k" "${v//\'/\'\\\'\'}"
  else
    export "$k=$v"
  fi
}

# --- 1Password --------------------------------------------------------

_load_1password() {
  if ! command -v op >/dev/null 2>&1; then
    _fail 10 "1Password CLI 'op' not installed. Install: https://developer.1password.com/docs/cli/get-started/"
    return $?
  fi
  # `op whoami` exits 0 when signed in. Without signin, callers need to
  # run `op signin` first (interactive) or set OP_SERVICE_ACCOUNT_TOKEN.
  if ! op whoami >/dev/null 2>&1; then
    _fail 11 "1Password session not authenticated. Run: eval \$(op signin)"
    return $?
  fi
  local missing=()
  while IFS= read -r key; do
    [ -z "$key" ] && continue
    # Field 'password' is the 1Password convention for single-credential items.
    local value
    value=$(op item get "${key}" --vault "${_VAULT_NAME}" --fields password \
                --reveal 2>/dev/null) || true
    if [ -z "$value" ]; then
      missing+=("$key")
      continue
    fi
    _emit_or_export "$key" "$value"
  done < <(_read_keys)
  if [ "${#missing[@]}" -gt 0 ]; then
    _fail 13 "keys not found in vault '${_VAULT_NAME}': ${missing[*]}"
    return $?
  fi
  echo "[load_secrets] 1Password: loaded $(_read_keys | wc -l | tr -d ' ') key(s) from vault '${_VAULT_NAME}'" >&2
}

# --- Bitwarden --------------------------------------------------------

_load_bitwarden() {
  if ! command -v bw >/dev/null 2>&1; then
    _fail 10 "Bitwarden CLI 'bw' not installed. Install: https://bitwarden.com/help/cli/"
    return $?
  fi
  # Detect Python. On fresh Windows installs, `python3` resolves to a
  # Microsoft Store shim that errors instead of running Python; probe
  # with `-c 'import sys'` to confirm. Fall back to `python`.
  local _py=""
  if command -v python3 >/dev/null 2>&1 && python3 -c "import sys" >/dev/null 2>&1; then
    _py=python3
  elif command -v python >/dev/null 2>&1 && python -c "import sys" >/dev/null 2>&1; then
    _py=python
  else
    _fail 10 "No usable python interpreter on PATH (need python3 or python)"
    return $?
  fi
  # `bw status` returns JSON with a "status" field.
  local status
  status=$(bw status 2>/dev/null | "$_py" -c 'import json,sys; d=json.load(sys.stdin); print(d.get("status",""))' 2>/dev/null || echo "")
  if [ "$status" != "unlocked" ]; then
    _fail 11 "Bitwarden vault not unlocked. Run: export BW_SESSION=\$(bw unlock --raw)"
    return $?
  fi
  local missing=()
  while IFS= read -r key; do
    [ -z "$key" ] && continue
    # `bw get password <item-name>` returns just the password string.
    # Items in the "tinyassets" folder/collection are named after the key.
    local value
    value=$(bw get password "${key}" 2>/dev/null) || true
    if [ -z "$value" ]; then
      missing+=("$key")
      continue
    fi
    _emit_or_export "$key" "$value"
  done < <(_read_keys)
  if [ "${#missing[@]}" -gt 0 ]; then
    _fail 13 "keys not found in Bitwarden: ${missing[*]}"
    return $?
  fi
  echo "[load_secrets] Bitwarden: loaded $(_read_keys | wc -l | tr -d ' ') key(s)" >&2
}

# --- plaintext (migration opt-out) -----------------------------------

_load_plaintext() {
  if [ ! -f "${_PLAINTEXT_PATH}" ]; then
    _fail 14 "plaintext file not readable: ${_PLAINTEXT_PATH}"
    return $?
  fi
  # Source the file in a subshell to isolate, then re-emit only keys we
  # care about. This prevents unrelated env vars leaking into the caller.
  local _ls_tmp
  _ls_tmp=$(mktemp) || return 14
  # shellcheck disable=SC1090
  ( set -a; source "${_PLAINTEXT_PATH}"; set +a
    while IFS= read -r key; do
      [ -z "$key" ] && continue
      # Use printf %s to avoid interpreting \n etc. in the value.
      printf '%s\0%s\0' "$key" "${!key-__UNSET__}"
    done < <(_read_keys)
  ) > "${_ls_tmp}"
  local missing=()
  while IFS= read -r -d '' key; do
    IFS= read -r -d '' value
    if [ "$value" = "__UNSET__" ]; then
      missing+=("$key")
      continue
    fi
    _emit_or_export "$key" "$value"
  done < "${_ls_tmp}"
  rm -f "${_ls_tmp}"
  if [ "${#missing[@]}" -gt 0 ]; then
    _fail 13 "keys missing in plaintext file: ${missing[*]}"
    return $?
  fi
  echo "[load_secrets] plaintext: loaded $(_read_keys | wc -l | tr -d ' ') key(s) from ${_PLAINTEXT_PATH}" >&2
  echo "[load_secrets] WARN: plaintext mode is migration-period only. Cut over to a vault and unset TINYASSETS_SECRETS_VENDOR=plaintext." >&2
}

# --- dispatch ---------------------------------------------------------

_dispatch_rc=0
case "${_VENDOR}" in
  1password) _load_1password || _dispatch_rc=$? ;;
  bitwarden) _load_bitwarden || _dispatch_rc=$? ;;
  plaintext) _load_plaintext || _dispatch_rc=$? ;;
  *)
    # Unknown vendor. If plaintext-fallback opt-out is enabled and the
    # file exists, fall through to plaintext with a warning. Otherwise
    # fail loudly — no silent drift.
    if [ "${TINYASSETS_SECRETS_PLAINTEXT_FALLBACK:-0}" = "1" ] \
       && [ -f "${_PLAINTEXT_PATH}" ]; then
      echo "[load_secrets] WARN: unknown vendor '${_VENDOR}'; falling back to plaintext (TINYASSETS_SECRETS_PLAINTEXT_FALLBACK=1)" >&2
      _load_plaintext || _dispatch_rc=$?
    else
      _fail 10 "unknown TINYASSETS_SECRETS_VENDOR='${_VENDOR}' (expected: 1password | bitwarden | plaintext)" \
        || _dispatch_rc=$?
    fi
    ;;
esac

# Propagate the vendor/dispatch failure to the caller. When sourced,
# `return` is the right verb (doesn't kill caller shell). When executed,
# `exit` ensures a non-zero exit code the shell can branch on.
if [ "${_dispatch_rc}" -ne 0 ]; then
  return "${_dispatch_rc}" 2>/dev/null || exit "${_dispatch_rc}"
fi
