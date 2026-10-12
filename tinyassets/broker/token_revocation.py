"""RFC 7009 revocation for a disconnected delegation.

The broker opens the record, calls the issuer when discovery advertises a
revocation endpoint, then the caller deletes the sealed record. When the
issuer offers no endpoint, the notice is the owner's to read. A failed call
leaves the record in place so the disconnect can be retried.
"""

from __future__ import annotations

import json
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

_NO_REVOCATION = "the issuer offered no revocation; the grant stays valid there until it expires"


class RevocationFailed(RuntimeError):
    """The issuer advertises revocation and did not accept it. No token text."""


def notice_for(record: dict[str, Any]) -> str:
    """Revoke when an endpoint is known. Return the owner-facing notice, or ''."""
    bundle = _bundle(record)
    if bundle is None:
        return ""
    token = bundle.get("refresh_token") or bundle.get("access_token") or ""
    if not isinstance(token, str) or not token:
        return ""
    endpoint = _endpoint(bundle)
    if not endpoint:
        return _NO_REVOCATION
    hint = "refresh_token" if bundle.get("refresh_token") else "access_token"
    body = urlparse.urlencode({"token": token, "token_type_hint": hint}).encode("utf-8")
    request = urlrequest.Request(
        endpoint,
        data=body,
        method="POST",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlrequest.urlopen(request, timeout=10) as response:
            response.read()
    except urlerror.HTTPError as exc:
        if exc.code in {200, 204}:
            return "the refresh token was revoked at the issuer"
        raise RevocationFailed("the issuer refused revocation") from None
    except (OSError, TimeoutError, urlerror.URLError) as exc:
        raise RevocationFailed("the issuer revocation endpoint was unreachable") from exc
    return "the refresh token was revoked at the issuer"


def _bundle(record: dict[str, Any]) -> dict[str, Any] | None:
    token = record.get("token")
    if isinstance(token, str) and token.lstrip().startswith("{"):
        try:
            document = json.loads(token)
        except ValueError:
            return None
        if isinstance(document, dict) and document.get("scheme") == "oauth2":
            return document
    if record.get("refresh_token") or record.get("revocation_endpoint"):
        return record
    return None


def _endpoint(bundle: dict[str, Any]) -> str:
    named = bundle.get("revocation_endpoint")
    if isinstance(named, str) and named.startswith("https://"):
        return named
    provider_id = bundle.get("provider_id")
    token_url = bundle.get("token_url")
    try:
        from tinyassets.connection_oauth.directory import entries
    except Exception:
        return ""
    for row in entries():
        endpoint = row.get("revocation_endpoint")
        if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
            continue
        if provider_id and row.get("id") == provider_id:
            return endpoint
        if token_url and row.get("token_endpoint") == token_url:
            return endpoint
    return ""
