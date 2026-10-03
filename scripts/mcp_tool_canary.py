"""MCP tool-invocation canary — end-to-end liveness beyond handshake.

Closes the gap flagged in task #6: `mcp_public_canary.py` only probes
``initialize``, which proves the daemon answers the MCP handshake but
NOT that any tool handler actually works. The ``handshake green, tool
handler crashed`` failure class would go undetected without this.

Canary flow
-----------
1. POST ``initialize`` → capture ``mcp-session-id`` header.
2. POST ``notifications/initialized`` (MCP protocol requires this
   before tool calls, even if the server is lenient).
3. POST ``tools/list`` → confirm the returned tools array is non-empty.
4. POST ``tools/call`` for canonical ``read_graph`` with ``target=status``.
   Advertising the forbidden legacy ``universe`` tool is drift (exit 5).

Exit codes (task #6 spec)
-------------------------
0 — all four steps passed.
2 — handshake failed (initialize error, network, TLS, non-200).
3 — session establishment failed (no ``mcp-session-id`` header, or
    ``notifications/initialized`` POST errored).
4 — ``tools/list`` failed or returned an empty tools array.
5 — probe ``tools/call`` failed or returned an invalid response
    (missing expected fields, isError set, etc.).

Usage
-----
    python scripts/mcp_tool_canary.py
    python scripts/mcp_tool_canary.py --url http://127.0.0.1:8001/mcp
    python scripts/mcp_tool_canary.py --verbose --timeout 20

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _canary_common import (  # noqa: E402
    _INITIALIZED_NOTIF,  # noqa: F401
    _extract_structured_tool_payload,
    _extract_tool_text,
    _init_payload,
    canary_bearer,
    canary_bearer_for,
)
from _canary_common import _post as _post_raw  # noqa: E402

#: "Not specified" -- distinct from an explicit ``None``, which means "this
#: daemon is pre-cutover, send no bearer". Omitting the argument reads the
#: configured token WITHOUT a network call, so a direct caller (and every unit
#: test that drives a helper) behaves as it always did.
_FROM_ENV: Any = object()


DEFAULT_URL = "https://tinyassets.io/mcp"
DEFAULT_TIMEOUT = 20.0

_INIT_PAYLOAD = _init_payload("mcp-tool-canary")


class ToolCanaryError(Exception):
    """Raised by any step; carries the exit code so main() can exit with it."""

    def __init__(self, code: int, msg: str) -> None:
        super().__init__(msg)
        self.code = code
        self.msg = msg


# `_post` is the shared HTTP+parse path from `_canary_common`, partially
# applied with this canary's exception constructor + User-Agent. Callers
# (and tests) use `_post(url, sid, payload, timeout, step_code=...)` as
# before — only the implementation is consolidated.
_post = partial(
    _post_raw,
    error_factory=ToolCanaryError,
    user_agent="tinyassets-mcp-tool-canary/1.0",
)


def _tool_names(tools: list[Any]) -> set[str]:
    return {
        tool.get("name")
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    }


def _select_probe(tools: list[Any]) -> tuple[str, dict[str, Any], str]:
    names = _tool_names(tools)
    if "universe" in names:
        raise ToolCanaryError(
            5,
            "tools/list advertised forbidden legacy tool 'universe'",
        )
    if "read_graph" in names:
        return "read_graph", {"target": "status"}, "read_graph status"
    raise ToolCanaryError(
        5,
        "tools/list did not advertise a supported read-only probe "
        f"(wanted 'read_graph'; saw {sorted(names)!r})",
    )


def _parse_tool_json_result(resp: dict[str, Any] | None, label: str) -> dict[str, Any]:
    if resp is None or "result" not in resp:
        raise ToolCanaryError(
            5, f"{label} returned no result: {resp!r}",
        )
    result = resp["result"]
    if result.get("isError"):
        text = _extract_tool_text(result)[:300]
        raise ToolCanaryError(
            5, f"{label} isError=true: {text!r}",
        )
    structured = _extract_structured_tool_payload(result)
    if structured is not None:
        return structured
    text = _extract_tool_text(result)
    if not text:
        raise ToolCanaryError(
            5, f"{label} returned no text content: {result!r}",
        )
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolCanaryError(
            5, f"{label} text not JSON: {exc}; preview={text[:200]!r}",
        ) from exc
    if not isinstance(obj, dict):
        raise ToolCanaryError(
            5, f"{label} JSON was not an object: {obj!r}",
        )
    return obj


def _validate_probe_obj(obj: dict[str, Any], label: str) -> None:
    if label == "read_graph status":
        if "schema_version" not in obj:
            raise ToolCanaryError(
                5, f"read_graph status missing schema_version: {obj!r}",
            )
        return

    raise ToolCanaryError(5, f"unsupported probe label: {label!r}")


def run_canary(
    url: str,
    timeout: float,
    *,
    post_fn=None,
    verbose: bool = False,
    bearer_token: Any = _FROM_ENV,
) -> dict[str, Any]:
    """Run the four-step canary. Returns the inspect-result dict on success.

    ``post_fn`` is an injection seam for tests so unit tests can feed
    scripted responses without network I/O. Signature matches ``_post``.
    Raises ``ToolCanaryError`` on any failure.
    """
    if bearer_token is _FROM_ENV:
        bearer_token = canary_bearer()
    post = post_fn or _post

    # Step 1 — initialize (handshake).
    resp, sid = post(
        url, None, _INIT_PAYLOAD, timeout, step_code=2,
        bearer_token=bearer_token,
    )
    if resp is None or "result" not in resp:
        raise ToolCanaryError(
            2, f"initialize returned no result: {resp!r}",
        )
    if "error" in resp:
        raise ToolCanaryError(
            2, f"initialize returned MCP error: {resp['error']!r}",
        )
    if verbose:
        print(f"[tool-canary] initialize OK sid={sid!r}")

    # Step 2 — session established (mcp-session-id present + notif accepted).
    if not sid:
        raise ToolCanaryError(
            3, "initialize response did not include mcp-session-id header",
        )
    # notifications/initialized has no id and no response body; we still POST
    # it for protocol conformance. HTTP-layer errors map to step_code=3.
    post(
        url, sid, _INITIALIZED_NOTIF, timeout, step_code=3,
        bearer_token=bearer_token,
    )
    if verbose:
        print("[tool-canary] session established OK")

    # Step 3 — tools/list and assert non-empty.
    list_payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    resp, _ = post(
        url, sid, list_payload, timeout, step_code=4,
        bearer_token=bearer_token,
    )
    if resp is None or "result" not in resp:
        raise ToolCanaryError(
            4, f"tools/list returned no result: {resp!r}",
        )
    tools = resp["result"].get("tools", [])
    if not isinstance(tools, list) or not tools:
        raise ToolCanaryError(
            4, f"tools/list returned empty or non-list tools: {tools!r}",
        )
    if verbose:
        names = [t.get("name") for t in tools]
        print(f"[tool-canary] tools/list OK ({len(tools)} tool(s)): {names}")

    # Step 4 - tools/call the strongest read-only probe advertised.
    tool_name, tool_args, label = _select_probe(tools)
    call_payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": tool_args},
    }
    resp, _ = post(
        url, sid, call_payload, timeout, step_code=5,
        bearer_token=bearer_token,
    )
    inspect_obj = _parse_tool_json_result(resp, label)
    _validate_probe_obj(inspect_obj, label)
    if verbose:
        uid = inspect_obj.get("command_center_id")
        schema_version = inspect_obj.get("schema_version")
        details = []
        if schema_version is not None:
            details.append(f"schema_version={schema_version!r}")
        if uid is not None:
            details.append(f"command_center_id={uid!r}")
        suffix = f" {' '.join(details)}" if details else ""
        print(f"[tool-canary] {label} OK{suffix}")

    return inspect_obj


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="End-to-end MCP tool-invocation canary (handshake + "
                    "session + tools/list + read-only tool call).",
    )
    ap.add_argument("--url", default=DEFAULT_URL,
                    help=f"MCP endpoint URL (default: {DEFAULT_URL})")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help=f"Per-request timeout seconds (default: {DEFAULT_TIMEOUT})")
    ap.add_argument("--verbose", action="store_true",
                    help="Print per-step OK lines to stdout.")
    args = ap.parse_args(argv)
    # Which contract does THIS daemon keep? Asked once, after the URL is
    # known, so one run never mixes the pre- and post-cutover shapes.
    bearer = canary_bearer_for(args.url, "tool-canary", args.timeout)

    try:
        inspect = run_canary(
            args.url,
            args.timeout,
            verbose=args.verbose,
            bearer_token=bearer,
        )
    except ToolCanaryError as exc:
        print(f"[tool-canary] FAIL (exit {exc.code}): {exc.msg}", file=sys.stderr)
        return exc.code

    if args.verbose:
        uid = inspect.get("command_center_id")
        schema_version = inspect.get("schema_version")
        details = []
        if schema_version is not None:
            details.append(f"schema_version={schema_version!r}")
        if uid is not None:
            details.append(f"command_center_id={uid!r}")
        suffix = f" {' '.join(details)}" if details else ""
        print(f"[tool-canary] PASS{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
