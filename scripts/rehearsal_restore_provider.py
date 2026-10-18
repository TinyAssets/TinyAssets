"""Recorded providers, mounted only by the network-less rehearsal entry point.

Not a runtime module or an image member. The fixture is explicitly a recording
of synthetic provider responses, not evidence of current upstream facts.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SECRET = "rehearsal-only-entitlement-secret"


def record(data, owners, destination):
    from tinyassets.billing import stripe_adapter as stripe

    subscriptions = []
    with sqlite3.connect(data / ".broker/plans.db") as db:
        db.row_factory = sqlite3.Row
        for row in db.execute("SELECT * FROM plans"):
            if not row["subscription_id"]:
                continue
            price = {"id": "price_recorded", "lookup_key": stripe.PRICE_LOOKUP_KEY,
                     "unit_amount": stripe.PLAN_UNIT_AMOUNT, "currency": stripe.PLAN_CURRENCY,
                     "recurring": {"interval": stripe.PLAN_INTERVAL, "interval_count": 1}}
            subscriptions.append({
                "id": row["subscription_id"], "customer": row["customer_id"],
                "status": "active" if row["tier"] == "paid" else "canceled",
                "metadata": {"owner_id": row["owner"], "tinyassets_entitlement_version": "1",
                             "tinyassets_entitlement_claim": stripe._entitlement_claim(
                                 row["owner"], price["id"], secret=SECRET, version="1")},
                "items": {"data": [{"quantity": 1, "price": price}]}})
    destination.write_text(json.dumps({
        "users": [owner for owner, _ in owners], "subscriptions": subscriptions,
        "unreachable": False}))


class RecordedProvider:
    def __init__(self, recording):
        self.recording = recording
        self.reconciled = set()

    def list_users(self):
        from tinyassets.router_account_deletion import AccountDeletionError

        if self.recording["unreachable"]:
            raise AccountDeletionError("recorded identity provider unreachable")
        return self.recording["users"]

    def identity_exists(self, owner):
        return owner in self.list_users()

    def revoke_sessions(self, owner):
        if owner not in self.list_users():
            raise ValueError("cannot revoke absent recorded identity")
        self.reconciled.add(owner)

    def subscription_lookup(self, path):
        if self.recording["unreachable"]:
            raise RuntimeError("recorded billing provider unreachable")
        subscriptions = self.recording["subscriptions"]
        if path == "subscriptions?status=all&limit=100":
            return {"data": subscriptions, "has_more": False}
        for subscription in subscriptions:
            if path == "subscriptions/" + subscription["id"]:
                return subscription
        raise ValueError("unrecorded subscription lookup")

    def billing_sync(self, root, now):
        from unittest.mock import patch

        from tinyassets.billing import stripe_adapter as stripe

        if self.reconciled != set(self.list_users()):
            raise ValueError("not every surviving recorded owner was reconciled")
        with (patch.object(stripe, "_get", self.subscription_lookup),
              patch.object(stripe, "_secret_key", lambda: "sk_test_recorded"),
              patch.object(stripe, "_webhook_secret", lambda secret="": secret or SECRET)):
            return stripe.restore_billing(root, now)


def adapters():
    provider = RecordedProvider(json.loads(Path("/backup/replay/provider.json").read_text()))
    return {"identity_exists": provider.identity_exists,
            "revoke_sessions": provider.revoke_sessions, "billing_sync": provider.billing_sync}
