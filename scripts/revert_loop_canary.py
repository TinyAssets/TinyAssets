"""revert_loop_canary — detect provider-exhaustion revert-loops.

Complements the MCP + tool + last-activity canaries. Those catch dark
daemon / broken tool handler / stalled executor. This canary catches
the "busy-broken" state where the daemon IS making progress but every
scene terminates as REVERT — the 2026-04-23 P0 class (67 reverts on
concordance before host noticed).

Spec: ``docs/architecture.md``.

Signal (per spec Q2): count **terminal REVERT verdicts** in
``get_status.evidence.activity_log_tail`` within a time window. REVERT
is terminal; Draft-FAILED can retry-recover within-scene and would add
noise. Mixing in Draft-FAILED was rejected in the spec.

Two-tier thresholds (per spec Q3):
  WARN:     N_WARN (default 3) REVERTs within T_WARN (10 min) → exit 2.
  CRITICAL: N_CRIT (default 5) REVERTs within T_CRIT (20 min) → exit 3.

Exit codes (per spec Q4):
  0  OK (below WARN threshold).
  2  WARN (page priority=0).
  3  CRITICAL (trigger auto-repair via p0-outage-triage).
  4  Handshake / connectivity failure (distinct from stale/dark for
     diagnostics).
  5  Daemon responded but activity_log_tail absent / unparseable / read
     failed. Code bumped from spec-inferred 3 to 5 to avoid collision with
     CRITICAL.

Env overrides (per spec Q3):
  TINYASSETS_REVERT_CANARY_N          WARN threshold (default 3)
  TINYASSETS_REVERT_CANARY_T_MIN      WARN window minutes (default 10)
  TINYASSETS_REVERT_CANARY_N_CRITICAL CRITICAL threshold (default 5)
  TINYASSETS_REVERT_CANARY_T_CRITICAL CRITICAL window minutes (default 20)

Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
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
DEFAULT_WARN_WINDOW_MIN = 10
DEFAULT_WARN_THRESHOLD = 3
DEFAULT_CRITICAL_WINDOW_MIN = 20
DEFAULT_CRITICAL_THRESHOLD = 5
DEFAULT_TIMEOUT = 20.0

# REVERT-verdict patterns. Per spec Q2, ONLY terminal commit verdicts
# count — Draft:FAILED is explicitly rejected because retry-recovery
# within-scene adds noise. These strings come from the commit pipeline
# (``domains/fantasy_daemon/phases/commit.py``) and the surrounding
# activity-log entries on 2026-04-23.
_REVERT_PATTERNS: tuple[re.Pattern, ...] = (
    # "Commit: score 0.00 -- REVERT" — the prototypical P0 signature.
    re.compile(r"Commit:.*score\s+\d+\.\d+\s*--\s*REVERT", re.IGNORECASE),
    # "Commit: reverting ... - draft provider failed" —
    # the cascade-cause wording from the 2026-04-23 trace.
    re.compile(r"Commit:\s*reverting", re.IGNORECASE),
    # Bare "score 0.00 -- REVERT" (legacy format, no "Commit:" prefix).
    re.compile(r"score\s+0\.0{1,2}\s*--\s*REVERT", re.IGNORECASE),
)

_INIT_PAYLOAD = _init_payload("revert-loop-canary")


class RevertLoopError(Exception):
    """Raised by any step; carries the exit code the caller should exit with."""

    def __init__(self, code: int, msg: str, *, reason: str = "probe_failed") -> None:
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.reason = reason
        self.observation = "unknown" if reason == "legacy_evidence_unavailable" else "red"


# `_post` is the shared HTTP+parse path from `_canary_common`, partially
# applied with this canary's exception constructor + User-Agent.
_post = partial(
    _post_raw,
    error_factory=RevertLoopError,
    user_agent="workflow-revert-loop-canary/1.0",
)


# ─────────────────────────────────────────────────────────────────────────────
# Pure classification (no network) — the core logic under test.
# ─────────────────────────────────────────────────────────────────────────────


def _line_is_revert(line: str) -> bool:
    """Return True when any REVERT-pattern regex matches ``line``."""
    return any(p.search(line) for p in _REVERT_PATTERNS)


_TIMESTAMP_RE = re.compile(
    r"^(?:\[)?"
    r"(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)"
)


def _parse_line_timestamp(line: str) -> _dt.datetime | None:
    """Extract the ISO-8601 timestamp prefix from a log line, if present."""
    m = _TIMESTAMP_RE.match(line.strip())
    if not m:
        return None
    raw = m.group("ts")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def _count_reverts_in_window(
    activity_tail: list[str],
    *,
    now: _dt.datetime,
    window_min: int,
) -> int:
    """Count REVERT lines with timestamps within ``window_min`` of ``now``."""
    cutoff = now - _dt.timedelta(minutes=window_min)
    count = 0
    for raw_line in activity_tail:
        if not isinstance(raw_line, str) or not raw_line.strip():
            continue
        if not _line_is_revert(raw_line):
            continue
        ts = _parse_line_timestamp(raw_line)
        if ts is None:
            # Untimestamped — can't place in window, skip.
            continue
        if ts >= cutoff:
            count += 1
    return count


def classify_loop(
    activity_tail: list[str],
    *,
    now: _dt.datetime,
    warn_window_min: int,
    warn_threshold: int,
    critical_window_min: int,
    critical_threshold: int,
) -> tuple[int, str]:
    """Classify an activity_log_tail against both WARN + CRITICAL thresholds.

    Returns (exit_code, human_message). CRITICAL takes precedence when
    both fire in the same pass. Empty tail is OK: this canary detects
    busy-broken work, and an empty well-formed tail has 0 REVERTs.
    """
    if not activity_tail:
        return 0, "OK: activity_log_tail empty; 0 REVERTs to classify"

    warn_count = _count_reverts_in_window(
        activity_tail, now=now, window_min=warn_window_min,
    )
    critical_count = _count_reverts_in_window(
        activity_tail, now=now, window_min=critical_window_min,
    )

    # CRITICAL first — it's the stricter / longer window.
    if critical_count >= critical_threshold:
        return 3, (
            f"CRITICAL revert-loop: {critical_count} REVERTs in last "
            f"{critical_window_min}min (threshold {critical_threshold}). "
            "Triggering auto-repair via p0-outage-triage."
        )
    if warn_count >= warn_threshold:
        return 2, (
            f"WARN revert-loop: {warn_count} REVERTs in last "
            f"{warn_window_min}min (threshold {warn_threshold}). "
            "Pushover priority=0."
        )
    return 0, (
        f"OK: {warn_count} REVERTs in last {warn_window_min}min "
        f"(warn {warn_threshold}, crit {critical_threshold} in "
        f"{critical_window_min}min)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Network path — `_post` + `_extract_tool_text` come from _canary_common.
# ─────────────────────────────────────────────────────────────────────────────


def fetch_status_activity_tail(
    url: str,
    timeout: float,
    *,
    post_fn=None,
    bearer_token: Any = _FROM_ENV,
) -> list[str]:
    """MCP handshake + tools/call get_status; return activity_log_tail.

    Raises RevertLoopError with step_code=4 on handshake trouble, 5 on
    tool-shape trouble (evidence missing).
    """
    if bearer_token is _FROM_ENV:
        bearer_token = canary_bearer()
    post = post_fn or _post

    resp, sid = post(
        url, None, _INIT_PAYLOAD, timeout, step_code=4,
        bearer_token=bearer_token,
    )
    if resp is None or "result" not in resp:
        raise RevertLoopError(4, f"initialize returned no result: {resp!r}")
    if "error" in resp:
        raise RevertLoopError(
            4, f"initialize returned MCP error: {resp['error']!r}",
        )
    if not sid:
        raise RevertLoopError(
            4, "initialize response missing mcp-session-id header",
        )

    post(
        url, sid, _INITIALIZED_NOTIF, timeout, step_code=4,
        bearer_token=bearer_token,
    )

    call_payload = {
        "jsonrpc": "2.0", "id": 2,
        "method": "tools/call",
        "params": {"name": "get_status", "arguments": {}},
    }
    resp, _ = post(
        url, sid, call_payload, timeout, step_code=5,
        bearer_token=bearer_token,
    )
    if resp is None or "result" not in resp:
        raise RevertLoopError(
            5, f"get_status returned no result: {resp!r}",
        )
    result = resp["result"]
    if result.get("isError"):
        text = _extract_tool_text(result)[:300]
        raise RevertLoopError(5, f"get_status isError=true: {text!r}")
    payload = _extract_structured_tool_payload(result)
    if payload is None:
        text = _extract_tool_text(result)
        if not text:
            raise RevertLoopError(
                5, f"get_status returned no text content: {result!r}",
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RevertLoopError(
                5, f"get_status text not JSON: {exc}; preview={text[:200]!r}",
            ) from exc

    if not isinstance(payload, dict):
        raise RevertLoopError(5, "get_status payload is not an object")
    caveats = payload.get("evidence_caveats")
    if isinstance(caveats, dict):
        tail_caveats = caveats.get("activity_log_tail", [])
        if isinstance(tail_caveats, list):
            read_failed = any(
                isinstance(caveat, str) and "read failed" in caveat.lower()
                for caveat in tail_caveats
            )
            if read_failed:
                raise RevertLoopError(
                    5,
                    "get_status reports activity_log_tail read failure: "
                    f"{tail_caveats!r}",
                )

    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        # Recognize the existing confined no-home projection positively. Empty,
        # denied, malformed or drifted status is still a failed probe, not an
        # excuse to downgrade a broken endpoint to missing legacy coverage.
        first_contact = payload.get("first_contact")
        daemon = payload.get("daemon")
        confined = (
            "evidence" not in payload
            and isinstance(first_contact, dict)
            and first_contact.get("event") == "no_universe_yet"
            and isinstance(daemon, dict)
            and isinstance(daemon.get("worker_liveness"), dict)
            and "release_state" in payload
        )
        raise RevertLoopError(
            5,
            f"get_status payload has no evidence block; top-level keys: "
            f"{sorted(payload.keys())}",
            reason="legacy_evidence_unavailable" if confined else "probe_failed",
        )
    tail = evidence.get("activity_log_tail")
    if not isinstance(tail, list):
        raise RevertLoopError(
            5,
            f"get_status evidence.activity_log_tail not a list: {type(tail).__name__}",
        )
    return tail


def run_canary(
    url: str,
    timeout: float,
    *,
    warn_window_min: int,
    warn_threshold: int,
    critical_window_min: int,
    critical_threshold: int,
    post_fn=None,
    now: _dt.datetime | None = None,
    bearer_token: Any = _FROM_ENV,
) -> tuple[int, str]:
    """Full canary flow. Returns (exit_code, human_message)."""
    if bearer_token is _FROM_ENV:
        bearer_token = canary_bearer()
    current_now = now or _dt.datetime.now(tz=_dt.timezone.utc)
    tail = fetch_status_activity_tail(
        url, timeout, post_fn=post_fn, bearer_token=bearer_token,
    )
    return classify_loop(
        tail, now=current_now,
        warn_window_min=warn_window_min,
        warn_threshold=warn_threshold,
        critical_window_min=critical_window_min,
        critical_threshold=critical_threshold,
    )


def _emit_observation(observation: str, reason: str, fmt: str) -> None:
    if fmt != "gha":
        return
    result = f"revert_observation={observation}\nrevert_reason={reason}\n"
    # These values are producer-owned enums, never endpoint-controlled text.
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as stream:
            stream.write(result)
    print(result, end="")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Two-tier revert-loop canary per Lane 4 spec.",
    )
    ap.add_argument("--url", default=DEFAULT_URL,
                    help=f"MCP endpoint URL (default: {DEFAULT_URL})")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help=f"Per-request timeout seconds (default: {DEFAULT_TIMEOUT})")

    default_warn_window = int(os.environ.get(
        "TINYASSETS_REVERT_CANARY_T_MIN", DEFAULT_WARN_WINDOW_MIN,
    ))
    default_warn_threshold = int(os.environ.get(
        "TINYASSETS_REVERT_CANARY_N", DEFAULT_WARN_THRESHOLD,
    ))
    default_critical_window = int(os.environ.get(
        "TINYASSETS_REVERT_CANARY_T_CRITICAL", DEFAULT_CRITICAL_WINDOW_MIN,
    ))
    default_critical_threshold = int(os.environ.get(
        "TINYASSETS_REVERT_CANARY_N_CRITICAL", DEFAULT_CRITICAL_THRESHOLD,
    ))

    ap.add_argument(
        "--warn-window-min", type=int, default=default_warn_window,
        help=f"WARN window in minutes (default: {default_warn_window}; "
             "env: TINYASSETS_REVERT_CANARY_T_MIN)",
    )
    ap.add_argument(
        "--warn-threshold", type=int, default=default_warn_threshold,
        help=f"WARN REVERT count threshold (default: {default_warn_threshold}; "
             "env: TINYASSETS_REVERT_CANARY_N)",
    )
    ap.add_argument(
        "--critical-window-min", type=int, default=default_critical_window,
        help=f"CRITICAL window in minutes (default: {default_critical_window}; "
             "env: TINYASSETS_REVERT_CANARY_T_CRITICAL)",
    )
    ap.add_argument(
        "--critical-threshold", type=int, default=default_critical_threshold,
        help=f"CRITICAL REVERT count threshold (default: {default_critical_threshold}; "
             "env: TINYASSETS_REVERT_CANARY_N_CRITICAL)",
    )
    ap.add_argument("--verbose", action="store_true",
                    help="Echo the canary classification summary.")
    ap.add_argument("--format", choices=("text", "gha"), default="text")
    args = ap.parse_args(argv)
    # Which contract does THIS daemon keep? Asked once, after the URL is
    # known, so one run never mixes the pre- and post-cutover shapes.
    bearer = canary_bearer_for(args.url, "revert-loop", args.timeout)

    try:
        code, msg = run_canary(
            args.url, args.timeout,
            warn_window_min=args.warn_window_min,
            warn_threshold=args.warn_threshold,
            critical_window_min=args.critical_window_min,
            critical_threshold=args.critical_threshold,
            bearer_token=bearer,
        )
    except RevertLoopError as exc:
        _emit_observation(exc.observation, exc.reason, args.format)
        print(
            f"[revert-loop] FAIL (exit {exc.code}): {exc.msg}",
            file=sys.stderr,
        )
        return exc.code

    _emit_observation("green" if code == 0 else "red", "observed", args.format)
    if code == 0:
        print(f"[revert-loop] {msg}")
    else:
        print(f"[revert-loop] FAIL (exit {code}): {msg}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
