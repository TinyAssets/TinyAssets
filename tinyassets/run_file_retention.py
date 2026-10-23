"""Bounded existing-maintenance tick for unbound captures and cleanup debt.

The scan cursor is only fairness, never authority or a new work queue. Current
rows plus a held operation lock decide each transition. No execution is retried.
"""

import logging
import time
from pathlib import Path

from tinyassets import runs
from tinyassets.run_file_cleanup import cleanup_file_operation
from tinyassets.scoped_reset import _assert_recovery_state_is_clean, acquire_maintenance_barrier
from tinyassets.storage import run_files as store
from tinyassets.storage.run_file_lock import try_file_operation_lock

logger = logging.getLogger(__name__)


def _expire_operation(base, operation_id):
    with try_file_operation_lock(base, operation_id=operation_id) as guard:
        if guard is None:
            return False
        with runs._connect(base) as conn:
            conn.execute("BEGIN IMMEDIATE")
            guard.require_held(conn)
            row = conn.execute(
                "SELECT * FROM run_file_operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            if row is None or row["state"] == "released":
                return False
            if row["state"] == "cleanup":
                return True
            deadline = row["unbound_expires_at"]
            if not deadline or deadline > time.time():
                return False
            selected = None
            if row["state"] == "committed":
                selected = [
                    item[0]
                    for item in conn.execute(
                        "SELECT o.file_id FROM run_file_objects o WHERE o.operation_id=? "
                        "AND o.state='ready' AND NOT EXISTS "
                        "(SELECT 1 FROM run_file_bindings b WHERE b.file_id=o.file_id)",
                        (operation_id,),
                    )
                ]
                if not selected:
                    return False
            store.mark_cleanup_in_transaction(
                conn,
                operation_id=operation_id,
                owner_id=row["owner_id"],
                universe_id=row["universe_id"],
                file_ids=selected,
            )
            return True


def reconcile_run_files(base, *, after_operation_id="", limit=32):
    """Return the next rotating cursor; malformed stores report/refuse, not guess."""
    if type(limit) is not int or not 1 <= limit <= 128 or type(after_operation_id) is not str:
        raise ValueError("invalid file retention scan")
    base = Path(base).absolute()
    if not runs.runs_db_path(base).is_file():
        return ""
    with acquire_maintenance_barrier(base, exclusive=False, timeout=5):
        _assert_recovery_state_is_clean(base)
        with runs._connect(base) as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_file_operations'"
                ).fetchone()
                is None
            ):
                return ""
            # An old pre-activation schema has unknown deadlines. Upgrade only
            # at normal capture initialization, never infer ages during deletion.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(run_file_operations)")}
            if "unbound_expires_at" not in columns:
                return ""
            operations = [
                row[0]
                for row in conn.execute(
                    "SELECT operation_id FROM run_file_operations WHERE state!='released' "
                    "AND operation_id>? ORDER BY operation_id LIMIT ?",
                    (after_operation_id, limit),
                )
            ]
        for operation_id in operations:
            try:
                if _expire_operation(base, operation_id):
                    cleanup_file_operation(base, operation_id=operation_id)
            except Exception:  # One corrupt/deferred operation must not starve siblings.
                logger.exception("run file retention: cleanup remains pending for %s", operation_id)
        return operations[-1] if len(operations) == limit else ""
