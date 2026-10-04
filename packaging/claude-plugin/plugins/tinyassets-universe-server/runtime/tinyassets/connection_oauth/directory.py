"""Daemon-owned OAuth client registrations. Providers are configuration, not code.

Only opaque provider ids enter offers and token bundles. Secret names are read
from this trusted directory again at exchange/refresh, never from those bundles.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

from tinyassets.connection_oauth.transport import OAuthError, validate_https_url

CONFIG_ENV = "TINYASSETS_OAUTH_DIRECTORY"
_SECRET_NAME = re.compile(r"TINYASSETS_OAUTH_[A-Z0-9_]+_SECRET\Z")
_PROVIDER_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_RESERVED = frozenset({
    "client_id", "client_secret", "redirect_uri", "response_type", "state", "scope",
    "code_challenge", "code_challenge_method", "request", "request_uri",
    "code", "code_verifier", "refresh_token", "grant_type",
})
# Values removed from the daemon environment before spawning children. Never
# serialized, returned in an offer, or inherited by a spawned interpreter.
_secrets: dict[str, str] = {}
_secret_lock = threading.Lock()
_pid = os.getpid()


def entries() -> list[dict[str, Any]]:
    """Load trusted JSON (a file override replaces the packaged directory)."""
    from tinyassets.connection_oauth.discovery import validate_scopes

    try:
        path = Path(os.environ.get(CONFIG_ENV) or Path(__file__).with_name("providers.json"))
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows = doc["providers"]
        if not isinstance(rows, list):
            raise ValueError
        seen: set[str] = set()
        for row in rows:
            ident = row["id"]
            if not isinstance(ident, str) or not _PROVIDER_ID.fullmatch(ident) or ident in seen:
                raise ValueError
            seen.add(ident)
            hosts = row["hosts"]
            if (not isinstance(hosts, list) or not hosts
                    or any(not isinstance(h, str) or not re.fullmatch(
                        r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", h) for h in hosts)):
                raise ValueError
            for key in ("authorization_endpoint", "token_endpoint",
                        "revocation_endpoint", "issuer"):
                if key in row or key in ("authorization_endpoint", "token_endpoint"):
                    validate_https_url(row[key])
            if not _SECRET_NAME.fullmatch(row["client_secret_env"]):
                raise ValueError
            if not re.fullmatch(r"TINYASSETS_OAUTH_[A-Z0-9_]+", row["client_id_env"]):
                raise ValueError
            if _SECRET_NAME.fullmatch(row["client_id_env"]):
                raise ValueError
            if row["token_endpoint_auth_method"] not in (
                "client_secret_post", "client_secret_basic",
            ):
                raise ValueError
            scopes = row.get("default_scopes", {})
            if not isinstance(scopes, dict):
                raise ValueError
            for use, values in scopes.items():
                if not isinstance(use, str) or not use:
                    raise ValueError
                validate_scopes(values)
            uses = row.get("host_uses", {})
            if not isinstance(uses, dict) or any(
                h not in hosts or u not in scopes for h, u in uses.items()
            ):
                raise ValueError
            extra = row.get("extra_auth_params", {})
            if (not isinstance(extra, dict) or set(extra) & _RESERVED
                    or any(not isinstance(v, str) or len(v) > 1024 for v in extra.values())):
                raise ValueError
        return rows
    except (OSError, ValueError, KeyError, TypeError, OAuthError):
        # Configuration/parse exception text can contain configuration contents.
        raise OAuthError("oauth_directory_invalid") from None


def secret(name: str) -> str:
    if os.getpid() != _pid:
        raise OAuthError("platform_client_unavailable")
    with _secret_lock:
        return os.environ.get(name) or _secrets.get(name, "")


def prepare_children() -> None:
    """Keep even multiprocessing spawn from inheriting platform client secrets."""
    entries()  # validate before starting a child
    with _secret_lock:
        for name in list(os.environ):
            if _SECRET_NAME.fullmatch(name):
                _secrets[name] = os.environ.pop(name)


def registered(provider_id: str, *, client_id: str, token_url: str) -> dict[str, Any]:
    """Re-pin both client and destination before resolving any secret."""
    for row in entries():
        if row["id"] == provider_id:
            if (os.environ.get(row["client_id_env"]) != client_id
                    or row["token_endpoint"] != token_url or not secret(row["client_secret_env"])):
                break
            return row
    raise OAuthError("platform_client_unavailable")


def resolve(requested: dict[str, Any], hosts: list[str]) -> dict[str, Any] | None:
    from tinyassets.connection_oauth.discovery import validate_scopes

    wanted = {h.lower() for h in hosts}
    for row in entries():
        # A token must not be granted to a connection that also names an
        # unrelated host. No suffix/wildcard matching.
        if not wanted or not wanted <= set(row["hosts"]):
            continue
        client_id = os.environ.get(row["client_id_env"], "")
        if not client_id or not secret(row["client_secret_env"]):
            continue
        scopes = requested.get("scopes") or []
        if not scopes:
            use = requested.get("use")
            uses = [use] if use else sorted(
                {row.get("host_uses", {}).get(h, "") for h in wanted} - {""})
            if use and use not in row.get("default_scopes", {}):
                continue
            scopes = [s for u in uses for s in row.get("default_scopes", {}).get(u, [])]
            # Ambiguous shared API hosts require explicit scopes or a named use.
            if not scopes:
                continue
        return {
            "source": "directory", "provider_id": row["id"],
            "issuer": row.get("issuer", ""), "authorize_url": row["authorization_endpoint"],
            "token_url": row["token_endpoint"], "client_id": client_id,
            "registration_url": "", "iss_parameter_supported": False,
            "scopes": validate_scopes(scopes),
            "extra_auth_params": row.get("extra_auth_params", {}),
        }
    return None
