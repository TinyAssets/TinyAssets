"""The owner's outside clients as a command center shows them: the broker's grants (P0-3).

One row per verified outside client the broker holds a grant for, never agent
authority or liveness. A revoked client, or a scoped one with no scope in this
command center, is not shown; an API key shows only once used and while live.
"""
from __future__ import annotations

import re


def display_name(value) -> str:
    if not isinstance(value, str) or len(value) > 80:
        return ""
    # Names are untrusted metadata, not an avenue to retain credentials/URLs.
    if re.search(r"(?i)(ta_key_|bearer|eyJ|sk-|sk_|https?[:/]|token[=:])", value):
        return ""
    return re.sub(r"[^\w .-]", "", value).strip()[:80]


def _entry(ident, name, protocol, timestamp, source, identity_source):
    return {"connection_id": ident, "name": name, "protocol": protocol,
            "name_source": source, "identity_source": identity_source,
            "last_authenticated_at": timestamp, "state": "previously_authenticated",
            "messaging_ready": False}


def roster(base, *, universe_id: str, owner: str) -> list[dict]:
    """A projection for an already-authorized owner/center read."""
    from tinyassets.api_keys import _client, current_store
    from tinyassets.broker.client import owner_policy_operation

    clients = owner_policy_operation(base, owner, "outside_list")["clients"]
    keys = {_client(key["key_id"]): key for key in current_store(base).list(owner)["keys"]}
    result = []
    for row in clients:
        if row["mode"] == "revoked" or row["mode"] == "scoped" and all(
                scope["universe"] != universe_id for scope in row["scopes"]):
            continue
        if row["source"] == "api_key":
            key = keys.get(row["client"])
            if key is None or key["last_used_at"] is None or key["revoked_at"] is not None:
                continue
            result.append(_entry("api:" + key["key_id"], display_name(key["name"]) or
                                 "Unnamed API client", "api", key["last_used_at"],
                                 "owner_supplied", "api_key"))
        else:
            result.append(_entry("mcp:" + row["client"], display_name(row["name"]) or
                                 "MCP client", "mcp", row["last_seen"], "unknown",
                                 "verified_client"))
    return result
