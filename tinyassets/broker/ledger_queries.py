"""Named D11 read operations; no SQL, path or callable crosses the broker socket.

Discovery facts are read in one SQLite transaction, including the priced-source
precedence check. The legacy local route exists only when broker mode is off.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DISCOVERY_FACTS = "DISCOVERY_FACTS"
HAS_PRICED_SOURCE = "HAS_PRICED_SOURCE"
GRANTED_RESOURCE = "GRANTED_RESOURCE"
QUERIES = frozenset({DISCOVERY_FACTS, HAS_PRICED_SOURCE, GRANTED_RESOURCE})


def validate_query(query, principal, command_center, grant_id, connection_id):
    if not isinstance(query, str) or query not in QUERIES:
        raise ValueError("unsupported ledger query")
    for value in (principal, command_center, grant_id):
        if not isinstance(value, str) or not value.strip() or len(value) > 512 or "\0" in value:
            raise ValueError("invalid ledger query scope")
    if not isinstance(connection_id, str) or len(connection_id) > 512 or "\0" in connection_id:
        raise ValueError("invalid connection identity")
    if query == HAS_PRICED_SOURCE and not connection_id:
        raise ValueError("missing connection identity")


def local_query(ledger, *, query: str, principal: str, command_center: str,
                grant_id: str, connection_id: str = "") -> dict[str, Any]:
    """Broker-local transaction, also used by the unsplit developer runtime."""
    from tinyassets.storage.outbound_connections import GrantResolutionError

    validate_query(query, principal, command_center, grant_id, connection_id)
    with ledger._connect() as conn:
        conn.execute("BEGIN")
        if query == HAS_PRICED_SOURCE:
            # Connect asks run before a first deposit/grant. An actually absent
            # pair has no priced source; an existing, revoked or foreign row
            # must still pass the full scoped authorization below.
            existing = conn.execute(
                "SELECT 1 FROM outbound_connections WHERE connection_id = ?",
                (connection_id,),
            ).fetchone()
            existing_grant = conn.execute(
                "SELECT 1 FROM outbound_connection_grants WHERE grant_id = ?",
                (grant_id,),
            ).fetchone()
            if existing is None and existing_grant is None:
                return {"priced": False}
        grant = conn.execute(
            "SELECT connection_id, granted_at FROM outbound_connection_grants "
            "WHERE grant_id = ? AND owner_user_id = ? AND universe_id = ? "
            "AND revoked_at IS NULL", (grant_id, principal, command_center),
        ).fetchone()
        if grant is None or (connection_id and grant["connection_id"] != connection_id):
            raise GrantResolutionError("outbound connection grant identity mismatch")
        row = conn.execute(
            "SELECT * FROM outbound_connections WHERE connection_id = ? "
            "AND owner_user_id = ? AND revoked_at IS NULL",
            (grant["connection_id"], principal),
        ).fetchone()
        if row is None:
            raise GrantResolutionError("outbound connection grant identity mismatch")
        # HTTP discovery needs live grant/resource facts even before a model
        # profile exists, or when that unrelated profile needs repair.
        fields = ("connection_id", "owner_user_id", "connection_class", "scopes_json",
                  "provider", "destination", "credential_ref", "revoked_at",
                  "connection_type", "auth_scheme", "allowed_endpoints_json", "access_mode",
                  "git_host", "incarnation")
        resource = {key: row[key] for key in fields}
        if query == GRANTED_RESOURCE:
            return {"resource": resource}
        priced = conn.execute(
            "SELECT descriptor_json FROM connection_capabilities "
            "WHERE connection_id = ? AND capability_kind = 'model_discovery'",
            (grant["connection_id"],),
        ).fetchone()
        if query == HAS_PRICED_SOURCE:
            # Presence, even of a malformed descriptor, blocks the declared
            # free/flat override. Never turn unreadable pricing into free use.
            return {"priced": priced is not None}
        kind, profile = "model_discovery", priced
        if profile is None:
            kind = "model_use"
            profile = conn.execute(
                "SELECT descriptor_json FROM connection_capabilities "
                "WHERE connection_id = ? AND capability_kind = 'model_use'",
                (grant["connection_id"],),
            ).fetchone()
        # Explicit projection: future ledger columns are never automatically
        # published. credential_ref is an opaque custody reference, not bytes.
        return {"resource": resource,
                "granted_at": grant["granted_at"],
                "profile_kind": kind if profile is not None else None,
                "profile": json.loads(profile[0]) if profile is not None else None}


def query_ledger(data_root: Path, *, query: str, principal: str, command_center: str,
                 grant_id: str, connection_id: str = "") -> dict[str, Any]:
    """Route before constructing a ledger; selected-but-unavailable is fatal."""
    from tinyassets.broker.supervisor import broker_selected, get_supervisor

    validate_query(query, principal, command_center, grant_id, connection_id)
    arguments = dict(query=query, grant_id=grant_id, connection_id=connection_id)
    if broker_selected():
        from tinyassets.broker.client import BrokerClient
        from tinyassets.storage.outbound_connections import ProxyRequestError

        supervisor = get_supervisor(data_root)
        if supervisor is None:
            raise ProxyRequestError("credential broker is selected but not running")
        client = BrokerClient(supervisor.socket_path, principal=principal,
                              command_center=command_center, fence=supervisor.fence,
                              verify_peer=supervisor.verify_broker, timeout=30)
        return client.ledger_query(**arguments)
    from tinyassets.storage.outbound_connections import ConnectionLedger

    ledger = ConnectionLedger(Path(data_root) / "outbound.db", data_root=data_root)
    return local_query(ledger, principal=principal, command_center=command_center, **arguments)
