"""Named HTTP disconnect steps; custody and dependent authority stay in the daemon."""
from __future__ import annotations

import time


def validate(document):
    if not isinstance(document, dict) or set(document) != {"action", "destination", "incarnation"}:
        raise ValueError("invalid disconnect fields")
    from tinyassets.api.http_connection import _DESTINATION_RE

    if (document["action"] not in {"inspect", "fence", "erase"}
            or not isinstance(document["destination"], str)
            or not _DESTINATION_RE.fullmatch(document["destination"])
            or not isinstance(document["incarnation"], str)
            or len(document["incarnation"]) > 512):
        raise ValueError("invalid disconnect operation")


def local_operation(ledger, *, principal, command_center, document):
    from tinyassets.api.http_connection import _ids
    from tinyassets.broker.ledger_queries import AUTHORIZED_CONNECTION, validate_query
    from tinyassets.storage.outbound_connections import GrantResolutionError

    validate(document)
    connection_id, grant_id = _ids(universe_id=command_center, destination=document["destination"])
    validate_query(AUTHORIZED_CONNECTION, principal, command_center, grant_id, connection_id)
    with ledger._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM outbound_connections WHERE connection_id=?",
                           (connection_id,)).fetchone()
        if row is None:
            return {"resource": None, "incarnation": "", "removed": False}
        # Deterministic center/destination identity also permits recovery of a
        # revoked connection or interrupted deposit with no surviving grant.
        if (row["owner_user_id"] != principal or row["connection_type"] != "http"
                or row["connection_class"] != "http" or row["provider"] != "http"
                or row["destination"] != document["destination"]
                or row["credential_ref"] != "vault://http/" + document["destination"]):
            raise GrantResolutionError("outbound connection identity mismatch")
        incarnation = row["incarnation"]
        fields = ("connection_id", "owner_user_id", "connection_class", "scopes_json",
                  "provider", "destination", "credential_ref", "revoked_at",
                  "connection_type", "auth_scheme", "allowed_endpoints_json", "access_mode",
                  "git_host")
        result = {"resource": {key: row[key] for key in fields},
                  "incarnation": incarnation, "removed": False}
        if document["action"] == "inspect":
            return result
        if not document["incarnation"] or document["incarnation"] != incarnation:
            raise GrantResolutionError("outbound connection incarnation changed")
        if document["action"] == "fence":
            conn.execute("UPDATE outbound_connections SET revoked_at=COALESCE(revoked_at, ?) "
                         "WHERE connection_id=?", (time.time(), connection_id))
        else:
            if row["revoked_at"] is None:
                raise GrantResolutionError("outbound connection must be fenced before erase")
            for table in ("connection_capabilities", "outbound_connection_grants",
                          "outbound_connections"):
                conn.execute(f"DELETE FROM {table} WHERE connection_id=?", (connection_id,))
            result["removed"] = True
        return result


def disconnect(data_root, *, principal, command_center, destination, action="inspect",
               incarnation=""):
    from tinyassets.broker.client import BrokerClient
    from tinyassets.broker.supervisor import get_supervisor
    from tinyassets.storage.outbound_connections import ProxyRequestError

    document = dict(action=action, destination=destination, incarnation=incarnation)
    validate(document)
    supervisor = get_supervisor(data_root)
    if supervisor is None:
        raise ProxyRequestError("credential broker is selected but not running")
    client = BrokerClient(supervisor.socket_path, principal=principal,
                          command_center=command_center, fence=supervisor.fence,
                          verify_peer=supervisor.verify_broker, timeout=30)
    result = client.disconnect(document)
    if (set(result) != {"resource", "incarnation", "removed"}
            or not isinstance(result["incarnation"], str)
            or type(result["removed"]) is not bool
            or (result["resource"] is not None and not isinstance(result["resource"], dict))):
        raise ProxyRequestError("invalid broker disconnect response")
    return result
