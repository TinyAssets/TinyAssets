"""Continuous single-tab enforcement for the claude.ai CDP browser.

Host-set forever rule: at any moment, the CDP browser must have exactly
one tab. `claude_chat.py` enforces this at command entry, but that only
covers user-sim action windows — tabs opened by host, extensions, or
redirects while user-sim is idle would linger until the next command.

This watchdog runs standalone in the background and polls the CDP
endpoint every ``TAB_WATCHDOG_INTERVAL_S`` seconds (default 3). On any
poll where >1 tab is open, it picks the mission tab and closes the rest,
logging each heal to stderr and to ``output/tab_watchdog.log``.

Usage:

    python scripts/tab_watchdog.py &           # background
    python scripts/tab_watchdog.py --once      # single pass for testing

If the CDP endpoint is unreachable (Chrome not launched), the watchdog
sleeps and retries — it never raises. Run it standalone when needed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

import browser_lock  # noqa: E402

CDP = "http://localhost:9222"
CLAUDE_HOST = "claude.ai"
ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "output" / "tab_watchdog.log"
INTERVAL_S = float(os.environ.get("TAB_WATCHDOG_INTERVAL_S", "3"))


def _log(line: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    msg = f"{ts} {line}"
    print(msg, file=sys.stderr, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except Exception:
        pass


def _rank_page(page) -> int:
    url = getattr(page, "url", "") or ""
    if CLAUDE_HOST in url and "/chat/" in url:
        return 0
    if CLAUDE_HOST in url:
        return 1
    return 2


def _enforce_once(pw) -> int:
    """Single CDP pass. Returns tabs-closed count, -1 if CDP unreachable."""
    try:
        browser = pw.chromium.connect_over_cdp(CDP)
    except Exception:
        return -1
    try:
        pages = [p for ctx in browser.contexts for p in ctx.pages]
        lock = browser_lock.read()
        if lock is not None and lock.get("owner") == "lead":
            # Lead owns the tab — still enforce single-tab invariant,
            # but don't reorder based on claude.ai preference. Keep
            # the first (most-recently-active) page; close extras.
            if len(pages) <= 1:
                return 0
            keeper = pages[0]
            closed = []
            for p in pages[1:]:
                url = getattr(p, "url", "") or "(unknown)"
                try:
                    p.close()
                    closed.append(url)
                except Exception as exc:
                    _log(f"WATCHDOG WARN (lead-lock): failed to close {url}: {exc}")
            if closed:
                keeper_url = getattr(keeper, "url", "") or "(unknown)"
                _log(
                    f"WATCHDOG HEAL (lead-lock): closed {len(closed)} extra tab(s); "
                    f"kept {keeper_url}; closed {closed}"
                )
            return len(closed)
        if len(pages) <= 1:
            return 0
        pages_sorted = sorted(pages, key=_rank_page)
        keeper = pages_sorted[0]
        closed = []
        for p in pages_sorted[1:]:
            url = getattr(p, "url", "") or "(unknown)"
            try:
                p.close()
                closed.append(url)
            except Exception as exc:
                _log(f"WATCHDOG WARN: failed to close {url}: {exc}")
        if closed:
            keeper_url = getattr(keeper, "url", "") or "(unknown)"
            _log(
                f"WATCHDOG HEAL: closed {len(closed)} extra tab(s); "
                f"kept {keeper_url}; closed {closed}"
            )
        return len(closed)
    finally:
        try:
            browser.close()
        except Exception:
            pass


def run(once: bool = False) -> int:
    if sync_playwright is None:
        print("playwright not installed; watchdog cannot start", file=sys.stderr)
        return 2
    _log(f"WATCHDOG START: interval={INTERVAL_S}s cdp={CDP} once={once}")
    last_unreachable_logged = 0.0
    with sync_playwright() as pw:
        while True:
            closed = _enforce_once(pw)
            if closed == -1:
                now = time.monotonic()
                if now - last_unreachable_logged > 60:
                    _log("WATCHDOG IDLE: CDP unreachable; will retry silently")
                    last_unreachable_logged = now
            if once:
                return 0 if closed >= 0 else 1
            time.sleep(INTERVAL_S)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true", help="single pass for testing")
    ns = p.parse_args()
    try:
        return run(once=ns.once)
    except KeyboardInterrupt:
        _log("WATCHDOG STOP: interrupt received")
        return 0


if __name__ == "__main__":
    sys.exit(main())
