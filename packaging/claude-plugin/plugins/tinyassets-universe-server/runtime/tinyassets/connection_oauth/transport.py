"""The one network path OAuth uses: the SSRF-hardened outbound driver.

Discovery, dynamic client registration, the code exchange and every refresh go
to URLs that came from data (a provider's own metadata, discovered from the
connection's own host), never from code. So each request is held to the same
egress rules as any connection call: HTTPS only, public addresses only (DNS
pinned, rebinding re-checked), no redirects, bounded time and size, and an
allowlist of exactly the one URL being requested.

Errors are :class:`OAuthError` with a fixed ``code`` and a bounded,
secret-scrubbed ``detail`` taken from the server's own RFC 6749 error fields,
which is the provider-reported detail a failure record carries.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlencode, urlsplit

#: Seconds for one OAuth HTTP exchange end to end.
TIMEOUT_SECONDS = 15.0
#: Largest OAuth response read. Metadata and token responses are small.
MAX_BODY_BYTES = 65536
_MAX_URL_CHARS = 2048
#: Path characters an endpoint may use. ``{`` / ``}`` would be read as an
#: allowlist template, and whitespace or controls have no place in a URL.
_PATH_RE = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*\Z")
_DETAIL_LIMIT = 200


class OAuthError(Exception):
    """Secret-free failure: a fixed code plus the server's own bounded detail."""

    def __init__(self, code: str, detail: str = "", *, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.status = status


def validate_https_url(value: Any, *, allow_query: bool = False) -> str:
    """A plain public ``https://host/path`` URL, or ``OAuthError``.

    No userinfo, no fragment, no port other than the default, and (unless
    ``allow_query``) no query: an endpoint's query would have to be declared in
    the allowlist, and none of the OAuth endpoints need one.
    """
    if not isinstance(value, str) or not value or len(value) > _MAX_URL_CHARS:
        raise OAuthError("invalid_oauth_url")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise OAuthError("invalid_oauth_url") from None
    if (parts.scheme != "https" or not parts.hostname or parts.username
            or parts.password or parts.fragment or port not in (None, 443)
            or (parts.query and not allow_query)
            or not _PATH_RE.match(parts.path or "/")):
        raise OAuthError("invalid_oauth_url")
    return value


def _endpoint_for(url: str, method: str):
    from tinyassets.storage.outbound_connections import (
        SsrfValidationError,
        _parse_allowed_endpoints,
    )

    parts = urlsplit(url)
    try:
        return tuple(_parse_allowed_endpoints([{
            "host": (parts.hostname or "").lower(),
            "path_template": parts.path or "/",
            "methods": [method],
        }]))
    except (SsrfValidationError, ValueError, TypeError):
        # An IP literal, a single-label host, localhost and the like: the
        # connection allowlist refuses them, and so does OAuth.
        raise OAuthError("oauth_endpoint_not_permitted") from None


def scrub(text: Any, secrets: tuple[str, ...] = ()) -> str:
    """One printable, bounded line with every known secret removed."""
    if not isinstance(text, str):
        return ""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    line = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
    return line if len(line) <= _DETAIL_LIMIT else line[: _DETAIL_LIMIT - 3] + "..."


def server_error_detail(status: int, doc: Any, secrets: tuple[str, ...] = ()) -> str:
    """The server's own words: HTTP status plus RFC 6749 ``error`` fields."""
    extras = []
    if isinstance(doc, dict):
        for key in ("error", "error_description"):
            value = doc.get(key)
            if isinstance(value, str) and value:
                extras.append(value)
    text = f"HTTP {status}" + (": " + " - ".join(extras) if extras else "")
    return scrub(text, secrets)


def request_json(
    method: str,
    url: str,
    *,
    form: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    secrets: tuple[str, ...] = (),
    basic_auth: tuple[str, str] | None = None,
) -> tuple[int, Any]:
    """One bounded request; returns ``(status, parsed JSON or None)``.

    ``secrets`` are values this request carries (a refresh token, a code) so
    they are scrubbed from anything that could surface as detail.
    """
    from tinyassets.storage import outbound_connections as oc

    method = method.upper()
    validate_https_url(url)
    endpoints = _endpoint_for(url, method)
    headers = {"Accept": "application/json", "User-Agent": "TinyAssets-connection"}
    body: Any = None
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = urlencode(form)
    elif json_body is not None:
        body = json_body
    # Looked up on the module at call time, so the transport a deployment (or a
    # test's loopback) installs is the one used, never an import-time copy.
    driver = oc._SsrfHardenedHttpDriver(
        timeout=TIMEOUT_SECONDS, max_total_seconds=TIMEOUT_SECONDS,
        max_body_bytes=MAX_BODY_BYTES,
    )
    try:
        result = driver(
            bundle=(oc.ConnectionSecretBundle(username=basic_auth[0], password=basic_auth[1])
                    if basic_auth else oc.ConnectionSecretBundle()),
            auth_scheme="basic" if basic_auth else "none", method=method,
            url=url, headers=headers, body=body, allowed_endpoints=endpoints,
        )
    except oc.SsrfValidationError:
        raise OAuthError("oauth_endpoint_not_permitted") from None
    except Exception:  # noqa: BLE001 - transport text may carry request material
        raise OAuthError("oauth_server_unreachable") from None
    status = result.get("status") if isinstance(result, dict) else None
    if not isinstance(status, int):
        raise OAuthError("oauth_server_unreachable")
    raw = result.get("body")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        doc = json.loads(raw) if isinstance(raw, str) and raw.strip() else None
    except (ValueError, RecursionError):
        doc = None
    return status, doc
