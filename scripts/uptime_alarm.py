"""Uptime Alarm — tail ``.agents/uptime.log``, emit alarms on sustained red.

Per ``openspec/specs/uptime-and-alarms/spec.md`` §3
escalation table. Design-note prescribes writing to ``STATUS.md``
Concerns; lead overrode to ``.agents/uptime_alarms.log`` because STATUS.md
is host-managed (memory ``feedback_status_md_host_managed``). Lead
surfaces alarms from the dedicated log to host.

Escalation (Layer 1)
--------------------
- 0-1 recent red    → no alarm.
- 2 consecutive red → write one alarm line, dedupe by kind+url+exit.
- 5+ consecutive red → update the existing line's duration, do not append.
- First green after red → append a RECOVERED line and reset the dedupe
  marker, so the next red re-alarms.

Invocation
----------
Windows Task Scheduler entry ``TinyAssets-Alarm`` runs this every 2 min,
decoupled from the probe job (design-note §5). Decoupling means the
alarm still fires even if the probe job lands slightly out-of-cadence.

Stdlib-only.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
UPTIME_LOG = _REPO_ROOT / ".agents" / "uptime.log"
ALARM_LOG = _REPO_ROOT / ".agents" / "uptime_alarms.log"
# Persists the last-emitted alarm fingerprint + state across invocations so
# we can dedupe and detect recovery. Sibling to the alarm log for easy
# cleanup.
ALARM_STATE = _REPO_ROOT / ".agents" / ".uptime_alarm_state.json"

# Minimum consecutive reds before we alarm. Design-note §3.
ALARM_THRESHOLD = 2
# Minimum consecutive SOFT_YELLOW lines before writing a fabrication-mode
# breadcrumb. §2.6 amendment 2026-04-20.
SOFT_YELLOW_THRESHOLD = 2
# Scan window — don't scan more than this many lines; protects against
# runaway log file state.
TAIL_LINES = 200


def _now_local_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _read_tail(path: Path, n: int) -> list[str]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fp:
            lines = fp.readlines()
    except OSError:
        return []
    return [line.rstrip("\n") for line in lines[-n:]]


def _parse_line(line: str) -> dict[str, str] | None:
    """Parse ``<ts> GREEN|RED|SOFT_YELLOW|SKIP layer=N url=... [exit=N] [reason=...]``.

    Returns a dict with keys status, url, exit (when RED/SOFT_YELLOW). None if
    the line doesn't match the canary format.
    """
    parts = line.split()
    if len(parts) < 3:
        return None
    if parts[1] not in ("GREEN", "RED", "SOFT_YELLOW", "SKIP"):
        return None
    out: dict[str, str] = {"ts": parts[0], "status": parts[1]}
    for token in parts[2:]:
        if "=" in token:
            k, _, v = token.partition("=")
            out[k] = v
    return out


def _load_state() -> dict[str, object]:
    if not ALARM_STATE.is_file():
        return {}
    try:
        return json.loads(ALARM_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict[str, object]) -> None:
    ALARM_STATE.parent.mkdir(parents=True, exist_ok=True)
    try:
        ALARM_STATE.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    except OSError:
        pass  # best-effort; next run re-derives from log tail


def _is_gha() -> bool:
    """True when running inside a GitHub Actions runner (GITHUB_ACTIONS=true)."""
    import os
    return os.environ.get("GITHUB_ACTIONS", "").lower() in ("true", "1")


def _append_alarm(line: str) -> None:
    ALARM_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ALARM_LOG.open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")
    # Mirror to stdout in GHA so the alarm line appears in workflow output.
    if _is_gha():
        print(f"[uptime-alarm] {line}", flush=True)


def _count_trailing_reds(parsed: list[dict[str, str]]) -> int:
    count = 0
    for entry in reversed(parsed):
        if entry.get("status") == "RED":
            count += 1
        else:
            break
    return count


def _count_trailing_soft_yellows(parsed: list[dict[str, str]]) -> int:
    """Count consecutive SOFT_YELLOW lines at the tail of parsed entries."""
    count = 0
    for entry in reversed(parsed):
        if entry.get("status") == "SOFT_YELLOW":
            count += 1
        else:
            break
    return count


def _first_green_after_last_red(parsed: list[dict[str, str]]) -> dict[str, str] | None:
    """Return the first GREEN entry after the latest RED in the scanned tail."""
    last_red_index = -1
    for index, entry in enumerate(parsed):
        if entry.get("status") == "RED":
            last_red_index = index

    for entry in parsed[last_red_index + 1:]:
        if entry.get("status") == "GREEN":
            return entry
    return None


def evaluate() -> int:
    """Walk the uptime log, emit alarms per the escalation table.

    Returns the number of alarm lines written (0 = nothing new).
    """
    raw = _read_tail(UPTIME_LOG, TAIL_LINES)
    parsed = [p for p in (_parse_line(line) for line in raw) if p]
    if not parsed:
        return 0

    state = _load_state()
    last_alarm_fingerprint = state.get("last_alarm_fingerprint")
    last_alarm_status = state.get("last_alarm_status")  # "red" | "recovered" | None

    trailing_reds = _count_trailing_reds(parsed)
    written = 0

    if trailing_reds >= ALARM_THRESHOLD:
        latest_red = parsed[-1]
        url = latest_red.get("url", "?")
        exit_code = latest_red.get("exit", "?")
        reason = latest_red.get("reason", "?")
        fingerprint = f"RED|{url}|{exit_code}"

        if fingerprint != last_alarm_fingerprint or last_alarm_status != "red":
            ts = _now_local_iso()
            _append_alarm(
                f"{ts} ALARM PUBLIC_MCP_OUTAGE consecutive_reds={trailing_reds} "
                f"url={url} exit={exit_code} reason={reason} "
                f"log={UPTIME_LOG.as_posix()}"
            )
            state["last_alarm_fingerprint"] = fingerprint
            state["last_alarm_status"] = "red"
            state["first_red_ts"] = latest_red.get("ts", "")
            _save_state(state)
            written += 1
        # else: dedupe — same outage already alarmed, do not re-alarm.
        return written

    # Check for consecutive SOFT_YELLOW (fabrication-mode breadcrumb).
    # Does NOT count as RED; does NOT fire CONNECTOR_DEGRADED.
    # Fires a soft breadcrumb after SOFT_YELLOW_THRESHOLD consecutive lines.
    trailing_soft_yellows = _count_trailing_soft_yellows(parsed)
    if trailing_soft_yellows >= SOFT_YELLOW_THRESHOLD:
        latest_sy = parsed[-1]
        url = latest_sy.get("url", "?")
        fingerprint = f"SOFT_YELLOW|{url}"
        last_sy_fingerprint = state.get("last_soft_yellow_fingerprint")
        last_sy_status = state.get("last_soft_yellow_status")

        if fingerprint != last_sy_fingerprint or last_sy_status != "soft_yellow":
            ts = _now_local_iso()
            rtt_ms = latest_sy.get("rtt_ms", "?")
            _append_alarm(
                f"{ts} FABRICATION_MODE_SUSPECTED "
                f"consecutive_soft_yellows={trailing_soft_yellows} "
                f"url={url} rtt_ms={rtt_ms} "
                f"log={UPTIME_LOG.as_posix()}"
            )
            state["last_soft_yellow_fingerprint"] = fingerprint
            state["last_soft_yellow_status"] = "soft_yellow"
            _save_state(state)
            written += 1
        return written

    # Clear soft-yellow state if the tail is no longer soft-yellow.
    if state.get("last_soft_yellow_status") == "soft_yellow" and trailing_soft_yellows == 0:
        state.pop("last_soft_yellow_fingerprint", None)
        state.pop("last_soft_yellow_status", None)
        _save_state(state)

    # If a GREEN arrived after the latest RED, emit a RECOVERED line.
    # SKIP and SOFT_YELLOW are not outage recovery evidence.
    if last_alarm_status == "red":
        recovered = _first_green_after_last_red(parsed)
        if recovered is None:
            return written
        ts = _now_local_iso()
        url = recovered.get("url", "?")
        rtt_ms = recovered.get("rtt_ms", "?")
        _append_alarm(
            f"{ts} RECOVERED PUBLIC_MCP url={url} rtt_ms={rtt_ms} "
            f"log={UPTIME_LOG.as_posix()}"
        )
        state["last_alarm_status"] = "recovered"
        # Clear the outage fingerprint so the next red re-alarms.
        state.pop("last_alarm_fingerprint", None)
        state.pop("first_red_ts", None)
        _save_state(state)
        written += 1

    return written


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Uptime alarm evaluator.")
    ap.add_argument("--verbose", action="store_true",
                    help="print one-line summary to stdout")
    args = ap.parse_args(argv)

    written = evaluate()
    if args.verbose:
        print(f"[alarm] evaluated; alarm_lines_written={written}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
