"""Daemon vault open for a broker stream.

The broker applies an outbound credential and does not hold the master key.
It asks, on the stream it already reauthorized, for the grant's current
``vault://http/`` secret. The daemon opens the envelope and returns that
secret. A different credential ref is refused. A failure acknowledgement
carries no secret.
"""
from __future__ import annotations

import contextvars
from pathlib import Path

#: Set on the broker stream thread for the duration of one dispatch. Scripted
#: test dispatches ignore it. Production dispatch reads it and does not take
#: the opener as a new argument, so a dispatch that does not open vaults is
#: unchanged.
current_open: contextvars.ContextVar = contextvars.ContextVar(
    "tinyassets_vault_open", default=None,
)


def prepare(data_root, *, principal, command_center, grant_id, connection_id):
    from tinyassets.broker.ledger_queries import authorized_connection

    _, resource, _ = authorized_connection(
        data_root, principal=principal, command_center=command_center,
        grant_id=grant_id, connection_id=connection_id)
    if resource.connection_type != "http":
        return None
    prefix = "vault://http/"
    ref = resource.credential_ref or ""
    # A foreign scheme on an http row is the broker's existing fail-closed
    # path. Returning no opener leaves that refusal as a proxy error.
    if not ref.startswith(prefix):
        return None
    destination = ref[len(prefix):]
    root = Path(data_root).resolve()
    universe = root / command_center
    if (universe.parent != root or universe.resolve() != universe
            or not destination or len(destination) > 128 or "/" in destination
            or destination in (".", "..")):
        return None

    def open_record(document):
        from tinyassets.credential_vault import read_http_credential

        if (set(document) != {"op", "sequence", "credential_ref"}
                or document["op"] != "VAULT_OPEN"
                or document["credential_ref"] != ref):
            raise PermissionError("vault open scope mismatch")
        return read_http_credential(universe, destination)

    return open_record
