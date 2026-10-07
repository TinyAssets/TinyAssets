"""Scoped capability metadata operations on the broker-owned connection ledger."""
from __future__ import annotations

from pathlib import Path


def validate(document):
    from tinyassets.broker.ledger_queries import AUTHORIZED_CONNECTION, validate_query

    if set(document) != {"action", "grant_id", "connection_id", "capability_kind",
                         "descriptor", "enabled", "preview"}:
        raise ValueError("invalid capability operation fields")
    validate_query(AUTHORIZED_CONNECTION, "scope", "scope", document["grant_id"],
                   document["connection_id"])
    if (document["action"] not in {"read", "configure"}
            or document["capability_kind"] not in {
                "constant_headers", "model_use", "model_discovery", "realtime_voice"}
            or type(document["enabled"]) is not bool
            or type(document["preview"]) is not bool):
        raise ValueError("invalid capability operation")
    if document["action"] == "read" and (
            document["descriptor"] is not None or document["enabled"] or document["preview"]):
        raise ValueError("invalid capability read")


def local_operation(ledger, *, principal, command_center, document):
    from tinyassets.broker.ledger_queries import AUTHORIZED_CONNECTION, local_query
    from tinyassets.storage.outbound_connections import ActionCap, ConnectionGrant

    validate(document)
    facts = local_query(ledger, query=AUTHORIZED_CONNECTION, principal=principal,
                        command_center=command_center, grant_id=document["grant_id"],
                        connection_id=document["connection_id"])
    row = facts["grant"]
    cap = row["unprompted_action_cap"]
    grant = ConnectionGrant(**(row | {
        "unprompted_action_cap": ActionCap(**cap) if cap is not None else None}))
    if document["action"] == "read":
        result = ledger.get_connection_capability(
            document["connection_id"], document["capability_kind"], expected_grant=grant)
    else:
        result = ledger.configure_capability(
            connection_id=document["connection_id"],
            capability_kind=document["capability_kind"], descriptor=document["descriptor"],
            enabled=document["enabled"], preview=document["preview"], expected_grant=grant)
    return {"descriptor": result.descriptor() if result is not None else None}


def capability_operation(data_root, *, principal, command_center, grant_id, connection_id,
                         capability_kind, action="read", descriptor=None, enabled=False,
                         preview=False):
    from tinyassets.broker.supervisor import broker_selected, get_supervisor
    from tinyassets.storage.outbound_connections import (
        ConnectionLedger,
        ProxyRequestError,
        _validate_connection_capability,
    )

    document = dict(action=action, grant_id=grant_id, connection_id=connection_id,
                    capability_kind=capability_kind, descriptor=descriptor,
                    enabled=enabled, preview=preview)
    validate(document)
    if broker_selected():
        from tinyassets.broker.client import BrokerClient

        supervisor = get_supervisor(data_root)
        if supervisor is None:
            raise ProxyRequestError("credential broker is selected but not running")
        client = BrokerClient(supervisor.socket_path, principal=principal,
                              command_center=command_center, fence=supervisor.fence,
                              verify_peer=supervisor.verify_broker, timeout=30)
        answer = client.capability(document)
    else:
        answer = local_operation(ConnectionLedger(Path(data_root) / "outbound.db"),
                                 principal=principal, command_center=command_center,
                                 document=document)
    if not isinstance(answer, dict) or set(answer) != {"descriptor"}:
        raise ProxyRequestError("invalid capability response")
    if answer["descriptor"] is None:
        return None
    return _validate_connection_capability(connection_id, capability_kind, answer["descriptor"])
