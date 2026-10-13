"""Public MCP uptime canary — stdlib-only end-to-end probe.

What it asserts:

* ``--edge-preflight`` checks the Worker answers browser OPTIONS itself
  (allowed origin 204, disallowed origin 403) from
  ``tinyassets/mcp_browser_origins.json``. That is the post-Worker-deploy
  gate. It does not require the daemon's ``resource_metadata`` URL, which
  changes only when the daemon image rolls;
* anonymous ``initialize`` gets the canonical Bearer challenge;
* every MCP read runs as the scoped canary service principal;
* authenticated initialize, tools/list, and get_status remain healthy; and
* a cached anonymous converse call gets the hosted OAuth linking result without
  dispatch, while the canary service principal is refused (403).

Intended for continuous uptime monitoring per the 24/7 forever rule.
Tray wires this on a timer; on nonzero exit, tray surfaces an alert.

Exit codes
----------
0   Endpoint is healthy — full MCP initialize round-trip succeeded.
1   Endpoint is reachable but did not return a valid MCP initialize
    response (wrong content-type, missing fields, protocol mismatch).
2   Endpoint is unreachable (DNS failure, TCP refused, TLS failure,
    HTTP non-2xx).
3   Response parsed but MCP-level error returned (``jsonrpc`` error
    field present).
4   ``--assert-handles`` drift: the live ``tools/list`` does not advertise
    exactly the canonical handle set (``read_graph`` / ``write_graph`` /
    ``run_graph`` / ``read_page`` / ``write_page`` / ``converse`` /
    ``get_status`` — see ``CANONICAL_HANDLES`` below and
    ``openspec/specs/live-mcp-connector-surface/spec.md``). This is the
    PR-178 drift guard required by Hard Rule #11 after any
    DNS/tunnel/Worker/connector change.
5   ``--assert-handles`` status failure: ``tools/call get_status`` failed or
    omitted the uptime-critical ``active_host`` / ``release_state`` fields.
6   Authentication-boundary failure: anonymous initialize was admitted, or
    ``converse`` was not refused at both the anonymous and canary boundaries.

Usage
-----
    python scripts/mcp_public_canary.py
    python scripts/mcp_public_canary.py --url https://tinyassets.io/mcp
    python scripts/mcp_public_canary.py --timeout 15
    python scripts/mcp_public_canary.py --assert-handles   # Hard Rule #11

All output on failure goes to stderr so tray can stream it. stdout
stays silent unless ``--verbose`` is passed so the canary is cheap to
tail.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _canary_common import canary_bearer, canary_bearer_for  # noqa: E402

#: "Not specified" -- distinct from an explicit ``None``, which means "this
#: daemon is pre-cutover, send no bearer". Omitting the argument reads the
#: configured token WITHOUT a network call, so a direct caller (and every unit
#: test that drives a helper) behaves as it always did.
_FROM_ENV: Any = object()



def resource_metadata_urls(resource_url: str) -> tuple[str, str]:
    """RFC 9728 URL the challenge advertises, and the legacy path.

    Must match ``tinyassets.auth.wellknown.resource_metadata_url``. The legacy
    path keeps serving the same document.
    """
    resource = resource_url.strip().rstrip("/")
    parts = urlsplit(resource)
    suffix = "" if parts.path in ("", "/") else parts.path
    advertised = urlunsplit((
        parts.scheme,
        parts.netloc,
        "/.well-known/oauth-protected-resource" + suffix,
        "",
        "",
    ))
    legacy = f"{resource}/.well-known/oauth-protected-resource"
    return advertised, legacy


def edge_cors_policy() -> dict[str, Any]:
    """The origin list and CORS headers the Worker and the daemon both read."""
    path = Path(__file__).resolve().parents[1] / "tinyassets" / "mcp_browser_origins.json"
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CanaryError(6, f"browser origin policy unreadable: {exc}") from exc
    if not isinstance(policy, dict):
        raise CanaryError(6, "browser origin policy is not an object")
    missing = [
        key for key in (
            "origins", "allow_methods", "allow_headers", "expose_headers", "max_age",
        )
        if key not in policy
    ]
    if missing:
        raise CanaryError(6, f"browser origin policy missing {missing}")
    return policy


# Origins the Worker must answer at the edge. They have to stay in the shared
# policy; the probe does not keep a second copy of the allow-* header values.
_EDGE_ALLOWED_ORIGINS = ("https://claude.ai", "https://chatgpt.com")
_EDGE_DENIED_ORIGIN = "https://evil.example"
_EDGE_REQUEST_HEADERS = "authorization, content-type, mcp-protocol-version"


def _options(
    url: str,
    timeout: float,
    origin: str,
    accepted_http_statuses: frozenset[int],
) -> tuple[int, dict[str, str], bytes]:
    """One browser OPTIONS preflight. 204 is success; 403 is an accepted refusal."""
    request = urllib.request.Request(
        url,
        method="OPTIONS",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": _EDGE_REQUEST_HEADERS,
            "User-Agent": "tinyassets-mcp-canary/1.0",
        },
    )
    try:
        with urllib.request.urlopen(
            request,
            timeout=timeout,
            context=ssl.create_default_context(),
        ) as response:
            return (
                response.status,
                {key.lower(): value for key, value in response.headers.items()},
                response.read(),
            )
    except urllib.error.HTTPError as exc:
        if exc.code in accepted_http_statuses:
            return (
                exc.code,
                {key.lower(): value for key, value in exc.headers.items()},
                exc.read(),
            )
        raise CanaryError(2, f"HTTP {exc.code} from OPTIONS {url}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise CanaryError(2, f"unreachable OPTIONS {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise CanaryError(2, f"timeout after {timeout}s: OPTIONS {url}") from exc
    except ssl.SSLError as exc:
        raise CanaryError(2, f"TLS error OPTIONS {url}: {exc}") from exc
    except OSError as exc:
        raise CanaryError(2, f"socket error OPTIONS {url}: {exc}") from exc


def assert_edge_preflight(url: str, timeout: float) -> None:
    """The Worker answers OPTIONS itself, using the shared origin policy.

    A proxied preflight dies in Cloudflare Access as a plain 403, with no
    ``Access-Control-Allow-Origin``. This check fails on that, and it does
    not look at ``WWW-Authenticate``: that header is the daemon's, and the
    daemon image can still be the previous release when this Worker deploys.
    """
    policy = edge_cors_policy()
    origins = policy["origins"]
    for origin in _EDGE_ALLOWED_ORIGINS:
        if origin not in origins:
            raise CanaryError(6, f"{origin} is missing from the shared browser origin list")
    if _EDGE_DENIED_ORIGIN in origins:
        raise CanaryError(6, f"{_EDGE_DENIED_ORIGIN} must not be an allowed browser origin")

    for origin in _EDGE_ALLOWED_ORIGINS:
        status, headers, _body = _options(url, timeout, origin, frozenset({204}))
        allow_origin = headers.get("access-control-allow-origin", "")
        if (
            status != 204
            or allow_origin != origin
            or headers.get("access-control-allow-methods") != policy["allow_methods"]
            or headers.get("access-control-allow-headers") != policy["allow_headers"]
            or headers.get("access-control-expose-headers") != policy["expose_headers"]
            or headers.get("access-control-max-age") != str(policy["max_age"])
        ):
            raise CanaryError(
                6,
                f"edge preflight for {origin} on {url} was not answered from the "
                f"shared origin policy: HTTP {status} allow-origin={allow_origin!r}",
            )

    status, headers, body = _options(
        url, timeout, _EDGE_DENIED_ORIGIN, frozenset({403}),
    )
    if headers.get("access-control-allow-origin") or status != 403:
        raise CanaryError(
            6,
            f"disallowed origin {_EDGE_DENIED_ORIGIN} was reflected on {url}: HTTP {status}",
        )
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict) or payload.get("error") != "origin_not_allowed":
        raise CanaryError(
            6,
            f"disallowed origin {_EDGE_DENIED_ORIGIN} on {url} was not refused at the edge",
        )


def _anonymous_challenge_body(payload: Any) -> bool:
    """Missing-token 401: ``authentication_required`` plus a description.

    The ``error`` value stays, so existing clients keep working. The header
    itself carries no ``error`` parameter (RFC 6750).
    """
    if not isinstance(payload, dict):
        return False
    description = payload.get("error_description")
    return (
        payload.get("error") == "authentication_required"
        and isinstance(description, str)
        and bool(description.strip())
        and set(payload) <= {"error", "error_description"}
    )


DEFAULT_URL = "https://tinyassets.io/mcp"
DEFAULT_TIMEOUT = 10.0
#: The AuthKit authorization server the public resource document must advertise.
#:
#: WorkOS PRODUCTION since 2026-08-29. Production had been signing real users in
#: against a `-staging` environment on an `sk_test_` key. The switch took two attempts:
#: the first failed with `application_not_found` because the app's client id is a WorkOS
#: **Connect** OAuth application (PKCE, no secret) — a separate section from Applications
#: — with no production equivalent until one was created.
#:
#: Pinned deliberately. This value drifting is exactly the failure worth paging on: it
#: means the daemon is pointed at an authorization server nobody intended, and every
#: token it accepts was minted by that server.
EXPECTED_AUTHORIZATION_SERVERS = (
    "https://unassuming-environment-16.authkit.app",
)

_INIT_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "mcp-public-canary", "version": "1.0"},
    },
}

# PR-178 + 2026-07-24 canonical convergence: the live user-facing surface is
# exactly these seven handles. The canary asserts the deployed tools/list
# advertises all seven and nothing beyond them. Legacy fat tools are
# dual-registered but hidden from tools/list, so they must NOT appear here.
# `converse` is the relay handle (chatbot -> universe intelligence); the handle
# shape is provisional pending host ratification of the design-note open-Q.
CANONICAL_HANDLES = frozenset({
    "read_graph",
    "write_graph",
    "run_graph",
    "read_page",
    "write_page",
    "converse",
    "get_status",
})
_ALLOWED_ADVERTISED = CANONICAL_HANDLES


def _die(code: int, msg: str) -> None:
    print(f"[canary] {msg}", file=sys.stderr)
    sys.exit(code)


class CanaryError(Exception):
    """Probe failed. Carries the same (exit_code, message) shape as ``_die``."""

    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code
        self.msg = msg


def _parse_sse_or_json(body: bytes) -> dict[str, Any]:
    """MCP streamable-http returns either JSON or SSE ``event: message``
    frames. Accept both."""
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        raise ValueError("empty body")
    if text.startswith("{"):
        return json.loads(text)
    # SSE: find the first ``data: {...}`` line.
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload:
                return json.loads(payload)
    raise ValueError("no JSON or SSE data frame in response body")


def _post(
    url: str,
    payload: dict[str, Any],
    timeout: float,
    session_id: str | None = None,
    accepted_http_statuses: frozenset[int] = frozenset(),
    bearer: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    """POST one JSON-RPC frame. Return (status, headers, body). Raise on I/O.

    Factored out (and module-level) so unit tests can monkeypatch it to drive
    the handshake offline without a network.
    """
    if bearer is _FROM_ENV:
        bearer = canary_bearer()
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "tinyassets-mcp-canary/1.0",
    }
    if session_id:
        headers["mcp-session-id"] = session_id
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code in accepted_http_statuses:
            return (
                exc.code,
                {k.lower(): v for k, v in exc.headers.items()},
                exc.read(),
            )
        raise CanaryError(2, f"HTTP {exc.code} from {url}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise CanaryError(2, f"unreachable {url}: {exc.reason}") from exc
    except TimeoutError as exc:
        raise CanaryError(2, f"timeout after {timeout}s: {url}") from exc
    except ssl.SSLError as exc:
        raise CanaryError(2, f"TLS error {url}: {exc}") from exc
    except OSError as exc:
        raise CanaryError(2, f"socket error {url}: {exc}") from exc


def _get_json(url: str, timeout: float) -> dict[str, Any]:
    """GET one public JSON document used by the continuity probe."""
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "tinyassets-mcp-canary/1.0",
        },
    )
    try:
        with urllib.request.urlopen(
            req,
            timeout=timeout,
            context=ssl.create_default_context(),
        ) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
        raise CanaryError(6, f"protected resource metadata unavailable: {url}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanaryError(6, f"protected resource metadata is not JSON: {url}") from exc
    if not isinstance(payload, dict):
        raise CanaryError(6, f"protected resource metadata is not an object: {url}")
    return payload


def advertised_tool_names(
    url: str, timeout: float, *, bearer: Any = _FROM_ENV,
) -> set[str]:
    """Full MCP handshake → tools/list; return the advertised tool name set."""
    if bearer is _FROM_ENV:
        bearer = canary_bearer()
    status, headers, _ = _post(url, _INIT_PAYLOAD, timeout, bearer=bearer)
    if status != 200:
        raise CanaryError(2, f"non-200 status {status} from {url}")
    session_id = headers.get("mcp-session-id")
    if session_id:
        # Streamable-HTTP requires the initialized notification before reads.
        _post(
            url,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            timeout,
            session_id,
            bearer=bearer,
        )
    status, _, body = _post(
        url,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        timeout,
        session_id,
        bearer=bearer,
    )
    if status != 200:
        raise CanaryError(2, f"non-200 status {status} from {url} (tools/list)")
    try:
        payload = _parse_sse_or_json(body)
    except (ValueError, json.JSONDecodeError) as exc:
        raise CanaryError(1, f"non-MCP tools/list body from {url}: {exc}") from exc
    if "error" in payload:
        raise CanaryError(3, f"MCP error on tools/list from {url}: {payload['error']}")
    tools = (payload.get("result") or {}).get("tools") or []
    if not isinstance(tools, list):
        raise CanaryError(1, f"malformed tools/list from {url}: {tools!r}")
    return {t.get("name") for t in tools if isinstance(t, dict) and t.get("name")}


def assert_canonical_handles(
    url: str, timeout: float, *, bearer: Any = _FROM_ENV,
) -> None:
    """Raise ``CanaryError(4)`` unless tools/list is exactly the canonical set."""
    if bearer is _FROM_ENV:
        bearer = canary_bearer()
    names = advertised_tool_names(url, timeout, bearer=bearer)
    missing = CANONICAL_HANDLES - names
    extra = names - _ALLOWED_ADVERTISED
    if missing or extra:
        raise CanaryError(
            4,
            f"handle drift on {url}: missing={sorted(missing)} "
            f"extra={sorted(extra)} advertised={sorted(names)}",
        )


def assert_converse_auth_gate(
    url: str, timeout: float, *, bearer: Any = _FROM_ENV,
) -> None:
    """Prove ``converse`` links anonymous clients and refuses the canary."""
    if bearer is _FROM_ENV:
        bearer = canary_bearer()
    status, _, body = _post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "converse",
                "arguments": {"message": "mcp-public-canary auth boundary probe"},
            },
        },
        timeout,
        accepted_http_statuses=frozenset({200}),
    )
    if status != 200:
        raise CanaryError(
            6,
            f"converse auth gate expected hosted linking result HTTP 200 "
            f"from {url}, got {status}",
        )
    resource_url = url.rstrip("/")
    advertised_url, legacy_url = resource_metadata_urls(resource_url)
    expected_metadata = f'resource_metadata="{advertised_url}"'
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CanaryError(6, f"non-JSON converse auth challenge from {url}") from exc
    result = payload.get("result") if isinstance(payload, dict) else None
    meta = result.get("_meta") if isinstance(result, dict) else None
    challenges = (
        meta.get("mcp/www_authenticate") if isinstance(meta, dict) else None
    )
    if (
        not isinstance(payload, dict)
        or payload.get("id") != 3
        or not isinstance(result, dict)
        or result.get("isError") is not True
        or not isinstance(challenges, list)
        or len(challenges) != 1
        or not isinstance(challenges[0], str)
    ):
        raise CanaryError(6, f"unexpected converse linking result from {url}")
    challenge = challenges[0]
    if (
        not challenge.startswith("Bearer ")
        or expected_metadata not in challenge
        or 'error="invalid_token"' not in challenge
    ):
        raise CanaryError(
            6,
            f"converse auth gate resource metadata drift on {url}",
        )
    documents = [
        _get_json(metadata_url, timeout)
        for metadata_url in dict.fromkeys((advertised_url, legacy_url))
    ]
    if any(document != documents[0] for document in documents):
        raise CanaryError(
            6, f"protected resource documents diverged on {url}",
        )
    metadata = documents[0]
    authorization_servers = metadata.get("authorization_servers")
    if (
        metadata.get("resource") != resource_url
        or not isinstance(authorization_servers, list)
        or authorization_servers != list(EXPECTED_AUTHORIZATION_SERVERS)
    ):
        raise CanaryError(6, f"protected resource document drift on {url}")

    if bearer is None:
        # A pre-cutover daemon has no canary principal to refuse, so there is
        # no 403 to assert. The anonymous half above is the whole contract it
        # keeps, and it just passed.
        return

    status, headers, _ = _post(url, _INIT_PAYLOAD, timeout, bearer=bearer)
    if status != 200:
        raise CanaryError(2, f"non-200 status {status} from {url}")
    session_id = headers.get("mcp-session-id")
    if session_id:
        _post(
            url,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            timeout,
            session_id,
            bearer=bearer,
        )
    status, _, _ = _post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "converse",
                "arguments": {"message": "mcp-public-canary auth boundary probe"},
            },
        },
        timeout,
        session_id,
        accepted_http_statuses=frozenset({403}),
        bearer=bearer,
    )
    if status != 403:
        raise CanaryError(
            6,
            f"canary converse gate expected HTTP 403 from {url}, got {status}",
        )


def assert_status_surface(
    url: str, timeout: float, *, bearer: Any = _FROM_ENV,
) -> str:
    """Call ``get_status`` and verify its uptime-critical response fields.

    Return a compact identity-evidence state so verbose canary output makes
    fingerprint configuration degradation visible without treating it as a
    status-surface outage.
    """
    if bearer is _FROM_ENV:
        bearer = canary_bearer()
    status, headers, _ = _post(url, _INIT_PAYLOAD, timeout, bearer=bearer)
    if status != 200:
        raise CanaryError(2, f"non-200 status {status} from {url}")
    session_id = headers.get("mcp-session-id")
    if session_id:
        _post(
            url,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            timeout,
            session_id,
            bearer=bearer,
        )
    status, _, body = _post(
        url,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_status", "arguments": {}},
        },
        timeout,
        session_id,
        bearer=bearer,
    )
    if status != 200:
        raise CanaryError(5, f"non-200 status {status} from {url} (get_status)")
    try:
        rpc_payload = _parse_sse_or_json(body)
    except (ValueError, json.JSONDecodeError) as exc:
        raise CanaryError(5, f"non-MCP get_status body from {url}: {exc}") from exc
    if "error" in rpc_payload:
        raise CanaryError(
            5,
            f"MCP error on get_status from {url}: {rpc_payload['error']}",
        )
    result = rpc_payload.get("result") or {}
    if result.get("isError"):
        raise CanaryError(5, f"get_status returned a tool error from {url}")
    payload = result.get("structuredContent")
    if not isinstance(payload, dict):
        content = result.get("content") or []
        text_blocks = [
            item.get("text")
            for item in content
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        if not text_blocks:
            raise CanaryError(5, f"get_status returned no text payload from {url}")
        try:
            payload = json.loads(text_blocks[0])
        except json.JSONDecodeError as exc:
            raise CanaryError(
                5,
                f"get_status returned non-JSON text from {url}",
            ) from exc
    if not isinstance(payload, dict):
        raise CanaryError(5, f"get_status payload is not an object from {url}")
    missing = [
        field for field in ("active_host", "release_state") if field not in payload
    ]
    if missing:
        raise CanaryError(
            5,
            f"get_status uptime fields missing from {url}: {missing}",
        )

    identity_evidence = payload.get("identity_evidence")
    if not isinstance(identity_evidence, dict):
        return "unknown"
    identity_status = identity_evidence.get("status")
    if identity_status == "unavailable":
        reason = identity_evidence.get("reason")
        return f"unavailable:{reason}" if isinstance(reason, str) else "unavailable"
    return str(identity_status or "unknown")


def assert_canonical_handles_with_retry(
    url: str,
    timeout: float,
    retries: int = 5,
    delay: float = 3.0,
    _sleep=time.sleep,
    *,
    bearer: Any = _FROM_ENV,
) -> str:
    """Assert canonical handles and status surface, retrying transient blips.

    Return the compact identity-evidence state reported by ``get_status``.

    Wired into the post-deploy gate, where a single transient ``tools/list``
    failure would otherwise trip a rollback of an otherwise-healthy daemon
    (a fresh image can briefly serve before the surface fully settles). The
    last attempt's ``CanaryError`` propagates so a genuine regression still
    fails the deploy.
    """
    if bearer is _FROM_ENV:
        bearer = canary_bearer()
    attempts = max(1, retries)
    for attempt in range(1, attempts + 1):
        try:
            assert_canonical_handles(url, timeout, bearer=bearer)
            assert_converse_auth_gate(url, timeout, bearer=bearer)
            return assert_status_surface(url, timeout, bearer=bearer)
        except CanaryError as exc:
            if attempt >= attempts:
                raise
            print(
                f"[canary] handle assertion attempt {attempt}/{attempts} "
                f"failed ({exc.msg}); retrying in {delay}s",
                file=sys.stderr,
            )
            _sleep(delay)


def probe_result(
    url: str,
    timeout: float,
    expected_name: str | None = None,
    *,
    bearer: Any = _FROM_ENV,
) -> None:
    """Run the probe. Return None on success; raise ``CanaryError`` on failure.

    Importable by layered canary wrappers that need to log outcomes without
    exiting the process. ``probe()`` is the CLI-shaped thin adapter.
    """
    if bearer is _FROM_ENV:
        bearer = canary_bearer()
    if bearer is not None:
        # Assertion one, and the thing that would silently regress: an
        # initialize with NO bearer must draw the 401 challenge. Skipped only
        # against a pre-cutover daemon, which has no such contract to keep.
        try:
            status, challenge_headers, challenge_body = _post(
                url,
                _INIT_PAYLOAD,
                timeout,
                accepted_http_statuses=frozenset({401}),
            )
        except CanaryError as exc:
            if exc.code == 2 and exc.msg.startswith("HTTP "):
                raise CanaryError(
                    6,
                    f"surface did not challenge anonymous initialize on {url}: {exc.msg}",
                ) from exc
            raise
        challenge = challenge_headers.get("www-authenticate", "")
        try:
            challenge_payload = json.loads(challenge_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            challenge_payload = None
        advertised_url, _legacy_url = resource_metadata_urls(url)
        if (
            status != 401
            or not challenge.startswith("Bearer ")
            or f'resource_metadata="{advertised_url}"' not in challenge
            or 'error="' in challenge
            or not _anonymous_challenge_body(challenge_payload)
        ):
            raise CanaryError(
                6,
                f"surface admitted an anonymous initialize on {url}: HTTP {status}",
            )

    status, _, body = _post(url, _INIT_PAYLOAD, timeout, bearer=bearer)

    if status != 200:
        raise CanaryError(2, f"non-200 status {status} from {url}")

    try:
        payload = _parse_sse_or_json(body)
    except (ValueError, json.JSONDecodeError) as exc:
        preview = body[:200].decode("utf-8", errors="replace")
        raise CanaryError(1, f"non-MCP body from {url}: {exc}; preview={preview!r}") from exc

    if "error" in payload:
        raise CanaryError(3, f"MCP error response from {url}: {payload['error']}")

    result = payload.get("result") or {}
    if not isinstance(result, dict):
        raise CanaryError(1, f"malformed result (not a dict) from {url}: {result!r}")
    if not result.get("protocolVersion"):
        raise CanaryError(1, f"missing protocolVersion in result from {url}: {result!r}")
    server_info = result.get("serverInfo") or {}
    if not server_info.get("name"):
        raise CanaryError(1, f"missing serverInfo.name in result from {url}: {result!r}")
    if expected_name is not None and server_info["name"] != expected_name:
        raise CanaryError(
            1,
            f"serverInfo.name drift from {url}: expected {expected_name!r}, "
            f"got {server_info['name']!r}",
        )


def probe(
    url: str,
    timeout: float,
    expected_name: str | None = None,
    *,
    bearer: Any = _FROM_ENV,
) -> None:
    """CLI-shaped adapter — calls ``probe_result`` and ``_die``s on failure."""
    if bearer is _FROM_ENV:
        bearer = canary_bearer()
    try:
        probe_result(url, timeout, expected_name=expected_name, bearer=bearer)
    except CanaryError as exc:
        _die(exc.code, exc.msg)


def _pulse_only(url: str, timeout: float, *, verbose: bool = False) -> int:
    """Liveness from release facts authorized as the canary principal.

    Exit 0 when the daemon answers `/mcp/pulse` with a git sha. Anything else
    is a dead or wedged process. Deliberately narrow: the container needs to
    know it is serving, and proving the tool surface is a different question
    asked by a different, authenticated probe.
    """
    import json as _json
    import urllib.error
    import urllib.request

    endpoint = url.rstrip("/") + "/pulse"
    bearer = canary_bearer_for(url, "canary", timeout)
    request = urllib.request.Request(
        endpoint,
        headers={
            "Authorization": f"Bearer {bearer}",
            "User-Agent": "tinyassets-healthcheck/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(65536).decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        print(f"[canary] pulse {endpoint} HTTP {exc.code}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - any transport failure is dead
        print(f"[canary] pulse {endpoint} unreachable: {exc}", file=sys.stderr)
        return 1
    if status != 200:
        print(f"[canary] pulse {endpoint} HTTP {status}", file=sys.stderr)
        return 1
    try:
        payload = _json.loads(body)
    except ValueError:
        print(f"[canary] pulse {endpoint} returned non-JSON", file=sys.stderr)
        return 1
    sha = str(payload.get("git_sha") or "").strip()
    if not sha:
        print(f"[canary] pulse {endpoint} carries no git_sha", file=sys.stderr)
        return 1
    if verbose:
        print(f"[canary] pulse OK {endpoint} git_sha={sha}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Probe a public MCP endpoint.")
    ap.add_argument("--url", default=DEFAULT_URL, help=f"MCP endpoint URL (default {DEFAULT_URL})")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help=f"request timeout seconds (default {DEFAULT_TIMEOUT})")
    ap.add_argument("--verbose", action="store_true",
                    help="print success line to stdout")
    ap.add_argument("--assert-name",
                    help="require an exact MCP serverInfo.name value")
    ap.add_argument(
        "--pulse-only", action="store_true",
        help=(
            "probe authenticated GET <url>/pulse and nothing else (git sha, "
            "image tag, deploy time, uptime). It proves the process is up and "
            "serving; it does NOT prove the tool surface, which is what the "
            "full authenticated probe is for."
        ),
    )
    ap.add_argument("--assert-handles", action="store_true",
                    help="also assert tools/list advertises exactly the "
                         "canonical handle set incl. converse (PR-178 drift "
                         "guard, Hard Rule #11)")
    ap.add_argument("--assert-handles-retries", type=int, default=5,
                    help="retry the handle assertion N times before failing "
                         "(default 5) — absorbs transient post-deploy blips")
    ap.add_argument("--assert-handles-retry-delay", type=float, default=3.0,
                    help="seconds between handle-assertion retries (default 3)")
    ap.add_argument(
        "--edge-preflight", action="store_true",
        help=(
            "probe browser OPTIONS on <url> and nothing else. Allowed origins "
            "from tinyassets/mcp_browser_origins.json must get 204 with those "
            "CORS headers; a disallowed origin must get 403 and not be "
            "reflected. Does not check the daemon challenge URL."
        ),
    )
    args = ap.parse_args(argv)

    if args.pulse_only:
        return _pulse_only(args.url, args.timeout, verbose=args.verbose)
    if args.edge_preflight:
        try:
            assert_edge_preflight(args.url, args.timeout)
        except CanaryError as exc:
            _die(exc.code, exc.msg)
        if args.verbose:
            print(f"[canary] edge preflight OK {args.url}")
        return 0
    bearer = canary_bearer_for(args.url, "canary", args.timeout)

    probe(args.url, args.timeout, expected_name=args.assert_name, bearer=bearer)

    identity_state = "not-checked"
    if args.assert_handles:
        try:
            identity_state = assert_canonical_handles_with_retry(
                args.url,
                args.timeout,
                retries=args.assert_handles_retries,
                delay=args.assert_handles_retry_delay,
                bearer=bearer,
            )
        except CanaryError as exc:
            _die(exc.code, exc.msg)

    if args.verbose:
        checks: list[str] = []
        if args.assert_handles:
            checks = [
                "unsigned initialize challenged",
                "canonical handle set advertised",
                "converse refused without a principal",
                "converse refused for the canary",
            ]
            checks.append("get_status uptime fields present")
            checks.append(f"identity_evidence={identity_state}")
        else:
            checks = ["unsigned initialize challenged"]
        suffix = f" ({'; '.join(checks)})" if checks else ""
        contract = "reads as the canary principal"
        print(f"[canary] OK {args.url} [{contract}]{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
