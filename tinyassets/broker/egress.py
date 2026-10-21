"""C7/T6: one admitted-owner, public-address check for every proxy transport."""
from __future__ import annotations

from contextvars import ContextVar
from ipaddress import ip_address

from tinyassets.broker.admission import require_admitted

# Set only by the broker from the authenticated channel, never request fields.
identity = ContextVar("egress_identity", default=None)


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
    identities, owner = binding
    require_admitted(identities, owner)
    if not 0 < port < 65536:
        raise SsrfValidationError("invalid destination port")
    try:
        literal = str(ip_address(host))
    except ValueError:
        literal = None
    if literal is not None:
        return [(validator or _classify_global_address)(literal)]
    return _resolve_pinned_addresses(
        host, port, resolver=resolver or _make_default_resolver(10),
        validator=validator or _classify_global_address,
    )
