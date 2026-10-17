"""Router billing client for the broker's private plan database."""
from __future__ import annotations

from tinyassets.broker.owner_reads import PlanMissing


class BillingPlans:
    def __init__(self, root):
        from tinyassets.owner_runtime import refuse_router_store

        refuse_router_store("plans")
        self.root = root

    def _call(self, action, **args):
        from tinyassets.broker.owner_identities import _owner_request

        answer = _owner_request(self.root, {"op": "BILLING_PLAN", "action": action, "args": args})
        if answer == {"op": "PLAN_MISSING"}:
            raise PlanMissing("account billing row missing")
        if set(answer) != {"op", "result"} or answer["op"] != "BILLING_RESULT":
            raise RuntimeError("billing operation refused")
        return answer["result"]

    def read(self, owner):
        return self._call("read", owner=owner)

    def owner_for(self, **args):
        return self._call("owner_for", **args)

    def owner_for_checkout(self, **args):
        return self._call("owner_for_checkout", **args)

    def bind_subscription(self, owner, **args):
        return self._call("bind_subscription", owner=owner, **args)

    def apply_tier_event(self, owner, **args):
        return self._call("apply_tier_event", owner=owner, **args)

    def current_checkout_attempt(self, owner, **args):
        return self._call("current_checkout_attempt", owner=owner, **args)

    def begin_checkout_attempt(self, owner, **args):
        return self._call("begin_checkout_attempt", owner=owner, **args)

    def record_checkout_session(self, owner, **args):
        return self._call("record_checkout_session", owner=owner, **args)

    def settle_checkout_attempt(self, owner, **args):
        return self._call("settle_checkout_attempt", owner=owner, **args)
