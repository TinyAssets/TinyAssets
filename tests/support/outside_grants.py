"""P0-3 outside-client grants in tests: the broker's policy store, in process.

The router reaches it through ``policy_submit`` (its owner channel) and the box
through ``owner_policy_operation`` (its box channel), both answered by the real
``_Connection._owner_policy`` with the matching role. ``RouterAdmission`` is the
router's one outside admission in front of an in-process app.
"""
from __future__ import annotations

import hashlib
import threading
from types import SimpleNamespace

from tests.support.admission import Plans, drive, projection
from tests.test_owner_policy import _session
from tinyassets.broker.policy import PolicyStore

OWNERS = ("alice", "bob")


def cookie(owner):
    return "interactive-cookie-" + owner + "-" + "p" * 32


def _admissions(root, owners):
    from tinyassets.broker.owner_identities import OwnerIdentities

    state = root / ".broker/state"
    state.mkdir(parents=True, mode=0o700, exist_ok=True)
    state.parent.chmod(0o700)
    identities = OwnerIdentities(state / "owner-identities.db", initialize=True, plans=Plans(root))
    for owner in owners:
        identities.resolve(owner, allocate=True)
        drive(identities.admit(owner, routing=projection(identities.path)))
    return identities


class LocalBroker:
    def __init__(self, root, monkeypatch, owners=OWNERS):
        from tinyassets.broker import client, owner_identities
        from tinyassets.broker.server import _Connection

        self.root = root
        self.store = PolicyStore(root, _admissions(root, owners))
        self.store.bootstrap()
        for owner in owners:
            _session(root, owner, cookie(owner))
        self.proofs = {owner: {"session_hash": hashlib.sha256(cookie(owner).encode())
                               .hexdigest()} for owner in owners}
        self.peers, self.streams, self.cancelled = {}, {}, []
        for role in ("owner", "box"):
            peer = object.__new__(_Connection)
            peer._role = role
            peer._server = SimpleNamespace(_policy_store=self.store, _streams=self.streams,
                                           _streams_lock=threading.Lock(),
                                           cancel=self.cancelled.append)
            self.peers[role] = peer
        self.calls = []
        original = owner_identities._owner_request

        def router(data_root, doc):
            if doc.get("op") != "OWNER_POLICY":
                return original(data_root, doc)
            self.calls.append(("router", doc["action"]))
            return self.peers["owner"]._owner_policy(
                {k: v for k, v in doc.items() if k not in {"generation", "token"}})

        monkeypatch.setattr(owner_identities, "_owner_request", router)
        monkeypatch.setattr(client, "owner_policy_operation", self.box)

    def box(self, data_root, owner, action, **fields):
        """The box channel: its client's allowlist, then the broker's box role."""
        from tinyassets.broker.client import BrokerRefused
        from tinyassets.broker.server import BOX_POLICY_ACTIONS

        if action not in BOX_POLICY_ACTIONS:
            raise ValueError("unsupported owner policy operation")
        self.calls.append(("box", action))
        answer = self.peers["box"]._owner_policy(
            {"op": "OWNER_POLICY", "action": action, "principal": owner, **fields})
        if answer["op"] == "OWNER_POLICY_REFUSED":
            raise BrokerRefused(answer["reason"])
        return answer["result"]

    def grant(self, owner, client, *, source="api_key", mode="full", scopes=()):
        """The owner's protected-session change, as the router submits it."""
        from tinyassets.broker.policy_submit import submit

        current = next((row["generation"] for row in self.store.outside_list(owner)
                        if row["client"] == client), 0)
        return submit(self.root, owner, "outside_set", session_proof=self.proofs[owner],
                      client=client, source=source, mode=mode, scopes=list(scopes),
                      expected_generation=current, name="")


def mint_key(broker, keys, owner, *scopes, name="ci bot"):
    """A key and its grant, as the router's mint writes them: no scopes is ``full``."""
    from tinyassets.api_keys import _client

    minted = keys.mint(owner, name=name)
    broker.grant(owner, _client(minted["key_id"]), mode="scoped" if scopes else "full",
                 scopes=scopes)
    return minted


class RouterAdmission:
    """``OwnerRouter``'s outside admission, in front of an in-process app."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        from tinyassets.auth.middleware import current_identity_or_none
        from tinyassets.owner_router import _admit_outside

        identity = current_identity_or_none() if scope["type"] == "http" else None
        if (identity is not None and identity.metadata.get("outside_origin") is not None
                and not await _admit_outside(identity, scope, receive, send)):
            return
        await self.app(scope, receive, send)
