"""Daemon vault rotation for a broker stream, without credentials on the wire.

Authority is captured before OPEN. The broker reauthorizes that OPEN and holds
its send fence while asking for rotation. The daemon takes the existing refresh
and exclusive vault locks before spending, and finishes persistence even if the
stream disconnects. A lost acknowledgement never requires replaying a token.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def prepare(data_root, *, principal, command_center, grant_id, connection_id):
    from tinyassets.broker.ledger_queries import authorized_connection

    _, resource, _ = authorized_connection(
        data_root, principal=principal, command_center=command_center,
        grant_id=grant_id, connection_id=connection_id)
    if resource.connection_type != "http" or resource.auth_scheme != "oauth2":
        return None
    prefix = "vault://http/"
    if not resource.credential_ref.startswith(prefix):
        raise PermissionError("refresh custody is unavailable")
    destination = resource.credential_ref[len(prefix):]
    root = Path(data_root).resolve()
    universe = root / command_center
    if (universe.parent != root or universe.resolve() != universe
            or not destination or len(destination) > 128):
        raise PermissionError("refresh scope is unavailable")

    def refresh(document):
        from tinyassets.connection_oauth import tokens
        from tinyassets.connection_oauth.transport import OAuthError
        from tinyassets.credential_refresh import RefreshError, refresh_credential
        from tinyassets.credential_vault import http_credential_record, http_deposit_refusal

        if (set(document) != {"op", "sequence", "destination", "rejected_digest"}
                or document["op"] != "REFRESH"
                or document["destination"] != destination):
            raise PermissionError("refresh scope mismatch")
        digest = document["rejected_digest"]
        if not isinstance(digest, str) or (digest and (
                len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest))):
            raise ValueError("invalid rejected-token digest")
        store = tokens.ConnectionTokens(universe_dir=universe, owner_user_id=principal)

        def read():
            # Inside the exclusive vault hold: deposits/deletion cannot overtake
            # the ownership check, reread, spend and durable replacement.
            if http_deposit_refusal(universe, destination=destination, owner_user_id=principal):
                raise RefreshError("refresh custody is unavailable")
            return tokens.decode(store._read(destination))

        def spend(current):
            if not current.refresh_token:
                raise RefreshError("no refresh token; reconnect")
            try:
                return tokens.refresh(current)
            except OAuthError as exc:
                raise RefreshError(exc.detail or exc.code) from None

        refresh_credential(
            universe_dir=universe, lock_id=destination, owner_user_id=principal,
            universe_id=command_center, read=read,
            stale=lambda current: current.expiring() or bool(
                digest and hashlib.sha256(current.access_token.encode()).hexdigest() == digest),
            spend=spend, records=lambda fresh: [http_credential_record(
                destination=destination, token=tokens.encode(fresh))], subject="connection")

    return refresh
