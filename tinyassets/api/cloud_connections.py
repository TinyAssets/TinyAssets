"""The universe's connection inventory: ``read_graph target=connections``.

Every connection a universe holds is one its owner deposited through the
ordinary ``connect`` request -- a key for any host, GitHub included. This module
only LISTS them, channel-agnostically, in a redacted projection. There is no
per-service setup path here: a GitHub-only WorkOS pipe used to live in this
file, and was removed on 2026-09-24 because nothing could ever resolve its
credential and it was the one place GitHub was special.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tinyassets.api.helpers import _base_path, _request_universe, _universe_dir
from tinyassets.storage.outbound_connections import ConnectionLedger
from tinyassets.storage.workspace_authority import (
    WORKSPACE_SINK,
    connection_access_mode,
    connection_git_host,
    connection_git_scopes,
    parse_workspace_consent_destination,
)

#: The write_graph operations that DO create or change a connection. Named in
#: the refusal so an agent that reaches for a per-service setup finds the
#: general one instead.
_GENERIC_CONNECTION_OPERATIONS = (
    "request_from_user", "answer_request", "configure", "remove_http",
)


def _actor() -> str | None:
    from tinyassets.api import permissions

    if not permissions.is_authenticated_request():
        return None
    from tinyassets.principals import named_principal

    return named_principal(permissions.current_actor_id()) or None


def _project(resource: Any, grant: Any) -> dict[str, Any]:
    return {
        "connection_id": resource.connection_id,
        "grant_id": grant.grant_id,
        "provider": resource.provider,
        "destination": resource.destination,
        "connection_class": resource.connection_class,
        "scopes": list(resource.scopes),
        # Redacted egress allow-list: host, path_template, methods, and the
        # query/param descriptors (allowed_query, param_patterns, query_patterns,
        # required_query) — i.e. the owner's OWN declared egress policy, never the
        # credential (ConnectionView carries no credential_ref). An http channel
        # connection exposes these so the agent can read back the exact host + path
        # it must emit in an authenticated_external_call packet; a legacy row
        # with no declared endpoints shows an empty list.
        "allowed_endpoints": [
            ep.as_dict() for ep in (getattr(resource, "allowed_endpoints", ()) or ())
        ],
        "action_cap": (
            grant.unprompted_action_cap.as_dict()
            if grant.unprompted_action_cap is not None
            else None
        ),
        # The git scopes, split out of the flat ``scopes`` list above so the
        # agent can SEE which repositories this connection may clone or push
        # without having to know the scope grammar. A universe that can read
        # what it holds stops asking for what it already has.
        # Where git goes when the owner declared it ("" = the endpoint host).
        "git_host": getattr(resource, "git_host", "") or "",
        "git_scopes": [
            {"kind": kind, "repo": repo, "host": connection_git_host(resource)}
            for kind, repo in sorted(connection_git_scopes(resource))
        ],
        # "exact" | "full" (full-channel-access D3.4). On a full channel the
        # rows above are what the owner declared as its hosts, NOT the limit of
        # what the universe may do there: any path, any verb, any repository the
        # key reaches. Rendered as the mode rather than as wildcard rows,
        # because no wildcard is stored and inventing one on the way out would
        # be a second definition of the grant.
        "access": connection_access_mode(resource),
        "status": "connected",
    }


def _workspace_consents(uid: str) -> list[dict[str, Any]]:
    """The universe's active workspace consents, in the shape the agent asked in.

    A malformed or foreign destination row is skipped rather than rendered: this
    is a read the agent uses to decide whether to ask, and a row it cannot act on
    is noise.
    """
    from tinyassets.storage.effector_consents import list_consents

    found: list[dict[str, Any]] = []
    for row in list_consents(_universe_dir(uid), sink=WORKSPACE_SINK):
        parsed = parse_workspace_consent_destination(row.get("destination"))
        if parsed is None:
            continue
        found.append({**parsed, "granted_at": row.get("granted_at")})
    return sorted(
        found,
        key=lambda item: (item["repo"], item["connection_id"], item["operation"]),
    )


def _ledger(actor: str) -> ConnectionLedger:
    return ConnectionLedger(
        Path(_base_path()) / "outbound.db",
        verify_authenticated_principal=lambda: actor,
    )


def cloud_connections(
    *,
    action: str,
    universe_id: str = "",
    payload: Any = None,
) -> dict[str, Any]:
    actor = _actor()
    if actor is None:
        return {"error": "authentication_required", "resource": "connection"}
    uid = _request_universe(universe_id)
    from tinyassets.api import permissions

    if not permissions.universe_access_allows(uid, write=action != "list"):
        return {"error": "not_found", "resource": "connection"}
    normalized = (action or "").strip().lower()
    if normalized == "list":
        # List EVERY connection granted to this universe, channel-agnostically:
        # any HTTPS host the owner deposited a key for, plus any legacy row a
        # retired setup path left behind. No per-service code — the
        # owner-chosen `destination` label IS the channel identity, and each row
        # carries the connection_id + grant_id + allowed_endpoints the agent needs to
        # build an authenticated_external_call node WITHOUT the owner pasting them
        # back by hand. Redacted views only (no credential_ref), scoped to the
        # owner's grants for THIS universe.
        ledger = _ledger(actor)
        rows = []
        from tinyassets.api.connection_uses import connection_uses_view
        from tinyassets.extension_state import remote_mcp_by_connection

        # Remote MCP servers ride a connection through an active extension, so
        # the connection that carries one says so; otherwise a server connected
        # in one turn is invisible where the owner and agent look in the next.
        mcp = remote_mcp_by_connection(Path(_base_path()), owner=actor, universe=uid)
        for grant in ledger.list_grants(owner_user_id=actor, universe_id=uid):
            resource = ledger.get_connection(grant.connection_id)
            if resource is not None:
                # What the connection is used for (call / model) and its
                # constant headers: the same connector reads the same way
                # whether it reaches a platform or a model.
                rows.append({**_project(resource, grant),
                             **connection_uses_view(ledger, resource.connection_id),
                             "mcp_servers": mcp.get(resource.connection_id, [])})
        return {
            "universe_id": uid,
            "connections": rows,
            "count": len(rows),
            # Consents are per UNIVERSE, not per connection, so they ride
            # alongside the rows rather than inside them. Both halves of the
            # workspace authority are visible here: the scope on the connection
            # (the credential may reach the repository) and the consent here
            # (the owner agreed to this kind of work on it).
            "workspace_consents": _workspace_consents(uid),
        }
    return {
        "error": "unknown_connection_action",
        "allowed_actions": ["list"],
        "detail": (
            "Connecting any service is the ordinary connect request: "
            "write_graph target=connection operation=request_from_user with "
            "action.type=\"connect\" naming its host and endpoints, which the "
            "owner answers in the app. There is no per-service setup "
            "operation."
        ),
        "connection_operations": list(_GENERIC_CONNECTION_OPERATIONS),
    }


__all__ = ["cloud_connections"]
