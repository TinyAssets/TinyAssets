"""Opaque owner operation identities are scoped to their durable creation epoch."""
from __future__ import annotations

import sqlite3
from contextlib import closing


def new_identity(value):
    """Call only at identity creation, never while replaying an existing identity."""
    from tinyassets.owner_runtime import current

    runtime = current()
    if runtime is None:
        return value
    return f"{runtime.tools._state.epoch(runtime.principal)}:{value}"


def check(database, identity):
    """Receipt-store epoch check; opaque IDs are never timestamp-compared."""
    from tinyassets.owner_runtime import current

    runtime = current()
    if runtime is not None:
        with runtime.tools._state._conn() as db:
            fence = db.execute("SELECT v FROM host WHERE k='restore_epoch'").fetchone()
        prefix = str(runtime.tools._state.epoch(runtime.principal)) + ":"
        if fence and not identity.startswith(prefix):
            raise ValueError("operation predates restore epoch; not re-run")
    if not database.exists():
        return
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='restore_fence'").fetchone():
            return
        row = db.execute("SELECT epoch FROM restore_fence").fetchone()
    if row is None or not isinstance(identity, str) or not identity.startswith(str(row[0]) + ":"):
        raise ValueError("operation predates restore epoch; not re-run")
