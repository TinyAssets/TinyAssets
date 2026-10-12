"""Uptime Canary — Layer 1 probe wrapper.

Per ``openspec/specs/uptime-and-alarms/spec.md``.

Runs ``mcp_public_canary.probe_result`` against the canonical MCP URL
and, for the production default, verifies the apex site ``/`` returns
HTTP 200. Appends one timestamped line to ``.agents/uptime.log``. Does
not mutate STATUS.md; alarm escalation is the separate
``scripts/uptime_alarm.py``'s job.

Invocation
----------
Windows Task Scheduler entry ``TinyAssets-Canary-L1`` invokes this every 2
minutes. Exit code mirrors the probe's: 0 green, nonzero red with reason
in the log line.

GHA usage (--once --format=gha):
    python scripts/uptime_canary.py --once --format=gha
Emits ``status=`` + ``msg=`` lines to stdout in $GITHUB_OUTPUT format.
Exit code mirrors the probe.

Stdlib only — must survive pip state dirty, tray crash, cloudflared
crash. If this canary can't run, the scheduler's "last run" error is
itself the alarm (per design-note §7).

URL precedence
--------------
MCP URL:
1. ``--url`` CLI arg.
2. ``TINYASSETS_MCP_CANARY_URL`` env var.
3. ``https://tinyassets.io/mcp`` default (canonical public endpoint via
   Cloudflare Worker; host directive 2026-04-20 retired the direct-tunnel
   ``mcp.tinyassets.io/mcp`` as a public URL — single entry point only).

Apex URL:
1. ``--apex-url`` CLI arg.
2. ``TINYASSETS_APEX_CANARY_URL`` env var.
3. ``https://tinyassets.io/`` when the MCP URL is the canonical
   production endpoint; disabled for non-production URLs to keep local
   tests offline.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Make the script importable regardless of CWD — Task Scheduler runs from
# the system32 directory by default.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from _canary_common import require_canary_bearer  # noqa: E402
from mcp_public_canary import CanaryError, probe_result  # noqa: E402

DEFAULT_URL = "https://tinyassets.io/mcp"
DEFAULT_APEX_URL = "https://tinyassets.io/"
DEFAULT_TIMEOUT = 10.0
LOG_PATH = _REPO_ROOT / ".agents" / "uptime.log"
LOG_ROTATE_BYTES = 10 * 1024 * 1024
APEX_FAILURE_EXIT_CODE = 12


class ApexProbeError(Exception):
    """Raised when the apex site check fails after the MCP probe is green."""


def _now_local_iso() -> str:
    """ISO-8601 with local timezone — matches design-note §2 format."""
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _rotate_if_needed(path: Path) -> None:
    try:
        if path.is_file() and path.stat().st_size > LOG_ROTATE_BYTES:
            backup = path.with_suffix(path.suffix + ".1")
            if backup.exists():
                backup.unlink()
            path.rename(backup)
    except OSError:
        # Rotation is best-effort; never fail the canary because rotation did.
        pass


def _append_log(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_needed(LOG_PATH)
    with LOG_PATH.open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")


def _format_green(ts: str, url: str, rtt_ms: int, apex_url: str | None = None) -> str:
    line = f"{ts} GREEN layer=1 url={url} rtt_ms={rtt_ms}"
    if apex_url:
        line += f" apex_url={apex_url} apex_status=200"
    return line


def _format_red(ts: str, url: str, exit_code: int, reason: str, rtt_ms: int) -> str:
    # Collapse newlines + strip the ``[canary] `` prefix so one probe = one line.
    reason_oneline = reason.replace("\n", " ").replace("\r", " ")
    return (
        f"{ts} RED   layer=1 url={url} exit={exit_code} "
        f"rtt_ms={rtt_ms} reason={reason_oneline!r}"
    )


def _emit_gha_kv(key: str, value: str) -> None:
    """Write one $GITHUB_OUTPUT key=value entry (multiline-safe heredoc).

    Uses a per-call random UUID delimiter so probe output containing a bare
    ``CANARY_EOF`` line cannot close the heredoc early and corrupt $GITHUB_OUTPUT.
    """
    if "\n" in value or "\r" in value:
        import uuid  # stdlib; import here keeps top-level imports minimal
        delimiter = f"EOF_{uuid.uuid4().hex}"
        print(f"{key}<<{delimiter}")
        print(value)
        print(delimiter)
    else:
        print(f"{key}={value}")


def _resolve_apex_url(mcp_url: str, explicit_apex_url: str | None) -> str | None:
    if explicit_apex_url is not None:
        return explicit_apex_url.strip() or None

    parsed = urllib.parse.urlsplit(mcp_url)
    if (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "tinyassets.io"
        and parsed.path.rstrip("/") == "/mcp"
    ):
        return DEFAULT_APEX_URL
    return None


def _probe_apex(apex_url: str, timeout: float) -> None:
    parsed = urllib.parse.urlsplit(apex_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ApexProbeError(f"invalid apex URL: {apex_url!r}")

    req = urllib.request.Request(
        apex_url,
        method="GET",
        headers={"User-Agent": "workflow-uptime-canary/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = response.getcode()
            if status != 200:
                raise ApexProbeError(f"apex {apex_url} returned HTTP {status}")
    except urllib.error.HTTPError as exc:
        raise ApexProbeError(f"apex {apex_url} returned HTTP {exc.code}") from exc
    except (OSError, TimeoutError, urllib.error.URLError) as exc:
        raise ApexProbeError(f"apex {apex_url} failed: {exc}") from exc


def run_probe(
    url: str,
    timeout: float,
    fmt: str = "log",
    apex_url: str | None = None,
) -> int:
    """Probe ``url`` once.

    ``fmt`` controls side-channel output:
    - ``"log"`` (default): append one line to LOG_PATH.
    - ``"gha"``: write ``status=`` + ``msg=`` to stdout in $GITHUB_OUTPUT format.
      Log line is still appended so the local .agents/uptime.log stays current.
    """
    ts = _now_local_iso()
    start = time.monotonic()
    resolved_apex_url = _resolve_apex_url(url, apex_url)
    try:
        probe_result(url, timeout)
        if resolved_apex_url:
            _probe_apex(resolved_apex_url, timeout)
    except CanaryError as exc:
        rtt_ms = int((time.monotonic() - start) * 1000)
        _append_log(_format_red(ts, url, exc.code, exc.msg, rtt_ms))
        if fmt == "gha":
            _emit_gha_kv("status", str(exc.code))
            _emit_gha_kv("msg", exc.msg)
        return exc.code
    except ApexProbeError as exc:
        rtt_ms = int((time.monotonic() - start) * 1000)
        msg = str(exc)
        _append_log(_format_red(ts, url, APEX_FAILURE_EXIT_CODE, msg, rtt_ms))
        if fmt == "gha":
            _emit_gha_kv("status", str(APEX_FAILURE_EXIT_CODE))
            _emit_gha_kv("msg", msg)
        return APEX_FAILURE_EXIT_CODE
    except Exception as exc:  # defensive — canary must never crash silently
        rtt_ms = int((time.monotonic() - start) * 1000)
        msg = f"unexpected: {exc!r}"
        _append_log(_format_red(ts, url, 99, msg, rtt_ms))
        if fmt == "gha":
            _emit_gha_kv("status", "99")
            _emit_gha_kv("msg", msg)
        return 99
    rtt_ms = int((time.monotonic() - start) * 1000)
    _append_log(_format_green(ts, url, rtt_ms, resolved_apex_url))
    if fmt == "gha":
        _emit_gha_kv("status", "0")
        msg = f"OK {url} rtt_ms={rtt_ms}"
        if resolved_apex_url:
            msg += f" apex_url={resolved_apex_url} apex_status=200"
        _emit_gha_kv("msg", msg)
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Layer-1 uptime canary.")
    ap.add_argument(
        "--url",
        default=os.environ.get("TINYASSETS_MCP_CANARY_URL", DEFAULT_URL),
        help=f"MCP endpoint URL (default: env TINYASSETS_MCP_CANARY_URL or {DEFAULT_URL})",
    )
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument(
        "--apex-url",
        default=os.environ.get("TINYASSETS_APEX_CANARY_URL"),
        help=(
            "Optional apex site URL that must return HTTP 200. Defaults to "
            f"{DEFAULT_APEX_URL} only when --url is the canonical production MCP URL."
        ),
    )
    ap.add_argument(
        "--once",
        action="store_true",
        help="Run a single probe and exit (default behavior; flag is an accepted no-op).",
    )
    ap.add_argument(
        "--format",
        dest="fmt",
        choices=["log", "gha"],
        default="log",
        help=(
            "Output format. 'log' (default): append to .agents/uptime.log. "
            "'gha': also emit status= + msg= lines to stdout for $GITHUB_OUTPUT capture."
        ),
    )
    args = ap.parse_args(argv)
    # Fail before spawning the child when the service-principal credential is
    # missing, so the operator sees the exact variable to restore.
    require_canary_bearer("uptime-canary")
    return run_probe(args.url, args.timeout, fmt=args.fmt, apex_url=args.apex_url)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
