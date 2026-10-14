"""Standard Webhooks, using the existing public-IP-pinned HTTPS substrate."""

from __future__ import annotations

import base64
import hmac
import json
import secrets
import time
from urllib.parse import urlsplit

MAX_BODY = 262144


class EventError(ValueError):
    def __init__(self, code, message, **data):
        super().__init__(message)
        self.code, self.data = code, data


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def signing_key(secret):
    try:
        if not isinstance(secret, str) or not secret.startswith("whsec_"):
            raise ValueError
        key = base64.b64decode(secret[6:], validate=True)
        if not 24 <= len(key) <= 64:
            raise ValueError
        return key
    except ValueError:
        raise EventError(-32602, "Invalid signing secret") from None


def callback_url(value):
    try:
        if not isinstance(value, str) or not 1 <= len(value) <= 4096:
            raise ValueError
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.port not in (None, 443)
            or url.fragment
            or "\\" in value
            or any(ord(c) <= 32 or ord(c) >= 127 for c in value)
        ):
            raise ValueError
        return value
    except ValueError:
        raise EventError(-32602, "Callback requires public HTTPS on port 443") from None


def signed_headers(subscription_id, event_id, body, secret, *, old_secret=None, now=None):
    stamp = str(int(time.time() if now is None else now))
    material = event_id.encode() + b"." + stamp.encode() + b"." + body
    signatures = [
        "v1," + base64.b64encode(hmac.digest(signing_key(key), material, "sha256")).decode()
        for key in (secret, old_secret)
        if key
    ]
    return {
        "Content-Type": "application/json",
        "webhook-id": event_id,
        "webhook-timestamp": stamp,
        "webhook-signature": " ".join(signatures),
        "X-MCP-Subscription-Id": subscription_id,
    }


def post(url, body, headers):
    from tinyassets.storage.outbound_connections import (
        _classify_global_address,
        _default_open_socket,
        _default_ssl_context,
        _make_default_resolver,
        _PinnedHTTPSConnection,
        _resolve_pinned_addresses,
    )

    target = urlsplit(callback_url(url))
    if len(body) > MAX_BODY:
        raise EventError(-32602, "Event exceeds 256 KiB")
    deadline = time.monotonic() + 10
    addresses = _resolve_pinned_addresses(
        target.hostname, 443, resolver=_make_default_resolver(3), validator=_classify_global_address
    )
    connection = _PinnedHTTPSConnection(
        target.hostname,
        pinned_address=addresses[0],
        open_socket=_default_open_socket,
        context=_default_ssl_context(),
        timeout=5,
        deadline=deadline,
    )
    try:
        connection.request(
            "POST",
            (target.path or "/") + ("?" + target.query if target.query else ""),
            body=body,
            headers=headers,
        )
        response = connection.getresponse()
        # No redirect handling, ambient proxy, credentials or response logging.
        content = response.read(4097)
        if len(content) > 4096:
            raise EventError(-32015, "Callback verification failed", reason="response_too_large")
        return response.status, content
    finally:
        connection.close()


def verify(subscription_id, url, secret, *, sender=post):
    challenge = secrets.token_urlsafe(32)
    body = canonical({"type": "verification", "challenge": challenge}).encode()
    ident = "verify_" + secrets.token_hex(16)
    try:
        status, response = sender(url, body, signed_headers(subscription_id, ident, body, secret))
        echoed = json.loads(response).get("challenge")
        if (
            not 200 <= status < 300
            or not isinstance(echoed, str)
            or not hmac.compare_digest(echoed.encode(), challenge.encode())
        ):
            raise ValueError
    except TimeoutError:
        raise EventError(-32015, "Callback verification failed", reason="timeout") from None
    except Exception:
        # Never echo callback URLs, response content or signing keys into RPC errors.
        raise EventError(
            -32015, "Callback verification failed", reason="challenge_failed"
        ) from None
