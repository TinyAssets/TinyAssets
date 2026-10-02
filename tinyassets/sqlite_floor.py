"""The SQLite library floor the serving daemon refuses to start below.

SQLite before 3.51.3 carries the WAL-reset corruption bug; production ran
3.46.1 (Debian trixie) until the image started building the pinned amalgamation
(Dockerfile, target-architecture S1a.1). Litestream replicates the WAL, so a
daemon that silently fell back to the system library would replicate exactly
the state the fix exists for. The image build already fails on a fall-back;
this is the same check at serving startup, for any image that skipped the
build gate.
"""

from __future__ import annotations

import sqlite3

MIN_SQLITE_VERSION: tuple[int, int, int] = (3, 51, 3)


def require_sqlite_floor(
    version_info: tuple[int, ...] = sqlite3.sqlite_version_info,
) -> None:
    """Raise if the loaded SQLite library is older than the floor."""
    if tuple(version_info) < MIN_SQLITE_VERSION:
        have = ".".join(str(part) for part in version_info)
        need = ".".join(str(part) for part in MIN_SQLITE_VERSION)
        raise RuntimeError(
            f"SQLite {have} is loaded; the serving daemon requires >= {need} "
            "(WAL-reset corruption fix). The image must ship the pinned "
            "libsqlite3 in /usr/local/lib; see the Dockerfile."
        )
