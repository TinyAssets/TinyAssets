#!/usr/bin/env bash
# docker-entrypoint.sh - container startup shim.
#
# 1. Detect silently-empty env_file and emit canonical ENV-UNREADABLE
#    markers to stderr so p0-outage-triage can grep and repair without
#    an SSH shell. Navigator 2026-04-22 section b layer-3.
# 2. Strip every platform-held model or GitHub push credential from the
#    environment, unconditionally. The platform has no LLM (AGENTS.md Hard
#    Rule 15) and pushes with no token of its own: no host login, key,
#    subscription bundle, push map or opt-in switch may reach the daemon. A universe's provider child gets its own credentials from that
#    universe (tinyassets/providers/base.py subprocess_env_for_provider),
#    never from this process.
# 3. Fail loud if required static data files are missing from the image.
# 4. exec the passed CMD (preserves tini PID-1 signal forwarding).
#
# Placed before CMD so operators can override CMD freely.

set -euo pipefail

# ---------------------------------------------------------------------
# ENV-UNREADABLE canary
# ---------------------------------------------------------------------
# The systemd unit's ExecStartPre catches the dominant failure shape
# (/etc/tinyassets/env not readable by user=tinyassets on the host). This
# entrypoint-level check catches an adjacent subclass: compose read the
# env_file, but the file was empty or stripped, so the container boots
# with no real env, silently broken.
#
# Heuristic: at least one of the required secrets must be non-empty. An
# all-empty env indicates compose silently passed an empty file. The
# ENV-UNREADABLE marker keeps the grep class the same regardless of
# which layer detected the problem.
# The daemon no longer receives the first two (it loads daemon.env, which
# drops the platform's own secrets; docs/concerns/
# 2026-10-02-platform-secrets-in-daemon-env.md). The canary bearer is synced
# into the env file by every deploy and the healthcheck needs it, so it is the
# daemon's live sentinel alongside the image pin.
_env_sentinels=(
    CLOUDFLARE_TUNNEL_TOKEN
    SUPABASE_DB_URL
    TINYASSETS_IMAGE
    TINYASSETS_WIKI_CANARY_TOKEN
)
_any_set=0
for _name in "${_env_sentinels[@]}"; do
    if [[ -n "${!_name:-}" ]]; then
        _any_set=1
        break
    fi
done
if [[ "${_any_set}" -eq 0 ]]; then
    # All sentinel env vars empty. compose env_file silently empty/unreadable.
    echo "ENV-UNREADABLE: entrypoint saw no populated secrets; compose env_file likely empty or unreadable" >&2
    echo "ENV-UNREADABLE: expected at least one of ${_env_sentinels[*]} to be set" >&2
    exit 1
fi

# ---------------------------------------------------------------------
# Platform-held credentials: strip, never seed
# ---------------------------------------------------------------------
# Before 2026-09-24 this block kept platform Codex and Claude CLI logins on
# the data volume, seeded them from base64 bundles, and let an opt-in switch
# admit API keys; the host env file also carried a platform GitHub push map.
# The platform has no LLM (Hard Rule 15) and pushes with no token of its own,
# so all of that is gone. What remains is a defence: if an env file, a stale
# compose file or an operator ever passes one of these names in, the daemon
# never sees it. Names are logged; values never are.
#
# tests/test_no_platform_llm_credentials.py and
# tests/test_no_platform_github_push_credential.py require this array to be
# the ONLY place in this file that names these variables.
_platform_credential_env=(
    CODEX_HOME
    CLAUDE_CONFIG_DIR
    CLAUDE_CODE_OAUTH_TOKEN
    OPENAI_API_KEY
    ANTHROPIC_API_KEY
    ANTHROPIC_BASE_URL
    GEMINI_API_KEY
    GOOGLE_API_KEY
    GROQ_API_KEY
    XAI_API_KEY
    TINYASSETS_ALLOW_API_KEY_PROVIDERS
    TINYASSETS_CODEX_AUTH_JSON_B64
    TINYASSETS_CLAUDE_CREDENTIALS_JSON_B64
    WORKFLOW_CODEX_AUTH_JSON_B64
    WORKFLOW_CLAUDE_CREDENTIALS_JSON_B64
    TINYASSETS_GITHUB_PUSH_CAPABILITIES
    TINYASSETS_GITHUB_PR_CAPABILITIES
    GH_TOKEN
    GITHUB_TOKEN
)
for _name in "${_platform_credential_env[@]}"; do
    if [[ -n "${!_name+set}" ]]; then
        echo "[entrypoint] removing ${_name}: the platform holds no model or push credential of its own" >&2
        unset "${_name}"
    fi
done

_tinyassets_bash_path() {
    local _path="${1:-}"
    if [[ "${_path}" =~ ^([A-Za-z]):([\\/].*)$ ]]; then
        if command -v cygpath >/dev/null 2>&1; then
            cygpath -u "${_path}"
        else
            local _drive
            local _prefix
            local _rest
            _drive="$(printf '%s' "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]')"
            _rest="${BASH_REMATCH[2]//\\//}"
            _rest="${_rest#/}"
            _prefix="/${_drive}"
            if [[ -d "/mnt/${_drive}" ]]; then
                _prefix="/mnt/${_drive}"
            fi
            printf '%s/%s\n' "${_prefix}" "${_rest}"
        fi
    else
        printf '%s\n' "${_path}"
    fi
}

_tinyassets_package_root="$(_tinyassets_bash_path "${TINYASSETS_PACKAGE_ROOT:-/app}")"
_required_data_files=(
    data/world_rules.lp
)

for _rel in "${_required_data_files[@]}"; do
    _expected="${_tinyassets_package_root}/${_rel}"
    if [[ ! -f "${_expected}" ]]; then
        echo "DATA-FILE-MISSING: ${_rel} (expected at ${_expected})" >&2
        exit 1
    fi
done

exec "$@"
