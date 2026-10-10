"""Explicit, incarnation-bound HTTP policy updates on the private broker ledger."""
from __future__ import annotations

import json


def validate(document):
    from tinyassets.api.http_connection import _DESTINATION_RE

    if not isinstance(document, dict) or set(document) != {
            "action", "destination", "expected", "endpoints", "scopes", "git_host"}:
        raise ValueError("invalid HTTP policy fields")
    expected = document["expected"]
    if (document["action"] not in {"extend", "full"}
            or not isinstance(document["destination"], str)
            or not _DESTINATION_RE.fullmatch(document["destination"])
            or not isinstance(expected, dict)
            or set(expected) != {"endpoints_json", "scopes_json", "access_mode", "incarnation"}
            or any(not isinstance(v, str) or not v for v in expected.values())
            or not isinstance(document["git_host"], str)
            or not isinstance(document["endpoints"], list)
            or not isinstance(document["scopes"], list)
            or any(not isinstance(v, str) for v in document["scopes"])):
        raise ValueError("invalid HTTP policy")
    if document["action"] == "full" and (document["endpoints"] or document["scopes"]):
        raise ValueError("full access changes only the mode")


def local_operation(ledger, *, principal, command_center, document):
    from tinyassets.api.http_connection import _canonical_endpoint_set, _ids
    from tinyassets.broker.ledger_queries import AUTHORIZED_CONNECTION, validate_query
    from tinyassets.storage.outbound_connections import GrantResolutionError

    validate(document)
    connection_id, grant_id = _ids(universe_id=command_center, destination=document["destination"])
    validate_query(AUTHORIZED_CONNECTION, principal, command_center, grant_id, connection_id)
    expected = document["expected"]
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT c.* FROM outbound_connections c JOIN outbound_connection_grants g "
            "ON g.connection_id=c.connection_id WHERE c.connection_id=? AND g.grant_id=? "
            "AND c.owner_user_id=? AND g.owner_user_id=? AND g.universe_id=? "
            "AND c.revoked_at IS NULL AND g.revoked_at IS NULL",
            (connection_id, grant_id, principal, principal, command_center)).fetchone()
        if (row is None or row["connection_type"] != "http" or row["provider"] != "http"
                or row["connection_class"] != "http"
                or row["destination"] != document["destination"]
                or row["credential_ref"] != "vault://http/" + document["destination"]):
            raise GrantResolutionError("outbound connection identity mismatch")
        actual = {"endpoints_json": row["allowed_endpoints_json"],
                  "scopes_json": row["scopes_json"], "access_mode": row["access_mode"],
                  "incarnation": row["incarnation"]}
        if expected != actual or document["git_host"] != row["git_host"]:
            return {"updated": False}
        if document["action"] == "full":
            updated = ledger.set_access_mode(
                connection_id=connection_id, access_mode="full",
                expected_mode=expected["access_mode"],
                expected_endpoints_json=expected["endpoints_json"],
                expected_scopes_json=expected["scopes_json"],
                expected_incarnation=expected["incarnation"], _transaction=conn)
        else:
            # The API constructs a union. Enforce that again at the persistence
            # boundary so IPC cannot turn extension into destructive narrowing.
            if (not _canonical_endpoint_set(json.loads(expected["endpoints_json"]))
                    <= _canonical_endpoint_set(document["endpoints"])
                    or not set(json.loads(expected["scopes_json"])) <= set(document["scopes"])):
                raise ValueError("HTTP policy extension cannot narrow")
            updated = ledger.extend_http_connection_endpoints(
                connection_id=connection_id, endpoints=document["endpoints"],
                scopes=tuple(document["scopes"]), git_host=document["git_host"],
                expected_endpoints_json=expected["endpoints_json"],
                expected_scopes_json=expected["scopes_json"],
                expected_access_mode=expected["access_mode"],
                expected_incarnation=expected["incarnation"], expected_grant_id=grant_id,
                _transaction=conn)
        return {"updated": updated}


def read_policy(data_root, *, principal, command_center, destination):
    from tinyassets.api.http_connection import _ids
    from tinyassets.broker.ledger_queries import AUTHORIZED_CONNECTION, query_ledger
    from tinyassets.storage.outbound_connections import (
        ActionCap,
        ConnectionGrant,
        ProxyRequestError,
        _resource_from_row,
    )

    connection_id, grant_id = _ids(universe_id=command_center, destination=destination)
    answer = query_ledger(data_root, query=AUTHORIZED_CONNECTION, principal=principal,
                          command_center=command_center, connection_id=connection_id,
                          grant_id=grant_id)
    try:
        row, grow = answer["resource"], answer["grant"]
        resource = _resource_from_row(row)
        cap = grow["unprompted_action_cap"]
        grant = ConnectionGrant(**(grow | {
            "unprompted_action_cap": ActionCap(**cap) if cap is not None else None}))
        if not all((resource.connection_id == grant.connection_id == connection_id,
                    grant.grant_id == grant_id, grant.universe_id == command_center,
                    grant.owner_user_id == resource.owner_user_id == principal,
                    grant.revoked_at is None, resource.revoked_at is None)):
            raise ValueError("invalid policy scope")
        expected = {"endpoints_json": row["allowed_endpoints_json"],
                    "scopes_json": row["scopes_json"], "access_mode": row["access_mode"],
                    "incarnation": row["incarnation"]}
        if any(not isinstance(v, str) or not v for v in expected.values()):
            raise ValueError("invalid policy snapshot")
        return resource, grant, expected
    except (LookupError, TypeError, ValueError):
        raise ProxyRequestError("invalid broker policy response") from None


def update_policy(data_root, *, principal, command_center, destination, expected,
                  action, endpoints=(), scopes=(), git_host=""):
    from tinyassets.broker.client import BrokerClient
    from tinyassets.broker.supervisor import get_supervisor
    from tinyassets.storage.outbound_connections import ProxyRequestError

    document = dict(action=action, destination=destination, expected=expected,
                    endpoints=list(endpoints), scopes=list(scopes), git_host=git_host)
    validate(document)
    supervisor = get_supervisor(data_root)
    if supervisor is None:
        raise ProxyRequestError("the credential broker is not running")
    client = BrokerClient(supervisor.socket_path, principal=principal,
                          command_center=command_center, fence=supervisor.fence,
                          verify_peer=supervisor.verify_broker, timeout=30)
    result = client.http_policy(document)
    if set(result) != {"updated"} or type(result["updated"]) is not bool:
        raise ProxyRequestError("invalid broker policy response")
    return result["updated"]
