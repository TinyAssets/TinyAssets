"""Owner-scoped MCP workflow events: the definition, and what the box decides.

Subscriptions, signing keys, delivery state and the callback connection live
on the trusted side (``broker/mcp_events.py``, ADR-025). The box answers only
what needs owner files: may this owner follow this workflow, and does a
completed run pass a subscription's filters.
"""

from __future__ import annotations

import json

from tinyassets.mcp_event_webhooks import EventError, canonical

NAME = "workflow.succeeded"
_IDENTIFIER = {"type": "string", "minLength": 1, "maxLength": 256}
_FILTER = {
    "type": "object",
    "maxProperties": 16,
    "additionalProperties": {"type": ["string", "number", "boolean", "null"]},
}
DEFINITION = {
    "name": NAME,
    "delivery": ["webhook"],
    "description": "An owned workflow completed successfully. Select an exact command center "
    "and workflow, and optional exact input/output fields (e.g. a successful inbox-save "
    "receipt). Replies are suppressed. Contains IDs only; read authorized run/inbox data "
    "with read_graph. Delivery acknowledges receipt, not an agent response.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "command_center_id": _IDENTIFIER,
            "branch_def_id": _IDENTIFIER,
            "input_equals": _FILTER,
            "output_equals": _FILTER,
        },
        "required": ["command_center_id", "branch_def_id"],
        "additionalProperties": False,
    },
    "payloadSchema": {
        "type": "object",
        "properties": {k: _IDENTIFIER for k in ("command_center_id", "branch_def_id", "run_id")},
        "required": ["command_center_id", "branch_def_id", "run_id"],
        "additionalProperties": False,
    },
}


def _scalar_matches(actual, expected):
    if not isinstance(actual, (str, int, float, bool, type(None))):
        return False
    try:
        return canonical(actual) == canonical(expected)
    except (ValueError, TypeError):
        return False


def arguments(value):
    if not isinstance(value, dict) or set(value) - {
        "command_center_id",
        "branch_def_id",
        "input_equals",
        "output_equals",
    }:
        raise EventError(-32602, "Invalid event arguments")
    for field in ("command_center_id", "branch_def_id"):
        if not isinstance(value.get(field), str) or not 1 <= len(value[field]) <= 256:
            raise EventError(-32602, "Exact command center and workflow IDs are required")
    for field in ("input_equals", "output_equals"):
        filters = value.get(field, {})
        if (
            not isinstance(filters, dict)
            or len(filters) > 16
            or any(
                not isinstance(k, str)
                or not 1 <= len(k) <= 128
                or not isinstance(v, (str, int, float, bool, type(None)))
                for k, v in filters.items()
            )
        ):
            raise EventError(-32602, "Filters must contain at most 16 scalar equality fields")
    try:
        if len(canonical(value).encode()) > 8192:
            raise ValueError
    except ValueError:
        raise EventError(-32602, "Event arguments exceed bounds") from None
    return value


def authorize(base, owner, args, authority=None):
    """Re-evaluate the persisted owner and exact resource grant; never use a fallback actor."""
    from tinyassets.auth.provider import Identity
    from tinyassets.branch_versions import branch_readable_by
    from tinyassets.daemon_server import (
        get_branch_definition,
        universe_access_permission,
        universe_is_private,
    )
    from tinyassets.principals import named_principal
    from tinyassets.universe_owner import owner_of

    if not named_principal(owner) or owner.startswith("universe:"):
        raise PermissionError("Named owner required")
    uid = args["command_center_id"]
    if owner_of(base, uid) != owner or (
        universe_is_private(base, universe_id=uid)
        and universe_access_permission(base, universe_id=uid, actor_id=owner) != "admin"
    ):
        raise PermissionError("Owned command center required")
    branch = get_branch_definition(base, branch_def_id=args["branch_def_id"])
    if not branch_readable_by(
        owner, author=branch.get("author"), visibility=branch.get("visibility")
    ):
        raise PermissionError("Workflow unavailable")
    if authority:
        from tinyassets.outside_authority import check_identity, refresh

        # The grant as it stands now, unchanged since the subscribe (P0-3).
        identity = Identity(owner, owner, metadata={"outside_origin": refresh(owner, authority)})
        check_identity(identity, universe=uid, agent="main", capability="read_graph")




def _box_owner():
    """The owner this box serves; ``None`` outside a box, where no broker channel exists."""
    from tinyassets.owner_runtime import current

    runtime = current()
    return runtime.principal if runtime is not None else None


def capture_terminal(base, row):
    """Report one completed run before the terminal outbox acknowledges it.

    Errors keep the outbox row owed. The broker names the live subscriptions for
    this run's scope; the box re-checks each against owner files, applies its
    input and output filters, and the broker queues only the ones named here.
    No run input or output leaves the box.
    """
    from tinyassets.owner_channel import request

    box_owner = _box_owner()
    if row["status"] != "completed" or box_owner is None:
        return
    universe = str(row["queue_universe_id"] or "")
    actor = str(row["actor"] or "")
    if actor.startswith("universe:"):
        universe = actor[len("universe:") :]
    owner = str(row["cause_principal"] or "")
    inputs = json.loads(row["inputs_json"])
    outputs = json.loads(row["output_json"])
    if (
        owner != box_owner
        or not universe
        or not isinstance(inputs, dict)
        or not isinstance(outputs, dict)
        or inputs.get("operation") == "reply"
        or isinstance(inputs.get("data"), dict)
        and inputs["data"].get("operation") == "reply"
    ):
        return
    event = {
        "command_center_id": universe,
        "branch_def_id": row["branch_def_id"],
        "run_id": row["run_id"],
        "event_id": f"{row['run_id']}#{row['seq']}",
        "occurred": row["created_at"],
    }
    if not all(
        isinstance(event[field], str) and 1 <= len(event[field]) <= 256
        for field in ("command_center_id", "branch_def_id", "run_id", "event_id")
    ):
        # No subscription can name it (arguments() needs exact IDs): an activity
        # session's run has no workflow. Nothing to report; the outbox may ack.
        return
    matched = request("", {"op": "MCP_EVENTS", "action": "match", "event": event})
    allow, revoke = [], []
    for subscription in matched:
        args = subscription["arguments"]
        try:
            authorize(base, owner, args, subscription["authority"])
        except (PermissionError, KeyError):
            revoke.append(subscription["id"])
            continue
        if all(
            k in actual and _scalar_matches(actual[k], v)
            for field, actual in (("input_equals", inputs), ("output_equals", outputs))
            for k, v in args.get(field, {}).items()
        ):
            allow.append(subscription["id"])
    if allow or revoke:
        request("", {"op": "MCP_EVENTS", "action": "capture", "event": event,
                     "allow": allow, "revoke": revoke})
