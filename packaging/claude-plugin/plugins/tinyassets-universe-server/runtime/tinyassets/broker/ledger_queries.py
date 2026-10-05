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
AUTHORIZED_CONNECTION = "AUTHORIZED_CONNECTION"
CONNECTION_GRANTS = "CONNECTION_GRANTS"
BOOTSTRAP_RECOVERY = "BOOTSTRAP_RECOVERY"
OWNER_CONNECTION_VIEW = "OWNER_CONNECTION_VIEW"
OWNER_CONNECTION_NAMES = "OWNER_CONNECTION_NAMES"
QUERIES = frozenset({DISCOVERY_FACTS, HAS_PRICED_SOURCE, GRANTED_RESOURCE,
                     AUTHORIZED_CONNECTION, CONNECTION_GRANTS, BOOTSTRAP_RECOVERY,
                     OWNER_CONNECTION_VIEW, OWNER_CONNECTION_NAMES})


def validate_query(query, principal, command_center, grant_id, connection_id):
    if not isinstance(query, str) or query not in QUERIES:
        raise ValueError("unsupported ledger query")
    for value in (principal, command_center, grant_id):
        if not isinstance(value, str) or not value.strip() or len(value) > 512 or "\0" in value:
            raise ValueError("invalid ledger query scope")
    if not isinstance(connection_id, str) or len(connection_id) > 512 or "\0" in connection_id:
        raise ValueError("invalid connection identity")
    if query in {HAS_PRICED_SOURCE, AUTHORIZED_CONNECTION, CONNECTION_GRANTS,
                 OWNER_CONNECTION_VIEW} and not connection_id:
        raise ValueError("missing connection identity")


def local_query(ledger, *, query: str, principal: str, command_center: str,
                grant_id: str, connection_id: str = "") -> dict[str, Any]:
    """Broker-local transaction, also used by the unsplit developer runtime."""
    from tinyassets.storage.outbound_connections import GrantResolutionError

    validate_query(query, principal, command_center, grant_id, connection_id)
    with ledger._connect() as conn:
        conn.execute("BEGIN")
        if query == OWNER_CONNECTION_VIEW:
            from tinyassets.storage.outbound_connections import _resource_from_row

            row = conn.execute(
                "SELECT * FROM outbound_connections WHERE connection_id=? "
                "AND owner_user_id=? AND revoked_at IS NULL",
                (connection_id, principal),
            ).fetchone()
            return {"view": _resource_from_row(row).to_view().as_dict() if row else None}
        if query == OWNER_CONNECTION_NAMES:
            rows = conn.execute(
                "SELECT connection_id, destination FROM outbound_connections "
                "WHERE owner_user_id=? AND revoked_at IS NULL AND connection_id>? "
                "ORDER BY connection_id LIMIT 65", (principal, connection_id),
            ).fetchall()
            return {"items": [dict(row) for row in rows[:64]],
                    "next_cursor": rows[63]["connection_id"] if len(rows) > 64 else None}
        if query == BOOTSTRAP_RECOVERY:
            row = conn.execute(
                "SELECT c.destination, p.descriptor_json FROM outbound_connection_grants g "
                "JOIN outbound_connections c ON c.connection_id=g.connection_id "
                "LEFT JOIN connection_capabilities p ON p.connection_id=c.connection_id "
                "AND p.capability_kind='model_discovery' "
                "WHERE g.grant_id=? AND g.owner_user_id=? AND c.owner_user_id=? "
                "AND g.universe_id=? AND (?='' OR c.connection_id=?)",
                (grant_id, principal, principal, command_center, connection_id, connection_id),
            ).fetchone()
            return {"recovery": None if row is None else {
                "destination": row[0], "descriptor": json.loads(row[1]) if row[1] else None}}
        if query == CONNECTION_GRANTS:
            rows = conn.execute(
                "SELECT g.grant_id FROM outbound_connection_grants g "
                "JOIN outbound_connections c ON c.connection_id=g.connection_id "
                "WHERE g.owner_user_id=? AND c.owner_user_id=? AND g.universe_id=? "
                "AND g.connection_id=? AND g.revoked_at IS NULL AND c.revoked_at IS NULL "
                "ORDER BY g.grant_id LIMIT 1001",
                (principal, principal, command_center, connection_id),
            ).fetchall()
            return {"grant_ids": [row[0] for row in rows]}
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
            "SELECT connection_id, granted_at, unprompted_action_cap_json "
            "FROM outbound_connection_grants "
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
        if query == AUTHORIZED_CONNECTION:
            return {"resource": resource, "grant": {
                "grant_id": grant_id, "connection_id": grant["connection_id"],
                "owner_user_id": principal, "universe_id": command_center,
                "granted_at": grant["granted_at"], "revoked_at": None,
                "unprompted_action_cap": json.loads(grant["unprompted_action_cap_json"])
                if grant["unprompted_action_cap_json"] else None,
            }}
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


def granted_resource_row(data_root: Path, *, principal: str, command_center: str, grant_id: str):
    """Validated live resource projection, including its custody incarnation."""
    from tinyassets.storage.outbound_connections import ProxyRequestError, _resource_from_row

    facts = query_ledger(data_root, query=GRANTED_RESOURCE, principal=principal,
                         command_center=command_center, grant_id=grant_id)
    try:
        row = facts["resource"]
        if not isinstance(row, dict):
            raise ValueError("invalid resource projection")
        resource = _resource_from_row(row)
        if resource.owner_user_id != principal or resource.revoked_at is not None:
            raise ValueError("invalid resource authority")
        if not isinstance(row.get("incarnation"), str):
            raise ValueError("invalid resource incarnation")
        return row
    except (LookupError, TypeError, ValueError):
        raise ProxyRequestError("invalid credential broker resource projection") from None


def authorized_connection(data_root: Path, *, principal: str, command_center: str,
                          grant_id: str, connection_id: str):
    """Live grant, resource and incarnation from one exact scoped snapshot."""
    from tinyassets.storage.outbound_connections import (
        ActionCap,
        ConnectionGrant,
        ProxyRequestError,
        _resource_from_row,
    )

    facts = query_ledger(data_root, query=AUTHORIZED_CONNECTION, principal=principal,
                         command_center=command_center, grant_id=grant_id,
                         connection_id=connection_id)
    try:
        row, grant_row = facts["resource"], facts["grant"]
        if not isinstance(row, dict) or not isinstance(grant_row, dict):
            raise ValueError("invalid authority projection")
        resource = _resource_from_row(row)
        cap = grant_row["unprompted_action_cap"]
        grant = ConnectionGrant(**(grant_row | {
            "unprompted_action_cap": ActionCap(**cap) if cap is not None else None}))
        if not all((grant.grant_id == grant_id,
                    grant.connection_id == resource.connection_id == connection_id,
                    grant.owner_user_id == resource.owner_user_id == principal,
                    grant.universe_id == command_center,
                    grant.revoked_at is None, resource.revoked_at is None,
                    isinstance(row.get("incarnation"), str))):
            raise ValueError("invalid authority projection")
        return grant, resource, row["incarnation"]
    except (LookupError, TypeError, ValueError):
        raise ProxyRequestError("invalid credential broker authority projection") from None
