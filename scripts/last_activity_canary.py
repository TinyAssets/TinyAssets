"""last_activity_canary — prove node execution is 24/7, not just MCP surface.

Complements ``mcp_public_canary`` (handshake) and ``mcp_tool_canary``
(tools/list + status read). Those catch "daemon is dark" and "tool
handler crashed" but NEITHER catches "MCP is green but node execution
is stalled" — which is exactly the state we saw live on 2026-04-22
before Task #14 landed the cloud-side worker.

This canary asserts the daemon has done *actual work* within N minutes
by reading ``daemon.last_activity_at`` from ``get_status`` and comparing
against ``now - threshold``.

Why ``get_status``
------------------
The canary principal cannot read a universe. ``get_status`` exposes the latest
run-ledger progress time across the platform without granting universe access,
so liveness remains observable inside the canary's narrow allowlist.

Exit codes (task #15 spec)
--------------------------
  0  last_activity_at is within the threshold (FRESH)
  2  last_activity_at exceeds the threshold (STALE)
  3  daemon responded but no parseable last_activity_at field
     (tool returned an unexpected shape, or field is null/malformed)
  4  handshake / connectivity failure (overlaps with mcp_public_canary
     — this canary doesn't duplicate that path, just fails loudly
     with a distinct code so operators can tell stale-execution from
     dark-daemon at a glance)

Grace handling
--------------
Per task spec: daemon may legitimately be idle (no pending work).
Decision — page anyway. Rationale: "true 24/7 independence means
'always working on something' once there's work to do." If the queue
is empty, that's a product signal (nothing to do) that's still worth
noticing because the activity scheduler is supposed to be keeping daemons
fed. A persistent stale-but-queue-empty state IS a pageable condition
because it means the paid market isn't working either. Coupling to
``pending_requests > 0`` is flagged as a follow-up if the simpler
decision is noisy in prod.

Usage
-----
    python scripts/last_activity_canary.py
    python scripts/last_activity_canary.py --url http://127.0.0.1:8001/mcp
    python scripts/last_activity_canary.py --threshold-min 60 --verbose

Env overrides
-------------
    TINYASSETS_LAST_ACTIVITY_THRESHOLD_MIN   default threshold (min)

Stdlib only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
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
DEFAULT_THRESHOLD_MIN = 30
DEFAULT_TIMEOUT = 20.0

_INIT_PAYLOAD = _init_payload("last-activity-canary")


class LastActivityError(Exception):
    """Raised by any step; carries the exit code so main() can exit with it."""

    def __init__(self, code: int, msg: str) -> None:
        super().__init__(msg)
        self.code = code
        self.msg = msg


# `_post` is the shared HTTP+parse path from `_canary_common`, partially
# applied with this canary's exception constructor + User-Agent.
_post = partial(
    _post_raw,
    error_factory=LastActivityError,
    user_agent="workflow-last-activity-canary/1.0",
)


def _parse_iso(value: str) -> _dt.datetime | None:
    """Parse an ISO-8601 timestamp, returning timezone-aware UTC datetime."""
    if not value:
        return None
    try:
        # Accept both "Z" suffix and "+00:00" style.
        text = value.replace("Z", "+00:00") if value.endswith("Z") else value
        parsed = _dt.datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc)


def classify_freshness(
    last_activity_iso: str | None,
    now: _dt.datetime,
    threshold_min: int,
) -> tuple[int, str]:
    """Pure classification of last_activity_at against threshold.

    Returns (exit_code, human_message).
      - 0 when the timestamp is within the threshold.
      - 2 when stale.
      - 3 when unparseable / missing.

    Injection seam for tests — no network or subprocess.
    """
    if not last_activity_iso:
        return 3, "daemon returned null/empty last_activity_at"
    ts = _parse_iso(last_activity_iso)
    if ts is None:
        return 3, f"could not parse last_activity_at={last_activity_iso!r}"
    age = now - ts
    age_min = age.total_seconds() / 60.0
    if age_min <= threshold_min:
        return 0, (
            f"FRESH: last_activity_at={last_activity_iso} "
            f"age={age_min:.1f}min threshold={threshold_min}min"
        )
    return 2, (
        f"STALE: last_activity_at={last_activity_iso} "
        f"age={age_min:.1f}min > threshold={threshold_min}min"
    )


def fetch_inspect_result(
    url: str,
    timeout: float,
    *,
    post_fn=None,
    bearer_token: Any = _FROM_ENV,
) -> dict[str, Any]:
    """Full MCP handshake + get_status call.

    Returns the parsed status result as a dict. ``post_fn`` is an injection
    seam for tests.

    Raises LastActivityError on any failure — handshake failures get
    step_code=4, tool failures get step_code=3.
    """
    if bearer_token is _FROM_ENV:
        bearer_token = canary_bearer()
    post = post_fn or _post

    # Step 1: initialize. Any failure => exit 4 (handshake).
    resp, sid = post(
        url, None, _INIT_PAYLOAD, timeout, step_code=4,
        bearer_token=bearer_token,
    )
    if resp is None or "result" not in resp:
        raise LastActivityError(
            4, f"initialize returned no result: {resp!r}",
        )
    if "error" in resp:
        raise LastActivityError(
            4, f"initialize returned MCP error: {resp['error']!r}",
        )
    if not sid:
        raise LastActivityError(
            4, "initialize response missing mcp-session-id header",
        )

    # Notifications/initialized. Protocol-required but response-less.
    post(
        url, sid, _INITIALIZED_NOTIF, timeout, step_code=4,
        bearer_token=bearer_token,
    )

    # Step 2: get_status is the allowed platform-wide activity source.
    call_payload = {
        "jsonrpc": "2.0", "id": 2,
        "method": "tools/call",
        "params": {"name": "get_status", "arguments": {}},
    }
    resp, _ = post(
        url, sid, call_payload, timeout, step_code=3,
        bearer_token=bearer_token,
    )
    if resp is None or "result" not in resp:
        raise LastActivityError(
            3, f"get_status returned no result: {resp!r}",
        )
    result = resp["result"]
    if result.get("isError"):
        text = _extract_tool_text(result)[:300]
        raise LastActivityError(
            3, f"get_status isError=true: {text!r}",
        )
    structured = _extract_structured_tool_payload(result)
    if structured is not None:
        return structured
    text = _extract_tool_text(result)
    if not text:
        raise LastActivityError(
            3, f"get_status returned no text content: {result!r}",
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LastActivityError(
            3, f"get_status text not JSON: {exc}; preview={text[:200]!r}",
        ) from exc
    if not isinstance(payload, dict):
        raise LastActivityError(
            3, f"get_status payload is not an object: {type(payload).__name__}",
        )
    return payload


def run_canary(
    url: str,
    timeout: float,
    threshold_min: int,
    *,
    post_fn=None,
    now: _dt.datetime | None = None,
    verbose: bool = False,
    bearer_token: Any = _FROM_ENV,
) -> tuple[int, str]:
    """Full canary flow. Returns (exit_code, human_message).

    ``now`` and ``post_fn`` are test seams — default to real time + real
    HTTP when unset.

    The status surface intentionally exposes only the platform-wide timestamp;
    universe phase and pause state are outside this principal's authority.
    """
    if bearer_token is _FROM_ENV:
        bearer_token = canary_bearer()
    current_now = now or _dt.datetime.now(tz=_dt.timezone.utc)
    inspect = fetch_inspect_result(
        url, timeout, post_fn=post_fn, bearer_token=bearer_token,
    )

    daemon = inspect.get("daemon")
    if not isinstance(daemon, dict):
        return 3, (
            "get_status result has no daemon block; "
            f"top-level keys: {sorted(inspect.keys())}"
        )

    if verbose:
        print(
            f"[last-activity] last_activity_at={daemon.get('last_activity_at')!r}"
        )

    # The supervisor heartbeat distinguishes the two states content-activity
    # mtimes conflate. A dead or stale beat is a WEDGE -- page with that as the
    # reason instead of the vaguer "activity stale". A live beat with no queued
    # work is healthy idleness. A live beat with work present falls through to
    # the freshness gate, because the daemon should be making progress and
    # last_activity measures exactly that.
    #
    # This lived here before the probe moved from `read_graph target=graph` to
    # `get_status`, and moving it carried `last_activity_at` across while
    # leaving this behind -- so on a quiet platform a wedged worker read as
    # healthy, which is the one state this canary exists to catch. `get_status`
    # carries a platform-wide summary now (the WORST worker on the daemon).
    liveness = daemon.get("worker_liveness")
    if isinstance(liveness, dict) and liveness.get("present"):
        alive = liveness.get("alive")
        if alive is False:
            return 2, (
                "STALE (worker_wedged): supervisor heartbeat is "
                f"{liveness.get('beat_age_s')}s old "
                f"(phase={liveness.get('phase')!r}, "
                f"consec_crashes={liveness.get('consec_crashes')}); "
                "inspect the active daemon coordinator; historical worker "
                "files do not authorize restarting a retired worker"
            )
        if alive is not True:
            return 3, "current daemon coordinator liveness is unavailable"
        if alive is True and not daemon.get("has_work", False):
            return 0, (
                "FRESH (worker alive, no active work): supervisor beat "
                f"{liveness.get('beat_age_s')}s old; "
                f"last_activity_at={daemon.get('last_activity_at')!r} "
                "staleness reflects idleness, not a wedge"
            )

    last_activity_iso = daemon.get("last_activity_at")
    return classify_freshness(last_activity_iso, current_now, threshold_min)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Probe daemon.last_activity_at for 24/7 node-execution liveness.",
    )
    ap.add_argument("--url", default=DEFAULT_URL,
                    help=f"MCP endpoint URL (default: {DEFAULT_URL})")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help=f"Per-request timeout seconds (default: {DEFAULT_TIMEOUT})")
    default_threshold = int(os.environ.get(
        "TINYASSETS_LAST_ACTIVITY_THRESHOLD_MIN", DEFAULT_THRESHOLD_MIN,
    ))
    ap.add_argument("--threshold-min", type=int, default=default_threshold,
                    help=f"Stale threshold in minutes (default: {default_threshold}; "
                         f"env: TINYASSETS_LAST_ACTIVITY_THRESHOLD_MIN)")
    ap.add_argument("--verbose", action="store_true",
                    help="Print one-line summary + diagnostic fields.")
    args = ap.parse_args(argv)
    # Which contract does THIS daemon keep? Asked once, after the URL is
    # known, so one run never mixes the pre- and post-cutover shapes.
    bearer = canary_bearer_for(args.url, "last-activity", args.timeout)

    try:
        code, msg = run_canary(
            args.url, args.timeout, args.threshold_min,
            verbose=args.verbose,
            bearer_token=bearer,
        )
    except LastActivityError as exc:
        print(f"[last-activity] FAIL (exit {exc.code}): {exc.msg}",
              file=sys.stderr)
        return exc.code

    # 0/2/3 outcomes all print the human message; exit code carries the
    # RED/GREEN decision up to the tinyassets.
    if code == 0:
        print(f"[last-activity] {msg}")
    else:
        print(f"[last-activity] FAIL (exit {code}): {msg}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
