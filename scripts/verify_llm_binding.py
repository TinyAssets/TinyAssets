"""LLM binding verifier — post-deploy smoke for HD-3.

Confirms the running daemon has at least one LLM provider bound (i.e.
``llm_endpoint_bound`` in get_status is not ``"unset"``). This is a
binding canary only: it does not mutate the live universe or claim a full
provider-chain run succeeded.

Exit codes
----------
0   llm_endpoint_bound is set (and sandbox is available when requested).
1   MCP protocol error or unexpected response shape.
2   Network / connectivity error.
3   llm_endpoint_bound is "unset" — daemon has no LLM.
4   get_status tool returned an MCP error.
5   Required sandbox runtime is unavailable on the daemon host.

Usage
-----
    python scripts/verify_llm_binding.py
    python scripts/verify_llm_binding.py --url https://tinyassets.io/mcp
    python scripts/verify_llm_binding.py --url http://127.0.0.1:8001/mcp --timeout 15

Stdlib only — no third-party deps.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _canary_common import canary_bearer, canary_bearer_for  # noqa: E402

#: "Not specified" -- distinct from an explicit ``None``, which means "this
#: daemon is pre-cutover, send no bearer". Omitting the argument reads the
#: configured token WITHOUT a network call, so a direct caller (and every unit
#: test that drives a helper) behaves as it always did.
_FROM_ENV: Any = object()


DEFAULT_URL = "https://tinyassets.io/mcp"
DEFAULT_TIMEOUT = 20.0

_INIT_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "verify-llm-binding", "version": "1.0"},
    },
}
_INITIALIZED_NOTIF = {"jsonrpc": "2.0", "method": "notifications/initialized"}


class VerifyError(Exception):
    def __init__(self, code: int, msg: str) -> None:
        super().__init__(msg)
        self.code = code
        self.msg = msg


def _post(
    url: str,
    sid: str | None,
    payload: dict[str, Any],
    timeout: float,
    *,
    bearer_token: Any = _FROM_ENV,
) -> tuple[dict | None, str | None]:
    if bearer_token is _FROM_ENV:
        bearer_token = canary_bearer()
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "workflow-verify-llm/1.0",
    }
    if sid:
        headers["mcp-session-id"] = sid
    if bearer_token:
        # Absent against a pre-cutover daemon, which refuses this bearer.
        headers["Authorization"] = f"Bearer {bearer_token}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            new_sid = resp.headers.get("mcp-session-id") or sid
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        raise VerifyError(2, f"network error on {url}: {exc}") from exc

    result: dict | None = None
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            try:
                result = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                pass
        elif line.startswith("{"):
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                pass
    return result, new_sid


def _parse_status(result: dict[str, Any]) -> dict[str, Any]:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured

    for item in result.get("content", []):
        if item.get("type") == "text":
            try:
                return json.loads(item["text"])
            except (json.JSONDecodeError, KeyError):
                return {"raw": item["text"]}
    raise VerifyError(1, "get_status returned no text content")


def _llm_endpoint_bound(status: dict[str, Any]) -> Any:
    """Return the LLM binding from either historical or current status shape."""
    active_host = status.get("active_host")
    if isinstance(active_host, dict) and "llm_endpoint_bound" in active_host:
        return active_host.get("llm_endpoint_bound")
    if "llm_endpoint_bound" in status:
        return status.get("llm_endpoint_bound")
    return "unset"


def _sandbox_status(status: dict[str, Any]) -> dict[str, Any]:
    value = status.get("sandbox_status")
    return value if isinstance(value, dict) else {}


def check_llm_binding(
    url: str,
    timeout: float,
    *,
    require_sandbox: bool = False,
    post_fn=None,  # injection seam for tests
    bearer_token: Any = _FROM_ENV,
) -> dict[str, Any]:
    """Run the full binding verification. Returns the final status dict.

    Raises VerifyError with an appropriate exit code on any failure.
    """
    if bearer_token is _FROM_ENV:
        bearer_token = canary_bearer()
    _post_fn = post_fn or _post

    # Step 1: initialize
    resp, sid = _post_fn(
        url, None, _INIT_PAYLOAD, timeout, bearer_token=bearer_token,
    )
    if not resp or "result" not in resp:
        raise VerifyError(1, f"MCP initialize failed: {resp!r}")
    _post_fn(
        url, sid, _INITIALIZED_NOTIF, timeout, bearer_token=bearer_token,
    )

    # Step 2: get_status — check llm_endpoint_bound
    status_result = _call_tool_with(
        url, sid, "get_status", {}, timeout, _post_fn, bearer_token,
    )
    status = _parse_status(status_result)

    llm_bound = _llm_endpoint_bound(status)
    print(f"[verify-llm] get_status llm_endpoint_bound={llm_bound!r}")

    if str(llm_bound).lower() in ("unset", "", "false", "none"):
        raise VerifyError(
            3,
            f"llm_endpoint_bound is {llm_bound!r} — this host reports no model "
            "CLI it could start. This is a self-host check only: the platform "
            "daemon has no LLM by design (AGENTS.md Hard Rule 15), and a "
            "universe runs on the provider its owner connects to it.",
        )

    if require_sandbox:
        sandbox = _sandbox_status(status)
        if not sandbox.get("box_available"):
            reason = sandbox.get("reason", "sandbox_status missing")
            raise VerifyError(
                5,
                "subscription LLM is bound, but the owner box runtime is "
                f"unavailable: {reason}. The daemon must serve from owner "
                "boxes (boxhostd) for any node work to run.",
            )
        print("[verify-llm] sandbox_status.box_available=true")

    return status


def _call_tool_with(
    url: str,
    sid: str | None,
    tool: str,
    args: dict[str, Any],
    timeout: float,
    post_fn,
    bearer_token: str,
) -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }
    resp, _ = post_fn(
        url, sid, payload, timeout, bearer_token=bearer_token,
    )
    if resp is None or "result" not in resp:
        raise VerifyError(1, f"tools/call {tool!r} got no result: {resp!r}")
    if resp["result"].get("isError"):
        content = resp["result"].get("content", [])
        text = next((c["text"] for c in content if c.get("type") == "text"), "")
        raise VerifyError(4, f"tools/call {tool!r} returned isError: {text[:300]}")
    return resp["result"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify the daemon has an LLM provider bound (HD-3 post-deploy check)."
    )
    ap.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"MCP endpoint URL (default: {DEFAULT_URL})",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Per-request timeout seconds (default {DEFAULT_TIMEOUT})",
    )
    ap.add_argument(
        "--require-sandbox",
        action="store_true",
        help="Fail unless get_status reports sandbox_status.box_available=true.",
    )
    ap.add_argument(
        "--retries",
        type=int,
        default=1,
        help="Total verification attempts before failing (default 1).",
    )
    ap.add_argument(
        "--retry-delay",
        type=float,
        default=5.0,
        help="Seconds to sleep between retry attempts (default 5).",
    )
    args = ap.parse_args(argv)
    # Which contract does THIS daemon keep? Asked once, after the URL is
    # known, so one run never mixes the pre- and post-cutover shapes.
    bearer = canary_bearer_for(args.url, "verify-llm", args.timeout)

    attempts = max(1, args.retries)
    retry_delay = max(0.0, args.retry_delay)
    last_error: VerifyError | None = None
    for attempt in range(1, attempts + 1):
        try:
            status = check_llm_binding(
                args.url,
                args.timeout,
                require_sandbox=args.require_sandbox,
                bearer_token=bearer,
            )
            llm_bound = _llm_endpoint_bound(status)
            print(f"[verify-llm] PASS — llm_endpoint_bound={llm_bound!r}")
            if attempt > 1:
                print(f"[verify-llm] recovered after {attempt} attempt(s)")
            return 0
        except VerifyError as exc:
            last_error = exc
            if attempt >= attempts:
                break
            print(
                f"[verify-llm] WARN attempt {attempt}/{attempts} failed "
                f"(exit {exc.code}): {exc.msg}; retrying in {retry_delay:g}s",
                file=sys.stderr,
            )
            if retry_delay:
                time.sleep(retry_delay)

    assert last_error is not None
    print(
        f"[verify-llm] FAIL (exit {last_error.code}): {last_error.msg}",
        file=sys.stderr,
    )
    return last_error.code


if __name__ == "__main__":
    sys.exit(main())
