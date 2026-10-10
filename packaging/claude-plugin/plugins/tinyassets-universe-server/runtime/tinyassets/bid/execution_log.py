"""Per-universe bid execution log (Phase G.2 rename of ``bid_ledger``).

Each time a daemon finalizes a NodeBid execution it appends an entry
to ``<universe>/bid_execution_log.json``. Append is serialized via a
sidecar ``.lock`` file so concurrent finalizations don't clobber.

**Distinct from the immutable settlement ledger at
``tinyassets/settlements.py``.** That module writes repo-root
``settlements/<bid_id>__<daemon_id>.yaml`` records that are part of
the v1-schema audit trail token-launch migration will read; records
are immutable and refuse overwrites. This module is the per-universe
daemon-local activity log — mutable, best-effort, primarily for
debugging and local dashboards.

The rename (G.2 follow-up #4, landed in Phase H) disambiguates the
two surfaces: one cross-host + immutable (settlements), one
per-universe + append-only (execution log).

``tinyassets/bid_ledger.py`` continues to re-export from here for a
single deprecation cycle; new code should import from this module.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

EXEC_LOG_FILENAME = "bid_execution_log.json"
# Dotted: a platform coordination file, never owner content
# (command_center_layout.PLATFORM_LOCK_NAMES).
EXEC_LOG_LOCK_FILENAME = ".bid_execution_log.json.lock"
# Legacy filenames — read during this transition only. Future
# hardening pass can remove once no universe retains the old names.
_LEGACY_LOG_FILENAME = "bid_ledger.json"


def execution_log_path(universe_path: Path) -> Path:
    return Path(universe_path) / EXEC_LOG_FILENAME


@contextlib.contextmanager
def _exec_log_lock(universe_path: Path) -> Iterator[None]:
    from tinyassets.universe_files import open_lock_file

    Path(universe_path).mkdir(parents=True, exist_ok=True)
    fd = open_lock_file(universe_path, EXEC_LOG_LOCK_FILENAME)
    try:
        if sys.platform == "win32":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if sys.platform == "win32":
                import msvcrt
                try:
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
    finally:
        os.close(fd)


def read_execution_log(universe_path: Path) -> list[dict]:
    """Return execution-log entries. Returns ``[]`` on missing / corrupt
    file (corrupt is logged, not raised — this log is append-only
    activity, best-effort read).

    Transition behavior: if the new filename is missing but a legacy
    ``bid_ledger.json`` exists, read from that. Write paths always
    use the new filename.
    """
    p = execution_log_path(universe_path)
    if not p.exists():
        legacy = Path(universe_path) / _LEGACY_LOG_FILENAME
        if legacy.exists():
            p = legacy
        else:
            return []
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return []
    if not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("bid_execution_log: corrupt at %s: %s", p, exc)
        return []
    if not isinstance(data, list):
        return []
    return [e for e in data if isinstance(e, dict)]


def append_execution_log_entry(universe_path: Path, entry: dict) -> None:
    """File-locked append of a single execution-log entry."""
    p = execution_log_path(universe_path)
    with _exec_log_lock(universe_path):
        data = read_execution_log(universe_path)
        data.append(dict(entry))
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, p)


# Back-compat aliases — delete once all callers switch over.
LEDGER_FILENAME = EXEC_LOG_FILENAME
LEDGER_LOCK_FILENAME = EXEC_LOG_LOCK_FILENAME
ledger_path = execution_log_path
read_ledger = read_execution_log
append_ledger_entry = append_execution_log_entry
