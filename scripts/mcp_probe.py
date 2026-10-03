"""Minimal MCP client over streamable-http — ops CLI for querying prod daemon state.

Stdlib only. Handles the streamable-http SSE-wrapped responses.

Subcommands (convenience):
    tinyassets-probe status               → get_status
    tinyassets-probe universes            → universe action=list
    tinyassets-probe universe <id>        → universe action=inspect universe_id=<id>
    tinyassets-probe wiki                 → wiki action=list
    tinyassets-probe tools                → tools/list (same as --list)
    tinyassets-probe latency              → time initialize + get_status

Raw call:
    tinyassets-probe --tool get_status
    tinyassets-probe --tool universe --args '{"action":"list"}'
    tinyassets-probe --list
    tinyassets-probe --tool universe --args '{"action":"inspect","universe_id":"x"}' --raw

The canary principal may run ``status``, ``tools``, raw ``get_status``, and raw
``read_graph`` with exactly ``{"target":"status"}``. The server refuses every
other tool call for this bearer with HTTP 403.

All subcommands accept --url and --raw flags.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

#: The clock `_cmd_latency` measures with, as a module attribute so a test can
#: replace THIS and nothing else.
#:
#: The tests used to do `monkeypatch.setattr(mcp_probe.time, "monotonic", ...)`.
#: `mcp_probe` does `import time`, so `mcp_probe.time` IS the `time` module --
#: that patched `time.monotonic` process-wide, against a two-element iterator
#: sized for this function's own two calls. Anything else in the process
#: consulting the clock in that window stole a value and the test died with
#: `StopIteration`, which is the attributed cause of a recurring CI flake
#: (`test_latency_raw_includes_response`, `test_latency_subcommand_reports_elapsed_ms`).
#:
#: A longer fake sequence does not fix that: a stolen value just turns the loud
#: error into a wrong `latency_ms`. Only a seam nobody else reads does, which is
#: this. `_cmd_latency` reads it once into a local, so a mid-call replacement
#: cannot make its two readings come from different clocks.
_clock = time.monotonic

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _canary_common import canary_bearer_for  # noqa: E402

#: "Not specified" -- distinct from an explicit ``None``, which means "this
#: daemon is pre-cutover, send no bearer". Omitting the argument reads the
#: configured token WITHOUT a network call, so a direct caller (and every unit
#: test that drives a helper) behaves as it always did.
_FROM_ENV: Any = object()



DEFAULT_URL = "https://tinyassets.io/mcp"
MCP_PROTOCOL_VERSION = "2024-11-05"

# Set to True by --verbose at parse time; read by helpers.
_VERBOSE = False


def _vlog(msg: str) -> None:
    if _VERBOSE:
        print(f"[probe] {msg}", file=sys.stderr)


def _mcp_call(
    url: str,
    sid: str | None,
    payload: dict[str, Any],
    bearer_token: str | None = None,
) -> tuple[dict | None, str | None]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "User-Agent": "workflow-lead-probe/1.0",
    }
    if sid:
        headers["mcp-session-id"] = sid
    if bearer_token:
        # Absent against a pre-cutover daemon, which refuses this bearer.
        headers["Authorization"] = f"Bearer {bearer_token}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST", headers=headers
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        new_sid = resp.headers.get("mcp-session-id") or sid
        body = resp.read().decode()
    result = None
    for line in body.splitlines():
        if line.startswith("data:"):
            try:
                result = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                pass
    return result, new_sid


def _initialize(url: str, bearer_token: str) -> tuple[str | None, int]:
    """Run MCP initialize + notifications/initialized. Returns (sid, exit_code)."""
    _vlog(f"initialize → {url}")
    init_resp, sid = _mcp_call(
        url,
        None,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "clientInfo": {"name": "lead-probe", "version": "1"},
                "capabilities": {},
            },
        },
        bearer_token,
    )
    if not init_resp or "result" not in init_resp:
        print("initialize failed", file=sys.stderr)
        print(init_resp, file=sys.stderr)
        return None, 1
    _vlog(f"session-id: {sid}")
    _mcp_call(
        url,
        sid,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        bearer_token,
    )
    _vlog("notifications/initialized sent")
    return sid, 0


def _tool_response_exit_code(resp: dict | None) -> int:
    if not resp or "error" in resp or "result" not in resp:
        return 1
    if resp["result"].get("isError"):
        return 1
    return 0


def _call_tool(
    url: str,
    sid: str | None,
    tool: str,
    tool_args: dict,
    bearer_token: str,
    *,
    raw: bool,
) -> int:
    _vlog(f"tools/call {tool} args={tool_args}")
    resp, _ = _mcp_call(
        url,
        sid,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool, "arguments": tool_args},
        },
        bearer_token,
    )
    if raw:
        print(json.dumps(resp, indent=2))
        return _tool_response_exit_code(resp)
    if resp and "result" in resp:
        for item in resp["result"].get("content", []):
            if item.get("type") == "text":
                print(item["text"])
        if resp["result"].get("isError"):
            return 1
        return 0
    print(json.dumps(resp, indent=2))
    return 1


def _cmd_status(url: str, raw: bool, bearer_token: str) -> int:
    sid, rc = _initialize(url, bearer_token)
    if rc:
        return rc
    return _call_tool(url, sid, "get_status", {}, bearer_token, raw=raw)


def _cmd_universes(url: str, raw: bool, bearer_token: str) -> int:
    sid, rc = _initialize(url, bearer_token)
    if rc:
        return rc
    return _call_tool(
        url, sid, "universe", {"action": "list"}, bearer_token, raw=raw,
    )


def _cmd_universe(
    url: str, universe_id: str, raw: bool, bearer_token: str,
) -> int:
    sid, rc = _initialize(url, bearer_token)
    if rc:
        return rc
    return _call_tool(
        url, sid, "universe", {"action": "inspect", "universe_id": universe_id},
        bearer_token, raw=raw,
    )


def _cmd_wiki(url: str, raw: bool, bearer_token: str) -> int:
    sid, rc = _initialize(url, bearer_token)
    if rc:
        return rc
    return _call_tool(url, sid, "wiki", {"action": "list"}, bearer_token, raw=raw)


def _cmd_tools(url: str, raw: bool, bearer_token: str) -> int:
    sid, rc = _initialize(url, bearer_token)
    if rc:
        return rc
    resp, _ = _mcp_call(
        url, sid, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        bearer_token,
    )
    if not resp or "result" not in resp:
        print(json.dumps(resp, indent=2))
        return 1
    if raw:
        print(json.dumps(resp, indent=2))
        return 0
    for t in resp["result"]["tools"]:
        print(f"{t['name']:<20} {t.get('description', '').splitlines()[0][:80]}")
    return 0


def _format_latency_line(result: dict[str, Any]) -> str:
    status = "ok" if result.get("ok") else "error"
    return (
        f"latency_ms={result['latency_ms']} "
        f"status={status} "
        f"stage={result['stage']} "
        f"url={result['url']}"
    )


def _cmd_latency(url: str, raw: bool, bearer_token: str) -> int:
    clock = _clock
    start = clock()
    sid, rc = _initialize(url, bearer_token)
    if rc:
        latency_ms = int((clock() - start) * 1000)
        result = {
            "ok": False,
            "url": url,
            "latency_ms": latency_ms,
            "stage": "initialize",
        }
        print(json.dumps(result, indent=2) if raw else _format_latency_line(result))
        return rc

    resp, _ = _mcp_call(
        url,
        sid,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_status", "arguments": {}},
        },
        bearer_token,
    )
    latency_ms = int((clock() - start) * 1000)
    rc = _tool_response_exit_code(resp)
    result: dict[str, Any] = {
        "ok": rc == 0,
        "url": url,
        "latency_ms": latency_ms,
        "stage": "get_status",
    }
    if raw:
        result["response"] = resp
        print(json.dumps(result, indent=2))
    else:
        print(_format_latency_line(result))
    return rc


def _coerce_relaxed_value(value: str) -> Any:
    value = value.strip().strip("'\"")
    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _parse_relaxed_object(raw: str) -> dict[str, Any] | None:
    """Parse simple PowerShell-stripped JSON like {action:list,limit:5}.

    This is deliberately shallow. Nested JSON still needs valid JSON quoting.
    """
    text = raw.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    body = text[1:-1].strip()
    if not body:
        return {}

    result: dict[str, Any] = {}
    for part in body.split(","):
        key, sep, value = part.partition(":")
        if not sep:
            return None
        key = key.strip().strip("'\"")
        if not key:
            return None
        result[key] = _coerce_relaxed_value(value)
    return result


def _parse_tool_args(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        parsed = _parse_relaxed_object(raw)
        if parsed is None:
            raise ValueError(
                "--args must be a JSON object; simple PowerShell-stripped "
                "{action:list} objects are also accepted"
            ) from exc
    if not isinstance(parsed, dict):
        raise ValueError("--args must decode to an object")
    return parsed


def _add_subcommand_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", default=argparse.SUPPRESS, help="MCP endpoint URL")
    parser.add_argument(
        "--raw",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print full JSON response",
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tinyassets-probe",
        description="Query the TinyAssets MCP daemon from the command line.",
    )
    p.add_argument("--url", default=DEFAULT_URL, help="MCP endpoint URL")
    p.add_argument("--raw", action="store_true", help="print full JSON response")
    p.add_argument("--verbose", action="store_true",
                   help="log initialize/tool-call progress to stderr")

    sub = p.add_subparsers(dest="subcommand")

    status = sub.add_parser("status", help="call get_status")
    _add_subcommand_flags(status)

    universes = sub.add_parser("universes", help="list all universes")
    _add_subcommand_flags(universes)

    uni = sub.add_parser("universe", help="inspect a specific universe")
    uni.add_argument("universe_id", help="universe ID to inspect")
    _add_subcommand_flags(uni)

    wiki = sub.add_parser("wiki", help="list wiki pages")
    _add_subcommand_flags(wiki)

    tools = sub.add_parser("tools", help="list available MCP tools")
    _add_subcommand_flags(tools)

    latency = sub.add_parser("latency", help="time initialize + get_status")
    _add_subcommand_flags(latency)

    # Raw / legacy flags (no subcommand path)
    p.add_argument("--tool", help="tool name for raw call")
    p.add_argument("--args", default="{}", help="JSON args for raw tool call")
    p.add_argument("--list", action="store_true", help="list tools (legacy alias for 'tools')")

    return p


def main() -> int:
    global _VERBOSE
    p = _build_parser()
    args = p.parse_args()
    # Which contract does THIS daemon keep? Asked once, after the URL is
    # known, so one run never mixes the pre- and post-cutover shapes.
    bearer = canary_bearer_for(args.url, "probe", getattr(args, 'timeout', 30.0))
    _VERBOSE = bool(args.verbose)
    url = args.url
    raw = args.raw

    if args.subcommand == "status":
        return _cmd_status(url, raw, bearer)
    if args.subcommand == "universes":
        return _cmd_universes(url, raw, bearer)
    if args.subcommand == "universe":
        return _cmd_universe(url, args.universe_id, raw, bearer)
    if args.subcommand == "wiki":
        return _cmd_wiki(url, raw, bearer)
    if args.subcommand == "tools":
        return _cmd_tools(url, raw, bearer)
    if args.subcommand == "latency":
        return _cmd_latency(url, raw, bearer)

    # Legacy / raw path
    if args.list:
        return _cmd_tools(url, raw, bearer)

    if not args.tool:
        print(
            "use a subcommand (status/universes/universe/wiki/tools/latency) "
            "or --tool <name>",
            file=sys.stderr,
        )
        return 2

    try:
        tool_args = _parse_tool_args(args.args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    sid, rc = _initialize(url, bearer)
    if rc:
        return rc
    return _call_tool(url, sid, args.tool, tool_args, bearer, raw=raw)


if __name__ == "__main__":
    sys.exit(main())
