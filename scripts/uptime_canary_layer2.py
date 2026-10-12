"""Uptime Canary Layer 2 — Claude.ai connector liveness probe.

Per ``openspec/specs/uptime-and-alarms/spec.md``.

Sends a single probe message to Claude.ai via the ``uptime_canary`` persona,
parses the chatbot response for evidence that the MCP connector was invoked,
and logs one line to ``.agents/uptime.log`` in the Layer-1-compatible format
(same grammar, ``layer=2`` tag + extended exit codes).

Exit-code table (§2.3 + §2.6 amendment 2026-04-20)
---------------------------------------------------
- 0  = GREEN       — tool called, field matched, settle ≤ 150 s
- 8  = SOFT_YELLOW — tool called, field matched, settle > 150 s
                     (fabrication-mode suspected; process exits 0, log only)
- 10 = RED         — tool not invoked
- 12 = RED         — tool invoked but response doesn't match expected field
- 13 = RED         — browser couldn't load Claude.ai
- 14 = SKIP        — browser lock unavailable (missed probe, not failure)
- 15 = RED         — persona auth expired / login loop
- 99 = RED         — unexpected failure

Invocation
----------
Windows Task Scheduler entry ``TinyAssets-Canary-L2`` invokes this hourly.
Exit code is 0 for GREEN and SOFT_YELLOW (soft signal, not a failure),
nonzero for RED or unexpected errors.  Exit 14 (SKIP) is also 0 from the
scheduler's perspective — a missed probe is not a failure.

Stdlib-only.  Browser interaction is injected via ``_probe_fn`` so tests
can exercise all paths without a real browser.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import browser_lock  # noqa: E402
from uptime_canary import _append_log, _now_local_iso  # noqa: E402

# Probe configuration.
PROBE_URL = "https://claude.ai/new"
PROBE_MESSAGE = "Are you there? Call get_status and tell me the llm_endpoint_bound value."
TOOL_NAME = "get_status"
# Fields to look for in the chatbot response (case-insensitive, any match = green).
FIELD_MATCHES = ("llm_endpoint_bound", "endpoint", "bound")
# Fabrication-mode soft-threshold from §2.6 amendment.
FABRICATION_THRESHOLD_MS = 150_000
# Browser lock owner name for Layer-2 probes.
LOCK_OWNER = "uptime-canary-l2"


# ---------------------------------------------------------------------------
# Log-line formatters
# ---------------------------------------------------------------------------

def _format_green(ts: str, url: str, rtt_ms: int, tool_called: str) -> str:
    return f"{ts} GREEN layer=2 url={url} rtt_ms={rtt_ms} tool_called={tool_called}"


def _format_soft_yellow(ts: str, url: str, rtt_ms: int, reason: str) -> str:
    return (
        f"{ts} SOFT_YELLOW layer=2 url={url} exit=8 rtt_ms={rtt_ms} "
        f"tool_called={TOOL_NAME} reason={reason!r}"
    )


def _format_red(ts: str, url: str, exit_code: int, rtt_ms: int, reason: str) -> str:
    reason_oneline = reason.replace("\n", " ").replace("\r", " ")
    return (
        f"{ts} RED   layer=2 url={url} exit={exit_code} "
        f"rtt_ms={rtt_ms} reason={reason_oneline!r}"
    )


def _format_skip(ts: str, url: str, reason: str) -> str:
    return f"{ts} SKIP  layer=2 url={url} exit=14 reason={reason!r}"


# ---------------------------------------------------------------------------
# Response parsing helpers
# ---------------------------------------------------------------------------

def _tool_was_called(response: str) -> bool:
    """Return True if the response contains evidence that TOOL_NAME was invoked."""
    return TOOL_NAME in response


def _field_matched(response: str) -> bool:
    """Return True if any of FIELD_MATCHES appear in the response (case-insensitive)."""
    lower = response.lower()
    return any(f in lower for f in FIELD_MATCHES)


# ---------------------------------------------------------------------------
# Core probe runner
# ---------------------------------------------------------------------------

def run_probe(
    url: str = PROBE_URL,
    *,
    _probe_fn=None,
) -> int:
    """Acquire browser lock, run the Layer-2 probe, release lock, log result.

    ``_probe_fn`` is injectable for tests.  Signature::

        def probe_fn(url: str, message: str) -> tuple[str, int]:
            '''Returns (response_text, settle_ms).  Raises on hard error.'''

    Returns the exit code (0 for GREEN/SOFT_YELLOW/SKIP, nonzero for RED/unexpected).
    """
    ts = _now_local_iso()
    start = time.monotonic()

    # ---- Browser lock acquisition ----------------------------------------
    acquired = browser_lock.acquire(LOCK_OWNER, "uptime-canary-l2")
    if not acquired:
        holder = (browser_lock.read() or {}).get("owner", "unknown")
        _append_log(_format_skip(ts, url, f"browser_lock_held_by_{holder}"))
        return 0  # SKIP is not a failure

    try:
        return _run_probe_locked(url, ts, start, _probe_fn=_probe_fn)
    finally:
        browser_lock.release(LOCK_OWNER)


def _run_probe_locked(url: str, ts: str, start: float, *, _probe_fn=None) -> int:
    """Inner probe logic — called only when the browser lock is held."""
    if _probe_fn is None:
        _probe_fn = _real_browser_probe

    try:
        response, rtt_ms = _probe_fn(url, PROBE_MESSAGE)
    except _BrowserLoadError as exc:
        rtt_ms = int((time.monotonic() - start) * 1000)
        _append_log(_format_red(ts, url, 13, rtt_ms, str(exc)))
        return 13
    except _AuthExpiredError as exc:
        rtt_ms = int((time.monotonic() - start) * 1000)
        _append_log(_format_red(ts, url, 15, rtt_ms, str(exc)))
        return 15
    except Exception as exc:  # defensive — canary must never crash silently
        rtt_ms = int((time.monotonic() - start) * 1000)
        _append_log(_format_red(ts, url, 99, rtt_ms, f"unexpected: {exc!r}"))
        return 99

    if not _tool_was_called(response):
        _append_log(_format_red(ts, url, 10, rtt_ms, "tool_not_invoked"))
        return 10

    if not _field_matched(response):
        _append_log(_format_red(ts, url, 12, rtt_ms, "field_not_matched"))
        return 12

    # Green criteria met — check fabrication-mode soft-threshold.
    if rtt_ms > FABRICATION_THRESHOLD_MS:
        _append_log(_format_soft_yellow(ts, url, rtt_ms, "settle_time_exceeded_150s"))
        return 0  # SOFT_YELLOW exits 0; it's a soft log-only signal

    _append_log(_format_green(ts, url, rtt_ms, TOOL_NAME))
    return 0


# ---------------------------------------------------------------------------
# Sentinel exceptions for browser errors (injectable in tests)
# ---------------------------------------------------------------------------

class _BrowserLoadError(Exception):
    """Browser could not load Claude.ai (exit=13)."""


class _AuthExpiredError(Exception):
    """Persona auth expired / login loop detected (exit=15)."""


# Public aliases for tests to raise.
BrowserLoadError = _BrowserLoadError
AuthExpiredError = _AuthExpiredError


# ---------------------------------------------------------------------------
# Real browser probe (invoked in production; not exercised by unit tests)
# ---------------------------------------------------------------------------

# Subprocess timeout for the Claude.ai chat round-trip. Layer-2 SOFT_YELLOW
# fires at 150s; allow extra for navigation + dialog-dismiss + tool roundtrip.
_PROBE_SUBPROCESS_TIMEOUT_S = 240


def _read_last_assistant_block(trace_path: Path) -> str | None:
    """Return the body of the most recent CLAUDE -> USER_SIM block, or None.

    The trace file uses ``## [<ts>] CLAUDE -> USER_SIM`` headers per
    `claude_chat._append_trace`. We scan from the end for the last such
    header and return everything below it (until the next ``## `` header
    or EOF).
    """
    if not trace_path.exists():
        return None
    text = trace_path.read_text(encoding="utf-8", errors="replace")
    marker = "] CLAUDE -> USER_SIM\n"
    idx = text.rfind(marker)
    if idx == -1:
        return None
    body_start = idx + len(marker)
    next_header = text.find("\n## ", body_start)
    body = text[body_start:next_header] if next_header != -1 else text[body_start:]
    return body.strip() or None


def _real_browser_probe(url: str, message: str) -> tuple[str, int]:  # pragma: no cover
    """Send ``message`` via the live claude_chat.py subprocess, return response + ms.

    The canary doesn't navigate explicitly — ``claude_chat ask`` handles
    page-state via ``_ensure_claude_page``. The probe URL is informational
    (logged in green/red lines). The actual chat round-trip uses the same
    subprocess entry point user-sim uses, so this exercises the canonical
    production path.

    Failure-mode mapping:
      - subprocess exit != 0     → _BrowserLoadError (covers playwright-not-installed,
                                   CDP unreachable, no chat input found, dialog stuck).
      - subprocess timeout       → _BrowserLoadError (claude.ai unresponsive).
      - exit 0 but no assistant trace block found → _BrowserLoadError (probe completed
        but trace missing — usually a claude_chat write-failure or stale trace path).

    Auth-expired (exit=15) is a hypothetical future signal — claude_chat.py does NOT
    currently surface auth-loop detection, so we cannot raise _AuthExpiredError yet.
    Add a stderr-pattern match or dedicated exit code to claude_chat.py + branch here
    when that signal becomes available.
    """
    import subprocess

    chat_script = _REPO_ROOT / "scripts" / "claude_chat.py"
    start = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, str(chat_script), "ask", message],
            capture_output=True,
            text=True,
            timeout=_PROBE_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise _BrowserLoadError(
            f"claude_chat ask timed out after {_PROBE_SUBPROCESS_TIMEOUT_S}s"
        ) from exc
    except OSError as exc:  # missing python, permissions, etc.
        raise _BrowserLoadError(f"claude_chat subprocess failed to launch: {exc}") from exc

    rtt_ms = int((time.monotonic() - start) * 1000)

    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip().splitlines()[-3:]
        reason = "; ".join(stderr_tail) or f"exit={result.returncode}"
        raise _BrowserLoadError(f"claude_chat exit={result.returncode}: {reason}")

    trace_path = _REPO_ROOT / "output" / "claude_chat_trace.md"
    response = _read_last_assistant_block(trace_path)
    if response is None:
        raise _BrowserLoadError(
            f"claude_chat exit=0 but no assistant block in {trace_path}"
        )
    return response, rtt_ms


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Layer-2 uptime canary.")
    ap.add_argument("--url", default=PROBE_URL)
    ap.add_argument(
        "--once",
        action="store_true",
        help="Run a single probe and exit (default behavior; flag is a no-op).",
    )
    args = ap.parse_args(argv)
    return run_probe(args.url)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
