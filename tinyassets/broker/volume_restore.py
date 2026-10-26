"""N10 protected volume restore.

The router submits this on its owner-role broker channel. The box channel
cannot call it, and the session proof is never an argument of a host operation.
"""
from __future__ import annotations

from tinyassets.broker.owner_identities import validate_principal
from tinyassets.broker.policy import PolicyRefused

_ACTIONS = {
    "catalog": {"op", "action", "principal", "session_proof"},
    "checkpoint": {"op", "action", "principal", "session_proof"},
    "restore": {"op", "action", "principal", "session_proof", "snapshot_id",
                "expected_generation"},
    "proposal": {"op", "action", "principal", "session_proof", "snapshot_id"},
}


def submit(data_root, owner: str, action: str, session_proof: dict, **fields):
    """Router-side call. The proof travels only on this broker channel."""
    from tinyassets.broker.owner_identities import _owner_request

    if action not in _ACTIONS:
        raise PermissionError("invalid volume request")
    answer = _owner_request(data_root, {
        "op": "OWNER_VOLUME", "action": action, "principal": owner,
        "session_proof": session_proof, **fields})
    if answer.get("op") == "OWNER_VOLUME_REFUSED":
        reason = answer.get("reason")
        raise PermissionError(reason if isinstance(reason, str) and reason
                              else "volume restore refused")
    if answer.get("op") != "OWNER_VOLUME_RESULT" or not isinstance(answer.get("result"), dict):
        raise RuntimeError("volume restore unavailable")
    return answer["result"]


async def operation(server, doc: dict):
    """Re-check the live owner session, then the existing lifecycle."""
    from tinyassets.broker.admission import require_admitted

    if not isinstance(doc, dict) or doc.get("op") != "OWNER_VOLUME":
        raise PermissionError("invalid volume request")
    action = doc.get("action")
    owner = doc.get("principal")
    validate_principal(owner)
    presented = {key for key in doc if key not in {"generation", "token"}}
    if action not in _ACTIONS or presented != _ACTIONS[action]:
        raise PermissionError("invalid volume request")
    proof = doc.get("session_proof")
    if not isinstance(proof, dict):
        raise PolicyRefused("interactive owner session required")
    store = server._policy_store
    if store is None:
        raise PolicyRefused("interactive owner session unavailable")
    session_id = store._session(owner, proof)
    require_admitted(server._owner_identities, owner)
    routing = server.routing
    if action == "proposal":
        # An agent proposal is a Needs-you receipt, never an authorized restore.
        return store.receipt_proposal(owner, owner, {"snapshot_id": doc["snapshot_id"]})
    if action == "catalog":
        record = routing.volumes.volume(owner)
        rows = routing.volumes.volume(owner, "snapshots")
        return {"volume_generation": record["volume_generation"],
                "snapshots": [{"snapshot_id": row["snapshot_id"],
                               "published_at": row["published_at"]} for row in rows]}
    if action == "checkpoint":
        store.receipt_volume(owner, session_id, snapshot_id="checkpoint", generation=0,
                             outcome="accepted")
        record = await routing.checkpoint(owner)
        pending = bool(record["work"])
        return {"pending": pending,
                "snapshot_id": None if pending else record["snapshot_id"],
                "volume_generation": record["volume_generation"]}
    snapshot_id = doc["snapshot_id"]
    expected = doc["expected_generation"]
    if not isinstance(snapshot_id, str) or type(expected) is not int:
        raise PermissionError("snapshot and volume generation are required")
    store.receipt_volume(owner, session_id, snapshot_id=snapshot_id, generation=expected,
                         outcome="accepted")
    record = await routing.restore(owner, snapshot_id, expected_generation=expected)
    return {"snapshot_id": record["snapshot_id"],
            "volume_generation": record["volume_generation"],
            "rewind": record.get("rewind_generation") == record["volume_generation"]}
