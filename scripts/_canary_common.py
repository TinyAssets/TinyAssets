"""Shared helpers for the canary script family.

Eliminates triplicate copies of `_post`, `_extract_tool_text`,
`_INIT_PAYLOAD`, `_INITIALIZED_NOTIF` across mcp_tool_canary,
last_activity_canary, revert_loop_canary, and wiki_canary. A bug fix
to the JSON-RPC POST path (e.g. SSE/JSON parser, retry-after handling,
header tweak) now lands in one place — the partial-fix landmine that
BUG-028 demonstrated for the wiki path.

Scope (intentionally tight):
    - HTTP POST + parse logic.
    - MCP `initialize` payload builder + `notifications/initialized` constant.
    - Tool-result text / structured payload extraction.

Out of scope (per Task #14 conservative-scope rule):
    - Per-canary exception classes (ToolCanaryError, LastActivityError,
      RevertLoopError). Callers do `except XError as e` and depend on
      the type identity. Each canary keeps its own.
    - Each canary's main() body, CLI shape, exit codes. Pure
      import-substitution.

Stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections.abc import Callable
from typing import Any

CANARY_TOKEN_ENV = "TINYASSETS_WIKI_CANARY_TOKEN"


def canary_bearer() -> str | None:
    """Return the stripped canary bearer, or ``None`` when it is unset."""
    token = os.environ.get(CANARY_TOKEN_ENV, "").strip()
    return token or None


def require_canary_bearer(prog: str) -> str:
    """Return the canary bearer or terminate before any network access."""
    token = canary_bearer()
    if token is None:
        print(
            f"[{prog}] {CANARY_TOKEN_ENV} is required: no anonymous reads exist; "
            "every probe is the canary principal",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return token


def canary_bearer_for(url: str, prog: str, timeout: float = 10.0) -> str:
    """Return the required service-principal bearer for every canary request.

    ``url`` and ``timeout`` remain in the signature so existing probe call sites
    do not need a parallel compatibility migration. No network negotiation is
    performed and there is no unsigned fallback.
    """
    del url, timeout
    return require_canary_bearer(prog)

# MCP `notifications/initialized` is parameter-free and identical across
# every canary client; consolidating eliminates 4 copies.
_INITIALIZED_NOTIF: dict[str, Any] = {
    "jsonrpc": "2.0",
    "method": "notifications/initialized",
}


def _init_payload(client_name: str) -> dict[str, Any]:
    """Build the MCP `initialize` JSON-RPC payload for a named client.

    Each canary identifies itself via `clientInfo.name` so server-side
    logs can attribute calls. The protocolVersion + capabilities shape
    is identical across all canaries.
    """
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": client_name, "version": "1.0"},
        },
    }


def _post(
    url: str,
    sid: str | None,
    payload: dict[str, Any],
    timeout: float,
    *,
    step_code: int,
    error_factory: Callable[[int, str], Exception],
    user_agent: str,
    bearer_token: str | None = None,
    observe_headers: Callable | None = None,
) -> tuple[dict | None, str | None]:
    """Send one JSON-RPC POST. Returns (parsed_response_or_None, new_sid).

    Network/HTTP/TLS failures raise `error_factory(step_code, msg)` so
    the caller's exit code matches its own failure ladder. The caller
    passes its own exception constructor (e.g. `ToolCanaryError`,
    `LastActivityError`, `RevertLoopError`) and User-Agent string —
    these are the only meaningful per-canary differences in the
    pre-consolidation `_post` copies.

    Body parsing accepts both raw JSON and SSE `data: {...}` frames
    (MCP streamable-http servers may emit either).
    """
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": user_agent,
    }
    if sid:
        headers["mcp-session-id"] = sid
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if observe_headers is not None:
                observe_headers(resp.headers)
            new_sid = resp.headers.get("mcp-session-id") or sid
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise error_factory(
            step_code,
            f"HTTP {exc.code} on {payload.get('method','?')}: {exc.reason}",
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise error_factory(
            step_code,
            f"network error on {payload.get('method','?')}: {exc}",
        ) from exc

    parsed: dict | None = None
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                parsed = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                pass
        elif line.startswith("{"):
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                pass
    return parsed, new_sid


def owner_canary_sign_in(origin: str, timeout: float) -> tuple[str, dict, Any]:
    """Renew the canary's ordinary app session, then read ``/app/me``.

    ``scripts/canary_session.py`` holds the session like a signed-in phone app.
    No credentials, upstream text or owner rows are included in errors.
    """
    from scripts.canary_session import USER_AGENT, CanarySessionError, access_token

    try:
        token = access_token(origin)
        request = urllib.request.Request(origin + "/app/me", headers={
            "Authorization": "Bearer " + token, "Origin": origin, "User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return token, json.load(response), response.headers
    except CanarySessionError as exc:
        raise RuntimeError(str(exc)) from None
    except Exception:
        raise RuntimeError("canary_sign_in_failed") from None


def _extract_tool_text(tool_result: dict[str, Any]) -> str:
    """Concatenate the text from `content` items of type 'text'."""
    return "".join(
        item.get("text", "")
        for item in tool_result.get("content", [])
        if item.get("type") == "text"
    )


def _extract_structured_tool_payload(
    tool_result: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a dict payload from MCP structuredContent when available."""
    structured = tool_result.get("structuredContent")
    if not isinstance(structured, dict):
        return None
    nested = structured.get("result")
    if set(structured) == {"result"} and isinstance(nested, dict):
        return nested
    return structured
