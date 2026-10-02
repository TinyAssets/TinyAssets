"""Is the daemon running anyone's work right now? Asked before a deploy swaps it.

Every merge to main deploys, and a deploy recreates the daemon container, which
kills whatever turn is running in it. On 2026-10-02 that cut the founder's live
village turn twice in one night. ``deploy-prod`` runs this before the swap and
waits while it answers "busy" (``.github/workflows/deploy-prod.yml``, step
"Wait for in-flight turns").

**It runs in a throwaway sibling container, never in the daemon's.** The
workflow ships this file from the checkout and pipes it to ``docker run --rm -i
--network none --user 1001:1001 -v tinyassets-data:/data --entrypoint python
<the daemon's image> -``. That reads the same volume as the daemon's own uid.
It does not ``docker exec``, because every repo-authored exec into the daemon goes
through ``ta-op``'s closed mode table (``scripts/check_drop_first_exec.py``). It
does not use the host's python as root either: a root sqlite open can create
``-wal``/``-shm`` files the daemon then cannot open. The liveness locks below are
kernel ``flock``s on the volume's inodes, so the sibling sees the daemon's. Because
the image may predate any helper this repo has now, the probe imports nothing from
``tinyassets`` and uses the standard library only (Python 3.11+).

What counts as in flight
------------------------
**Account seats a live process still holds.** A chat turn holds an interactive
seat for the whole model call (``universe_intelligence``), and a graph agent
node holds a background seat (``graph_compiler``). A seat counts while its
holder's liveness lock is held, EXPIRED OR NOT: the ledger keeps an expired seat
whose holder is alive (``universe_seats._reap``), and so must this. A seat whose
holder is unprovable counts until its lease expires; a dead holder's never
counts. The ledger's own reaper rule, inverted.

**Graph runs queued or running under a live owner.** Seats cover agent calls,
not whole runs: an automation releases its admission seat before its run
starts, and a source-code node runs with no seat at all. A run row counts while
its ``owner_token``'s liveness lock is held, or, for a row with no token, if it
started after this container booted. These are the rows
``runs.recover_in_flight_runs`` would interrupt after the swap.

**Journal turns are reported, not gated on.** An ``agent_turns`` row in a working
state can outlive its task -- a cancelled task leaves ``native_started`` behind,
and nothing settles it until the next boot (``agent_turn_reconcile``). Gating on
it would hold every deploy to the cap behind a phantom. The rows are still
printed, so a deploy log shows them next to the seats.

Exit status: 0 idle, 10 busy, 2 cannot tell. "Cannot tell" is never reported as
idle; the caller decides what an unknown answer means for its own action.

The marker
----------
``--mark-pending`` writes ``.deploy-pending.json`` into the data root so the
waiting is visible to ``get_status`` (``deploy_pending``) and to whoever is
watching a long turn. It carries ``expires_at``: a deploy job that dies
mid-wait leaves a marker that stops meaning anything a minute later, instead of
reading "update pending" forever. ``--clear-pending`` removes it.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

#: Same names the daemon uses (``universe_seats.LEDGER_NAME``,
#: ``storage.DB_FILENAME``); duplicated because this file cannot import them.
#: ``tests/test_turns_in_flight.py`` pins them against the source.
SEATS_DB = ".account_seats.db"
JOURNAL_DB = ".tinyassets.db"
MARKER = ".deploy-pending.json"
#: ``agent_turn_journal.WORKING_STATES``, pinned the same way.
WORKING_STATES = ("inference_started", "native_started", "ready", "tools_pending")
#: ``runs.RUN_STATUS_{QUEUED,RESUMED,RUNNING}``: what recovery interrupts.
IN_FLIGHT_RUN_STATUSES = ("queued", "resumed", "running")
RUNS_DB = ".runs.db"
#: ``process_liveness.LIVENESS_DIR`` and its token shape.
LIVENESS_DIR = ".consumer_liveness"
_TOKEN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
ALIVE, DEAD, UNKNOWN_OWNER = "alive", "dead", "unknown"

IDLE, BUSY, UNKNOWN = 0, 10, 2


def _connect(path: Path) -> sqlite3.Connection:
    """Open an EXISTING database without creating anything.

    ``mode=rw`` refuses to create a missing file and this runs no DDL. Not
    ``mode=ro``: a WAL database whose ``-shm`` is missing cannot be opened
    read-only, which is the state a freshly restarted box is in.
    """
    conn = sqlite3.connect(
        path.resolve().as_uri() + "?mode=rw", uri=True, timeout=10.0, isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,),
    ).fetchone() is not None


def _try_lock(fd: int) -> bool:
    """``singleton_lock._lock_fd``'s primitive, then let go.

    It must be the SAME primitive the holder took: flock and fcntl record locks
    do not see each other.
    """
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return True
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    fcntl.flock(fd, fcntl.LOCK_UN)
    return True


def owner_state(base: Path, token: object) -> str:
    """``process_liveness.owner_state``: alive, dead or unknown. Deletes nothing."""
    if (not isinstance(token, str) or not token or len(token) > 128
            or not set(token) <= _TOKEN_CHARS):
        return UNKNOWN_OWNER
    path = base / LIVENESS_DIR / f"{token}.lock"
    if not path.is_file():
        return UNKNOWN_OWNER
    try:
        fd = os.open(str(path), os.O_RDWR)
    except OSError:
        return UNKNOWN_OWNER
    try:
        return DEAD if _try_lock(fd) else ALIVE
    finally:
        os.close(fd)


def live_seats(data_dir: Path, *, now: float) -> list[dict[str, object]]:
    """Seats a live process holds. A missing ledger has none."""
    path = data_dir / SEATS_DB
    if not path.exists():
        return []
    conn = _connect(path)
    try:
        if not _has_table(conn, "account_seats"):
            return []
        rows = conn.execute(
            "SELECT seat_class, kind, universe_id, holder, acquired_at, expires_at "
            "FROM account_seats ORDER BY acquired_at",
        ).fetchall()
    finally:
        conn.close()
    states: dict[str, str] = {}
    out = []
    for row in rows:
        holder = str(row["holder"])
        if holder not in states:
            states[holder] = owner_state(path.parent, holder)
        expired = float(row["expires_at"]) < now
        if states[holder] == DEAD or (states[holder] != ALIVE and expired):
            continue
        out.append({
            "seat_class": row["seat_class"],
            "kind": row["kind"],
            "universe_id": row["universe_id"],
            "holder": states[holder],
            "expired": expired,
            "age_s": round(now - float(row["acquired_at"]), 1),
        })
    return out


def _runs_dbs(data_dir: Path) -> list[Path]:
    """The root run store and every universe's, one level down."""
    found = [data_dir / RUNS_DB] if (data_dir / RUNS_DB).is_file() else []
    for child in sorted(data_dir.iterdir()) if data_dir.is_dir() else ():
        candidate = child / RUNS_DB
        if child.is_dir() and not child.is_symlink() and candidate.is_file():
            found.append(candidate)
    return found


def active_runs(data_dir: Path, *, now: float, boot: float | None) -> list[dict[str, object]]:
    """Queued or running graph runs a live process owns."""
    out = []
    for path in _runs_dbs(data_dir):
        conn = _connect(path)
        try:
            if not _has_table(conn, "runs"):
                continue
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
            owner_col = "owner_token" if "owner_token" in columns else "NULL"
            rows = conn.execute(
                f"SELECT run_id, status, started_at, {owner_col} AS owner_token FROM runs "
                f"WHERE status IN ({','.join('?' * len(IN_FLIGHT_RUN_STATUSES))})",
                IN_FLIGHT_RUN_STATUSES,
            ).fetchall()
        finally:
            conn.close()
        states: dict[str, str] = {}
        for row in rows:
            owner = row["owner_token"]
            if owner:
                if owner not in states:
                    states[owner] = owner_state(path.parent, owner)
                live = states[owner] == ALIVE
            else:
                live = boot is not None and float(row["started_at"] or 0) >= boot
            if live:
                out.append({
                    "store": "" if path.parent == data_dir else path.parent.name,
                    "run": str(row["run_id"])[:8],
                    "status": row["status"],
                    "age_s": round(now - float(row["started_at"] or now), 1),
                })
    return out


def working_turns(data_dir: Path, *, now: float) -> list[dict[str, object]]:
    """Journal rows in a working state. Reported only -- see the module doc."""
    path = data_dir / JOURNAL_DB
    if not path.exists():
        return []
    conn = _connect(path)
    try:
        if not _has_table(conn, "agent_turns"):
            return []
        rows = conn.execute(
            "SELECT universe_id, turn_id, state, created_at FROM agent_turns "
            f"WHERE state IN ({','.join('?' * len(WORKING_STATES))}) "
            "ORDER BY created_at",
            WORKING_STATES,
        ).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        age = None
        stamp = row["created_at"]
        if isinstance(stamp, str) and stamp.endswith("Z"):
            try:
                when = datetime.fromisoformat(stamp[:-1] + "+00:00")
                age = round(now - when.timestamp(), 1)
            except ValueError:
                age = None
        out.append({
            "universe_id": row["universe_id"],
            # A prefix is enough to find the row; the full id is not a secret but
            # a deploy log is not the place to carry it.
            "turn": str(row["turn_id"])[:8],
            "state": row["state"],
            "age_s": age,
        })
    return out


def observe(data_dir: Path, *, now: float | None = None,
            boot: float | None = None) -> tuple[int, dict[str, object]]:
    """(exit status, report). Never raises for a store it cannot read.

    ``boot`` is when the daemon container started (epoch seconds), from the
    host's ``docker inspect``; None leaves token-less run rows uncounted.
    """
    moment = time.time() if now is None else now
    report: dict[str, object] = {"data_dir": str(data_dir), "observed_at": moment,
                                 "boot_epoch": boot}
    try:
        seats = live_seats(data_dir, now=moment)
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        report["error"] = f"seat ledger unreadable: {type(exc).__name__}: {exc}"
        report["in_flight"] = None
        return UNKNOWN, report
    try:
        runs = active_runs(data_dir, now=moment, boot=boot)
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        report["error"] = f"run store unreadable: {type(exc).__name__}: {exc}"
        report["in_flight"] = None
        return UNKNOWN, report
    report["seats"] = seats
    report["runs"] = runs
    report["in_flight"] = len(seats) + len(runs)
    try:
        report["journal_working"] = working_turns(data_dir, now=moment)
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        report["journal_error"] = f"{type(exc).__name__}: {exc}"
    return (BUSY if report["in_flight"] else IDLE), report


def write_marker(data_dir: Path, payload: dict[str, object]) -> Path:
    """Atomically replace the pending marker (temp file + rename)."""
    target = data_dir / MARKER
    fd, tmp = tempfile.mkstemp(prefix=".deploy-pending.", dir=str(data_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def clear_marker(data_dir: Path) -> bool:
    try:
        (data_dir / MARKER).unlink()
    except FileNotFoundError:
        return False
    return True


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("TINYASSETS_DATA_DIR") or "/data",
        help="data root (default: $TINYASSETS_DATA_DIR, else /data)",
    )
    parser.add_argument("--mark-pending", action="store_true",
                        help="also write the deploy-pending marker")
    parser.add_argument("--clear-pending", action="store_true",
                        help="remove the deploy-pending marker and exit")
    parser.add_argument("--target", default="", help="revision the deploy is waiting to ship")
    parser.add_argument("--waiting-since", type=float, default=0.0,
                        help="epoch seconds the deploy started waiting")
    parser.add_argument("--deadline", type=float, default=0.0,
                        help="epoch seconds the deploy proceeds regardless")
    parser.add_argument("--ttl", type=float, default=90.0,
                        help="seconds the marker stays meaningful without a refresh")
    parser.add_argument("--run-url", default="", help="the waiting deploy run")
    parser.add_argument("--boot-epoch", type=float, default=None,
                        help="epoch seconds the daemon container started")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir)

    if args.clear_pending:
        removed = clear_marker(data_dir)
        print(json.dumps({"cleared": removed}))
        return IDLE

    status, report = observe(data_dir, boot=args.boot_epoch)
    if args.mark_pending:
        now = float(report["observed_at"])
        marker = {
            "pending": True,
            "target": args.target,
            "waiting_since": _iso(args.waiting_since or now),
            "deadline": _iso(args.deadline) if args.deadline else "",
            "in_flight": report.get("in_flight"),
            "observed_at": _iso(now),
            "expires_at": _iso(now + max(args.ttl, 1.0)),
            "run_url": args.run_url,
        }
        try:
            write_marker(data_dir, marker)
        except OSError as exc:
            # Visibility only; it must not turn a readable answer into "unknown".
            report["marker_error"] = f"{type(exc).__name__}: {exc}"
    print(json.dumps(report, sort_keys=True))
    return status


if __name__ == "__main__":
    sys.exit(main())
