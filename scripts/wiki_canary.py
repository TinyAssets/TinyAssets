"""Authenticated wiki write-roundtrip canary — Layer-1 extension.

First proves tokenless ``initialize`` receives the canonical Bearer challenge.
Then the canary principal initializes a session, writes the exact reserved
``drafts/notes/uptime-probe.md`` shape, and reads it back with the same bearer.

Exit codes
----------
0  — all probe steps passed.
2  — MCP handshake failed (initialize / session).
6  — anonymous initialize was admitted or the scoped write failed.
7  — wiki read failed or canary draft content mismatch.
99 — unexpected error.

Usage
-----
    python scripts/wiki_canary.py
    python scripts/wiki_canary.py --url https://tinyassets.io/mcp --verbose
    python scripts/wiki_canary.py --once --format=gha   # GHA output mode

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _canary_common import (  # noqa: E402
    _INITIALIZED_NOTIF,
    _init_payload,
    canary_bearer,
    canary_bearer_for,
)
from mcp_public_canary import CanaryError  # noqa: E402
from mcp_public_canary import _post as _status_post  # noqa: E402
from mcp_tool_canary import (  # noqa: E402
    ToolCanaryError,
    _extract_structured_tool_payload,
    _extract_tool_text,
    _post,
)

#: "Not specified" -- distinct from an explicit ``None``, which means "this
#: daemon is pre-cutover, send no bearer". Omitting the argument reads the
#: configured token WITHOUT a network call, so a direct caller (and every unit
#: test that drives a helper) behaves as it always did.
_FROM_ENV: Any = object()

from uptime_canary import _append_log, _now_local_iso  # noqa: E402

DEFAULT_URL = "https://tinyassets.io/mcp"
DEFAULT_TIMEOUT = 20.0

_CANARY_FILENAME = "uptime-probe"
# `notes` is in _WIKI_CATEGORIES on the server (tinyassets/universe_server.py
# `_WIKI_CATEGORIES`); `canary` is not. The previous value silently failed
# the server's category validation, masking real wiki-write breakage.
_CANARY_CATEGORY = "notes"
# ASCII-only content. Server's JSON response wraps the read body with
# `json.dumps`, which (default ensure_ascii=True) escapes non-ASCII
# characters like em-dash to \uNNNN sequences. A substring check on the
# raw response text would then fail. Keep the canary content ASCII so
# the roundtrip check stays a simple substring match.
_CANARY_CONTENT = "TinyAssets wiki uptime canary - automated write-roundtrip probe."
_CANARY_RELATIVE_PATH = "drafts/notes/uptime-probe.md"
_INIT_PAYLOAD = _init_payload("wiki-canary")


def _wiki_write_payload(
    call_id: int,
    *,
    filename: str = _CANARY_FILENAME,
    content: str = _CANARY_CONTENT,
) -> dict:
    # Canonical `write_page` full-page write (no old_text/new_text, no kind)
    # This exact shape is the canary principal's only allowed write.
    return {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {
            "name": "write_page",
            "arguments": {
                "filename": filename,
                "category": _CANARY_CATEGORY,
                "content": content,
                "dry_run": False,
            },
        },
    }


def _wiki_read_payload(call_id: int, *, filename: str = _CANARY_FILENAME) -> dict:
    # Canonical `read_page` takes a single `page=` arg (the slug);
    # _resolve_page locates it across pages/ + drafts/ subdirectories. No
    # `category` / `slug` kwargs — that mismatch was the 2026-04-26 canary
    # RED root cause.
    return {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {
            "name": "read_page",
            "arguments": {
                "page": filename,
            },
        },
    }


def _format_green(ts: str, url: str, rtt_ms: int) -> str:
    return f"{ts} GREEN layer=wiki url={url} surface=wiki_gate rtt_ms={rtt_ms}"


def _format_red(ts: str, url: str, exit_code: int, reason: str, rtt_ms: int) -> str:
    reason_oneline = reason.replace("\n", " ").replace("\r", " ")
    return (
        f"{ts} RED   layer=wiki url={url} exit={exit_code} "
        f"surface=wiki_gate rtt_ms={rtt_ms} reason={reason_oneline!r}"
    )


def _emit_gha_kv(key: str, value: str) -> None:
    if "\n" in value or "\r" in value:
        delimiter = f"EOF_{uuid.uuid4().hex}"
        print(f"{key}<<{delimiter}")
        print(value)
        print(delimiter)
    else:
        print(f"{key}={value}")


def _fresh_canary_content() -> str:
    """Return a unique marker so each credentialed read proves this write."""
    return f"{_CANARY_CONTENT} run={uuid.uuid4().hex}"


def _validate_write_response(resp: dict | None) -> None:
    """Require a successful write response for the exact reserved path."""
    if resp is None or "result" not in resp:
        raise ToolCanaryError(6, f"wiki write returned no result: {resp!r}")
    result = resp["result"]
    if result.get("isError"):
        text = _extract_tool_text(result)[:300]
        raise ToolCanaryError(6, f"wiki write isError=true: {text!r}")
    obj = _extract_structured_tool_payload(result)
    if obj is None:
        text = _extract_tool_text(result)
        if not text:
            raise ToolCanaryError(6, f"wiki write returned no text content: {result!r}")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolCanaryError(
                6,
                f"wiki write text not JSON: {exc}; preview={text[:200]!r}",
            ) from exc
    if not isinstance(obj, dict):
        raise ToolCanaryError(6, f"wiki write JSON was not an object: {obj!r}")
    if (
        obj.get("path") != _CANARY_RELATIVE_PATH
        or obj.get("status") not in {"drafted", "updated"}
    ):
        raise ToolCanaryError(
            6,
            "wiki write did not confirm the reserved path "
            f"{_CANARY_RELATIVE_PATH!r}: {obj!r}",
        )


def _assert_anonymous_initialize_challenged(
    url: str,
    timeout: float,
    *,
    post_fn,
) -> None:
    try:
        status, headers, body = post_fn(
            url,
            _INIT_PAYLOAD,
            timeout,
            accepted_http_statuses=frozenset({401}),
        )
    except CanaryError as exc:
        if exc.code == 2 and exc.msg.startswith("HTTP "):
            raise ToolCanaryError(
                6, f"surface did not challenge anonymous initialize on {url}: {exc.msg}"
            ) from exc
        raise
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    description = payload.get("error_description") if isinstance(payload, dict) else None
    if (
        status != 401
        or not headers.get("www-authenticate", "").startswith("Bearer ")
        or not isinstance(payload, dict)
        or payload.get("error") != "authentication_required"
        or not isinstance(description, str)
        or not description.strip()
        or set(payload) > {"error", "error_description"}
    ):
        raise ToolCanaryError(
            6,
            f"surface admitted an anonymous initialize on {url}: HTTP {status}",
        )


def run_canary(
    url: str,
    timeout: float,
    *,
    post_fn=None,
    status_post_fn=None,
    verbose: bool = False,
    bearer_token: Any = _FROM_ENV,
    canary_content: str = _CANARY_CONTENT,
) -> None:
    """Challenge anonymous initialize, then run the scoped roundtrip.

    ``post_fn`` is injectable for tests (same signature as ``mcp_tool_canary._post``).
    Raises ``ToolCanaryError`` on any failure with the appropriate exit code.

    The authenticated write and read always target the reserved
    ``uptime-probe`` draft required by the canary allowlist.
    """
    if bearer_token is _FROM_ENV:
        bearer_token = canary_bearer()
    post = post_fn or _post
    status_post = status_post_fn or _status_post
    # The reserved WRITE always carries the scoped token. The pre-cutover
    # daemon honours it for exactly that one shape and refuses it everywhere
    # else, so against that build the handshake and the read-back stay
    # anonymous while the write still lands; against this build the negotiated
    # bearer and the token are the same value (Codex round 2, P0).
    write_bearer = bearer_token if bearer_token is not None else canary_bearer()

    if bearer_token is not None:
        # Only this build has that contract to keep.
        _assert_anonymous_initialize_challenged(
            url, timeout, post_fn=status_post,
        )

    # ---- Step 2: authenticated MCP handshake ------------------------------
    resp, sid = post(
        url, None, _INIT_PAYLOAD, timeout, step_code=2,
        bearer_token=bearer_token,
    )
    if resp is None or "result" not in resp:
        raise ToolCanaryError(2, f"initialize returned no result: {resp!r}")
    if "error" in resp:
        raise ToolCanaryError(2, f"initialize returned MCP error: {resp['error']!r}")
    if not sid:
        raise ToolCanaryError(2, "initialize response did not include mcp-session-id header")
    post(
        url, sid, _INITIALIZED_NOTIF, timeout, step_code=2,
        bearer_token=bearer_token,
    )
    if verbose:
        print(f"[wiki-canary] handshake OK sid={sid!r}")

    # ---- Step 3: scoped write ---------------------------------------------
    write_resp, _ = post(
        url,
        sid,
        _wiki_write_payload(2, content=canary_content),
        timeout,
        step_code=6,
        bearer_token=write_bearer,
    )
    _validate_write_response(write_resp)
    if verbose:
        print("[wiki-canary] scoped write OK - reserved draft confirmed")

    # ---- Step 4: scoped read of the shared canary draft -------------------
    read_resp, _ = post(
        url,
        sid,
        _wiki_read_payload(3, filename=_CANARY_FILENAME),
        timeout,
        step_code=7,
        bearer_token=bearer_token,
    )
    if read_resp is None or "result" not in read_resp:
        raise ToolCanaryError(7, f"wiki read returned no result: {read_resp!r}")
    read_result = read_resp["result"]
    if read_result.get("isError"):
        text = _extract_tool_text(read_result)[:300]
        raise ToolCanaryError(7, f"wiki read isError=true: {text!r}")
    read_obj = _extract_structured_tool_payload(read_result)
    read_path = None
    if read_obj is not None:
        read_path = read_obj.get("path")
        read_text = json.dumps(read_obj, default=str)
    else:
        read_text = _extract_tool_text(read_result)
        if not read_text:
            raise ToolCanaryError(7, f"wiki read returned no text content: {read_result!r}")
        try:
            parsed_read = json.loads(read_text)
        except json.JSONDecodeError:
            parsed_read = None
        if isinstance(parsed_read, dict):
            read_path = parsed_read.get("path")
    if canary_content not in read_text:
        raise ToolCanaryError(
            7,
            f"wiki read mismatch: persisted canary draft content not found. "
            f"preview={read_text[:300]!r}",
        )
    if read_path != _CANARY_RELATIVE_PATH:
        raise ToolCanaryError(
            7,
            "wiki read did not confirm the reserved path "
            f"{_CANARY_RELATIVE_PATH!r}: path={read_path!r}",
        )
    if verbose:
        print("[wiki-canary] wiki read OK — persisted canary draft content confirmed")


def run_probe(
    url: str,
    timeout: float,
    fmt: str = "log",
    *,
    post_fn=None,
    status_post_fn=None,
    verbose: bool = False,
    bearer_token: Any = _FROM_ENV,
) -> int:
    """Run one wiki roundtrip probe. Returns exit code (0=green, nonzero=red)."""
    if bearer_token is _FROM_ENV:
        bearer_token = canary_bearer()
    ts = _now_local_iso()
    start = time.monotonic()
    canary_content = _fresh_canary_content()
    try:
        run_canary(
            url,
            timeout,
            post_fn=post_fn,
            status_post_fn=status_post_fn,
            verbose=verbose,
            bearer_token=bearer_token,
            canary_content=canary_content,
        )
    except ToolCanaryError as exc:
        rtt_ms = int((time.monotonic() - start) * 1000)
        _append_log(_format_red(ts, url, exc.code, exc.msg, rtt_ms))
        if fmt == "gha":
            _emit_gha_kv("status", str(exc.code))
            _emit_gha_kv("msg", exc.msg)
        return exc.code
    except Exception as exc:
        rtt_ms = int((time.monotonic() - start) * 1000)
        msg = f"unexpected: {exc!r}"
        _append_log(_format_red(ts, url, 99, msg, rtt_ms))
        if fmt == "gha":
            _emit_gha_kv("status", "99")
            _emit_gha_kv("msg", msg)
        return 99
    rtt_ms = int((time.monotonic() - start) * 1000)
    _append_log(_format_green(ts, url, rtt_ms))
    if fmt == "gha":
        _emit_gha_kv("status", "0")
        _emit_gha_kv("msg", f"OK wiki write+read {url} rtt_ms={rtt_ms}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Authenticated wiki write-roundtrip uptime canary.",
    )
    ap.add_argument(
        "--url", default=DEFAULT_URL,
        help=f"MCP endpoint URL (default: {DEFAULT_URL})",
    )
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument(
        "--once", action="store_true",
        help="Run a single probe and exit (default behavior; flag is a no-op).",
    )
    ap.add_argument(
        "--format", dest="fmt", choices=["log", "gha"], default="log",
        help="Output format: 'log' (default) or 'gha' ($GITHUB_OUTPUT).",
    )
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    # Which contract does THIS daemon keep? Asked once, after the URL is
    # known, so one run never mixes the pre- and post-cutover shapes.
    bearer = canary_bearer_for(args.url, "wiki-canary", args.timeout)
    return run_probe(
        args.url,
        args.timeout,
        fmt=args.fmt,
        verbose=args.verbose,
        bearer_token=bearer,
    )


if __name__ == "__main__":
    sys.exit(main())
