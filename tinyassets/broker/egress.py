"""C7/T6: one admitted-owner, public-address check for every proxy transport."""
from __future__ import annotations

import re
from contextvars import ContextVar
from ipaddress import ip_address

from tinyassets.broker.admission import require_admitted

# Set only by the broker from the authenticated channel, never request fields.
identity = ContextVar("egress_identity", default=None)


class EgressPolicyRefused(PermissionError):
    def __init__(self, destination, generation, decision):
        self.detail = {**destination, "generation": generation, "decision": decision}
        super().__init__(f"owner egress policy: {decision}")


def normalize_destination(host, port):
    if not isinstance(host, str) or type(port) is not int or not 0 < port < 65536:
        raise ValueError("invalid destination")
    try:
        host = str(ip_address(host))
    except ValueError:
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
        if (len(host) > 253 or not host or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.split("."))):
            raise ValueError("invalid destination host")
    return {"host": host, "port": port}


def destination(binding, host, port, *, resolver=None, validator=None):
    """Return pinned public addresses after consulting the owner's admission."""
    from tinyassets.storage.outbound_connections import (
        SsrfValidationError,
        _classify_global_address,
        _make_default_resolver,
        _resolve_pinned_addresses,
    )

    if binding is None:
        raise PermissionError("egress owner is not bound")
    identities, owner, store, ledger = binding
    require_admitted(identities, owner)
    target = normalize_destination(host, port)
    host = target["host"]
    if store is None:
        raise PermissionError("owner policy unavailable")
    policy = store.read(owner)
    try:
        try:
            literal = str(ip_address(host))
        except ValueError:
            literal = None
        if literal is not None:
            addresses = [(validator or _classify_global_address)(literal)]
        else:
            addresses = _resolve_pinned_addresses(
                host, port, resolver=resolver or _make_default_resolver(10),
                validator=validator or _classify_global_address,
            )
    except (SsrfValidationError, ValueError, PermissionError, OSError):
        store.receipt_egress(owner, policy.generation, target, "deny_public_address")
        raise
    egress = policy.document["egress"]
    allowed = egress["mode"] == "open" or target in egress["destinations"]
    if not allowed and ledger is not None:
        import json

        with ledger._connect() as db:
            rows = db.execute(
                "SELECT c.owner_egress_json FROM outbound_connections c "
                "WHERE c.owner_user_id=? AND c.revoked_at IS NULL "
                "AND EXISTS (SELECT 1 FROM outbound_connection_grants g "
                "WHERE g.connection_id=c.connection_id AND g.owner_user_id=? "
                "AND g.revoked_at IS NULL)", (owner, owner)).fetchall()
        allowed = any(target in json.loads(row[0]) for row in rows)
    decision = "allow" if allowed else "ask" if egress["mode"] == "ask" else "deny"
    store.receipt_egress(owner, policy.generation, target, decision)
    if not allowed:
        raise EgressPolicyRefused(target, policy.generation, decision)
    return addresses
