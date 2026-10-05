"""Bounded, credential-free pages for an admitted owner's connection catalogs."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

PAGE_SIZE = 64


def validate(cursor, limit):
    if (not isinstance(cursor, str) or len(cursor) > 512 or "\0" in cursor
            or type(limit) is not int or not 1 <= limit <= PAGE_SIZE):
        raise ValueError("invalid connection catalog page")


def local_page(ledger, *, principal, command_center, cursor, limit):
    from tinyassets.broker.ledger_queries import GRANTED_RESOURCE, validate_query
    from tinyassets.storage.outbound_connections import (
        ActionCap,
        ConnectionGrant,
        _resource_from_row,
    )

    validate(cursor, limit)
    validate_query(GRANTED_RESOURCE, principal, command_center, "catalog", "")
    with ledger._connect() as conn:
        conn.execute("BEGIN")
        rows = conn.execute(
            "SELECT c.*, g.grant_id, g.universe_id, g.granted_at, g.unprompted_action_cap_json "
            "FROM outbound_connection_grants g "
            "JOIN outbound_connections c ON c.connection_id=g.connection_id "
            "WHERE g.owner_user_id=? AND c.owner_user_id=? AND g.universe_id=? "
            "AND g.revoked_at IS NULL AND c.revoked_at IS NULL AND g.grant_id>? "
            "ORDER BY g.grant_id LIMIT ?",
            (principal, principal, command_center, cursor, limit + 1),
        ).fetchall()
        items = []
        for row in rows[:limit]:
            cap = row["unprompted_action_cap_json"]
            grant = ConnectionGrant(row["grant_id"], row["connection_id"], principal,
                                     command_center, row["granted_at"], None,
                                     ActionCap(**json.loads(cap)) if cap else None)
            items.append({"grant": asdict(grant), "connection": _resource_from_row(row).to_view(
            ).as_dict(), "incarnation": row["incarnation"]})
        return {"items": items, "next_cursor": rows[limit - 1]["grant_id"]
                if len(rows) > limit else None}


def connections(data_root, *, principal, command_center, limit=None):
    """Yield (grant, redacted view, incarnation); no silent catalog truncation."""
    from tinyassets.broker.supervisor import broker_selected, get_supervisor
    from tinyassets.storage.outbound_connections import (
        ActionCap,
        ConnectionGrant,
        ConnectionLedger,
        ConnectionView,
        ProxyRequestError,
        _parse_allowed_endpoints,
    )

    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("invalid connection catalog limit")
    cursor, count = "", 0
    selected = broker_selected()
    if selected:
        from tinyassets.broker.client import BrokerClient

        supervisor = get_supervisor(data_root)
        if supervisor is None:
            raise ProxyRequestError("credential broker is selected but not running")
        client = BrokerClient(supervisor.socket_path, principal=principal,
                              command_center=command_center, fence=supervisor.fence,
                              verify_peer=supervisor.verify_broker, timeout=30)
    else:
        ledger = ConnectionLedger(Path(data_root) / "outbound.db")
    while limit is None or count < limit:
        page_size = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - count)
        answer = (client.connection_catalog(cursor=cursor, limit=page_size) if selected
                  else local_page(ledger, principal=principal, command_center=command_center,
                                  cursor=cursor, limit=page_size))
        try:
            if (not isinstance(answer, dict) or set(answer) != {"items", "next_cursor"}
                    or not isinstance(answer["items"], list)
                    or len(answer["items"]) > page_size):
                raise ValueError("invalid page")
            decoded = []
            previous = cursor
            for item in answer["items"]:
                if set(item) != {"grant", "connection", "incarnation"}:
                    raise ValueError("invalid item")
                grow, vrow = item["grant"], item["connection"]
                cap = grow["unprompted_action_cap"]
                grant = ConnectionGrant(**(grow | {
                    "unprompted_action_cap": ActionCap(**cap) if cap is not None else None}))
                view = ConnectionView(**(vrow | {"scopes": tuple(vrow["scopes"]),
                    "allowed_endpoints": _parse_allowed_endpoints(vrow["allowed_endpoints"])}))
                if not all((grant.owner_user_id == view.owner_user_id == principal,
                            grant.universe_id == command_center,
                            grant.connection_id == view.connection_id,
                            grant.revoked_at is None, view.revoked_at is None,
                            isinstance(grant.grant_id, str), grant.grant_id > previous,
                            isinstance(item["incarnation"], str))):
                    raise ValueError("invalid scope")
                previous = grant.grant_id
                decoded.append((grant, view, item["incarnation"]))
            next_cursor = answer["next_cursor"]
            if next_cursor is not None and (len(decoded) != page_size or next_cursor != previous):
                raise ValueError("invalid cursor")
        except (LookupError, TypeError, ValueError):
            raise ProxyRequestError("invalid broker connection catalog response") from None
        yield from decoded
        count += len(decoded)
        if next_cursor is None:
            return
        cursor = next_cursor
