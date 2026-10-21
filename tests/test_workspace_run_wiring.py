"""The run lifecycle owes the workspace outbox its work (workspace-node D0/D6).

A run that held a scratch lease releases it THROUGH an outbox entry.  A local
lease is atomic with terminal status; a universe-owned lease is enqueued just
after the root commit and repaired by its sweep across the two-WAL crash gap.
Every terminal recovery path follows that protocol; a once-per-process
reconciler finishes what an earlier process left; and every workspace refusal
is a first-class failure class with an action.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets import runs, workspace_pool

GIB = 1024 ** 3


class _NoopFs:
    """A filesystem that has nothing to delete: every entry completes.

    It stands in for the owner-box reclaimer (``BoxPoolFilesystem``), which
    is built over the data root.
    """

    def __init__(self, data_root: Path | None = None) -> None:
        self.data_root = data_root

    def exists(self, path: Path) -> bool:
        return False

    def rename(self, src: Path, dst: Path) -> None:
        raise AssertionError("nothing exists, nothing to rename")

    def owner_scoped(self, path: Path) -> bool:
        return False

    def remove_tree_no_follow(self, path: Path) -> None:
        raise AssertionError("nothing exists, nothing to remove")


def _seed_run(
    base: Path,
    run_id: str,
    status: str = "running",
    *,
    queue_universe_id: str = "",
    actor: str = "universe:u-test",
) -> None:
    runs.initialize_runs_db(base)
    with runs._connect(base) as conn:
        conn.execute(
            "INSERT INTO runs "
            "(run_id, branch_def_id, thread_id, status, started_at, actor, "
            "queue_universe_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id, "b1", f"t-{run_id}", status, time.time() - 7200,
                # Every run records who asked for it; the column is NOT NULL.
                actor, queue_universe_id or None,
            ),
        )


def _admit(base: Path, tmp: Path, run_id: str, universe: str = "u-1") -> workspace_pool.Lease:
    db = runs.runs_db_path(base)
    with runs._connect(base) as conn:
        workspace_pool.ensure_schema(conn)
    return workspace_pool.admit(
        db,
        universe_id=universe,
        connection_id="c1",
        repo_key="github.com--o--r",
        storage_class="scratch",
        run_id=run_id,
        max_bytes=GIB,
        pool_root=tmp / "scratch",
        universe_root=tmp / "universes" / universe,
    )


def _pending(base: Path, run_id: str) -> list[tuple]:
    conn = sqlite3.connect(runs.runs_db_path(base))
    try:
        return conn.execute(
            "SELECT action, lease_id FROM workspace_outbox WHERE run_id = ? AND done_at IS NULL",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()




























































