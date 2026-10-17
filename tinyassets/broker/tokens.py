"""Broker-local current-token resolution and refresh."""
from __future__ import annotations

import time
from pathlib import Path

from tinyassets.connection_oauth import tokens
from tinyassets.connection_oauth.tokens import LOCK_WAIT_SECONDS, TokenBundle, decode, encode
from tinyassets.connection_oauth.transport import OAuthError


class ConnectionTokens:
    """Current access token for one universe's oauth2 connections.

    Built inside the broker child from its own config: the universe directory
    whose vault holds the bundle and the owner the vault records it under. A
    refresh that fails raises
    :class:`~tinyassets.storage.outbound_connections.ConnectionAuthorizationError`,
    an ordinary connection failure (stage ``connection``, class ``auth``) with
    the token endpoint's own words as its detail.
    """

    def __init__(self, *, universe_dir: str | Path, owner_user_id: str) -> None:
        self._universe_dir = Path(universe_dir)
        self._owner = str(owner_user_id)

    # The vault seam: read and write the ONE record, by its destination.
    def _read(self, destination: str) -> str:
        from tinyassets.credential_vault import load_credential_vault

        for record in load_credential_vault(self._universe_dir):
            if (str(record.get("credential_type") or "").lower() == "http"
                    and str(record.get("destination") or "").strip() == destination):
                value = record.get("token")
                if isinstance(value, str) and value.strip():
                    return value.strip()
        raise LookupError("credential reference is unavailable")

    def _write(self, destination: str, bundle: TokenBundle) -> None:
        from tinyassets.broker.vault import write_credential_vault
        from tinyassets.credential_vault import http_credential_record

        write_credential_vault(self._universe_dir,
            [http_credential_record(destination=destination, token=encode(bundle))],
            owner_user_id=self._owner, universe_id=self._universe_dir.name)

    @staticmethod
    def _failed(detail: str):
        from tinyassets.storage.outbound_connections import ConnectionAuthorizationError

        return ConnectionAuthorizationError(detail)

    def current(self, destination: str, credential: str, *, rejected: str = "") -> TokenBundle:
        """The bundle whose access token to send now.

        ``rejected`` is an access token the service just answered 401 to; the
        stored one is refreshed unless another holder already replaced it.
        """
        from tinyassets.broker.credential_refresh import RefreshError, refresh_credential
        from tinyassets.credential_vault import http_credential_record

        try:
            bundle = decode(credential)
        except ValueError:
            raise self._failed("the stored authorization is unreadable; reconnect") from None
        if not rejected and not bundle.expiring():
            return bundle
        def read() -> TokenBundle:
            # Re-read INSIDE the locks: the holder before us may have rotated it.
            try:
                from contextlib import closing

                from tinyassets.broker.vault import check_not_deleted, metadata_connection
                from tinyassets.credential_vault import http_deposit_refusal

                with closing(metadata_connection(self._universe_dir.parent)) as conn:
                    check_not_deleted(conn, self._owner)

                if http_deposit_refusal(self._universe_dir, destination=destination,
                                        owner_user_id=self._owner):
                    raise RefreshError("refresh custody is unavailable")
                return decode(self._read(destination))
            except (LookupError, ValueError):
                raise RefreshError(
                    "the stored authorization is unreadable; reconnect") from None

        def stale(current: TokenBundle) -> bool:
            # Another holder already refreshed: use theirs, never spend the
            # (possibly single-use) refresh token a second time.
            now = time.time()
            return current.expiring(now) or bool(
                rejected and current.access_token == rejected)

        def spend(current: TokenBundle) -> TokenBundle:
            if not current.refresh_token:
                raise RefreshError(
                    "the provider issued no refresh token; reconnect to sign in again")
            try:
                return tokens.refresh(current)
            except OAuthError as exc:
                raise RefreshError(exc.detail or exc.code) from None

        try:
            return refresh_credential(
                universe_dir=self._universe_dir,
                lock_id=destination,
                owner_user_id=self._owner,
                universe_id=self._universe_dir.name,
                read=read,
                stale=stale,
                spend=spend,
                records=lambda fresh: [
                    http_credential_record(destination=destination, token=encode(fresh))
                ],
                subject="connection",
                wait_seconds=LOCK_WAIT_SECONDS,
            )
        except RefreshError as exc:
            raise self._failed(exc.detail) from None
