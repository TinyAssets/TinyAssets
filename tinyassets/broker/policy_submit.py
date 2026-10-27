"""N10 owner policy from the router: rules, spend, egress, one-use approvals and
outside-client grants (P0-3).

The router submits these on its owner-role broker channel. The box channel
cannot widen policy, and the session proof travels only on this channel.
"""
from __future__ import annotations

from tinyassets.broker.policy import PolicyRefused

_ACTIONS = frozenset({"read", "receipts", "write", "issue_once", "apply_proposal",
                      "outside_admit", "outside_set", "outside_list", "held"})


def submit(data_root, owner: str, action: str, **fields) -> dict:
    """Router-side call; a refusal raises PolicyRefused with the broker's reason."""
    from tinyassets.broker.owner_identities import _owner_request

    if action not in _ACTIONS:
        raise PolicyRefused("unsupported owner policy operation")
    answer = _owner_request(data_root, {"op": "OWNER_POLICY", "action": action,
                                        "principal": owner, **fields})
    if answer.get("op") == "OWNER_POLICY_REFUSED":
        reason = answer.get("reason")
        raise PolicyRefused(reason if isinstance(reason, str) and reason
                            else "owner policy refused")
    if answer.get("op") != "OWNER_POLICY_RESULT" or not isinstance(answer.get("result"), dict):
        raise RuntimeError("owner policy unavailable")
    return answer["result"]
