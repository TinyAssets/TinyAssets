"""Closed accounting operations on the authenticated daemon channel.

Liveness locks stay in the daemon. Send/claim/retry are deliberately absent:
they run inside the broker's already-authorized outbound dispatch.
"""
from __future__ import annotations

import math

_FIELDS = {
    "create": {"policy", "failures", "attempts", "closed", "owner_token", "parent_token"},
    "reserve": {"source_ref", "model", "free", "purpose"},
    "check_available": {"source_ref", "free", "purpose"},
    "dispatched": {"ordinal"}, "settle": {"ordinal", "outcome"},
    "settle_invocation": {"ordinal", "outcome"}, "close": set(), "receipt": set(),
    "link": {"kind", "subject_id"}, "for_subject": {"kind", "subject_id"},
    "issue_reference": {"ordinal", "grant_id", "connection_id", "verb", "request", "operation_id"},
}
_POLICY = {"max_requests", "free_limit", "free_pool_limit", "failure_limit", "source_limits",
           "deadline"}


def validate(document):
    if not isinstance(document, dict) or not isinstance(document.get("action"), str):
        raise ValueError("invalid accounting operation")
    action = document["action"]
    if action in {"daily_page", "linked_turns"}:
        from tinyassets.broker.usage_evidence import validate as validate_evidence

        validate_evidence(document)
        return
    if action not in _FIELDS or set(document) != _FIELDS[action] | {"action"}:
        raise ValueError("invalid accounting fields")
    for name in ("source_ref", "model", "grant_id", "connection_id", "operation_id",
                 "subject_id", "owner_token", "parent_token"):
        if name in document and (not isinstance(document[name], str)
                                 or not document[name] or len(document[name]) > 512):
            raise ValueError("invalid accounting identity")
    if "ordinal" in document and (type(document["ordinal"]) is not int or document["ordinal"] < 1):
        raise ValueError("invalid accounting ordinal")
    if "free" in document and type(document["free"]) is not bool:
        raise ValueError("invalid accounting classification")
    if "purpose" in document and document["purpose"] not in {
            "reply", "tool_review", "review", "helper", "learning"}:
        raise ValueError("invalid accounting purpose")
    if "outcome" in document and document["outcome"] not in {
            "not_sent", "succeeded", "failed", "unknown"}:
        raise ValueError("invalid accounting outcome")
    if action in {"link", "for_subject"} and document["kind"] not in {"turn", "run"}:
        raise ValueError("invalid accounting link")
    if action == "create":
        policy = document["policy"]
        if not isinstance(policy, dict) or set(policy) != _POLICY:
            raise ValueError("invalid accounting policy")
        deadline = policy["deadline"]
        if deadline is not None and (type(deadline) not in {int, float}
                                     or not math.isfinite(deadline)):
            raise ValueError("invalid accounting deadline")
        if (type(document["closed"]) is not bool or not isinstance(document["attempts"], list)
                or not isinstance(document["failures"], dict)):
            raise ValueError("invalid accounting initial state")


def mutation_document(operation, args, kwargs):
    if operation == "reserve":
        # Scope comes from the durable parent, never redundant mutation fields.
        return {"action": operation, **{k: v for k, v in kwargs.items()
                                        if k not in {"owner", "universe"}}}
    if operation == "check_available":
        return {"action": operation, **kwargs}
    if operation == "dispatched" and len(args) == 1 and not kwargs:
        return {"action": operation, "ordinal": args[0]}
    if operation == "settle" and len(args) == 2 and not kwargs:
        return {"action": operation, "ordinal": args[0], "outcome": args[1]}
    if operation == "close" and not args and not kwargs:
        return {"action": operation}
    raise ValueError("unsupported accounting mutation")


def local_operation(ledger, *, principal, command_center, usage_id, document):
    from tinyassets.process_liveness import ALIVE, owner_state
    from tinyassets.request_budget import RequestAttempt, TurnRequestBudget
    from tinyassets.storage.agent_request_usage import UsageStore

    validate(document)
    action = document["action"]
    store = UsageStore(ledger._data_root, broker_ledger=ledger)
    if action in {"daily_page", "linked_turns"}:
        from tinyassets.broker.usage_evidence import local_operation as evidence

        if usage_id != "":
            raise ValueError("daily evidence does not select a parent")
        return evidence(store, principal, document)
    if action != "for_subject" and (not isinstance(usage_id, str) or len(usage_id) != 32
                                    or any(c not in "0123456789abcdef" for c in usage_id)):
        raise ValueError("invalid accounting root")
    scope = (principal, command_center, usage_id)
    fields = {k: v for k, v in document.items() if k != "action"}
    if action == "create":
        token, parent = fields["owner_token"], fields["parent_token"]
        if (parent != "request_" + usage_id or owner_state(store.base, token) != ALIVE
                or owner_state(store.base, parent) != ALIVE):
            raise ValueError("accounting owner is unavailable")
        budget = TurnRequestBudget(principal, command_center, **fields["policy"])
        budget._attempts = [RequestAttempt(**a) for a in fields["attempts"]]
        if any(a.ordinal != i for i, a in enumerate(budget._attempts, 1)):
            raise ValueError("invalid accounting attempt order")
        budget._failures = fields["failures"]
        budget._closed = fields["closed"]
        store._insert(scope, budget, token, parent)
        return {"value": None}
    if action == "receipt":
        return {"receipt": store.receipt(scope)}
    if action == "for_subject":
        return {"receipts": store.for_subject(principal, command_center, **fields)}
    if action == "issue_reference":
        return {"reference": store.issue_reference(scope, **fields).document()}
    # Explicit dispatch table, never getattr on an incoming operation name.
    operations = {
        "reserve": lambda: store.mutate(scope, "reserve", owner=principal,
                                         universe=command_center, **fields),
        "check_available": lambda: store.mutate(scope, "check_available", **fields),
        "dispatched": lambda: store.mutate(scope, "dispatched", **fields),
        "settle": lambda: store.mutate(scope, "settle", **fields),
        "close": lambda: store.mutate(scope, "close"),
        "link": lambda: store.link(scope, **fields),
        "settle_invocation": lambda: store.settle_invocation(scope, **fields),
    }
    return {"value": operations[action]()}


def operation(base, scope, document):
    from tinyassets.broker.client import BrokerClient
    from tinyassets.broker.supervisor import get_supervisor
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.request_budget import RequestBudgetExceeded

    validate(document)
    supervisor = get_supervisor(base)
    if supervisor is None:
        raise ProviderAuthorityHeldError("accounting broker is unavailable")
    client = BrokerClient(supervisor.socket_path, principal=scope[0], command_center=scope[1],
                          fence=supervisor.fence, verify_peer=supervisor.verify_broker, timeout=30)
    try:
        return client.usage(scope[2], document)
    except RequestBudgetExceeded:
        raise
    except (RuntimeError, OSError, ValueError) as exc:
        raise ProviderAuthorityHeldError("accounting broker operation refused") from exc
