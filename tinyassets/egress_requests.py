"""In-box translation of broker refusals into the existing Needs you flow."""
from __future__ import annotations


def request_access(detail):
    from tinyassets.api.pending_requests import request_from_user
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.broker.client import owner_policy_operation
    from tinyassets.broker.egress import normalize_destination
    from tinyassets.owner_centers import Centers
    from tinyassets.owner_runtime import current

    runtime = current()
    if runtime is None or detail.get("decision") != "ask":
        return
    target = normalize_destination(detail["host"], detail["port"])
    snapshot = owner_policy_operation(runtime.root, runtime.principal, "read")
    policy = snapshot["policy"]
    if (snapshot["generation"] != detail["generation"]
            or policy["egress"]["mode"] != "ask"
            or target in policy["egress"]["destinations"]):
        return
    centers = Centers(runtime.root).active()
    if not centers:
        raise RuntimeError("internet approval has no owner request home")
    policy["egress"]["destinations"].append(target)
    address = f"{target['host']}:{target['port']}"
    with identity_context(Identity(user_id=runtime.principal, username=runtime.principal)):
        result = request_from_user(universe_id=centers[0], origin="platform", payload={
            "kind": "Policy", "title": f"Allow internet access to {address}",
            "body": f"Allow future connections to {address} until you remove this rule.",
            "action": {"type": "policy_proposal", "policy": policy,
                       "expected_generation": snapshot["generation"]},
        })
    if result.get("error"):
        raise RuntimeError("internet approval request unavailable: " + result["error"])
    return result
