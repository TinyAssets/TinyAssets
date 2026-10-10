"""Read accepted intent before its terminal transcript exists. No writes or replay.

Callers supply an already authorized home and memory session, just as for the
retained transcript. Pending entries have admission identities, not row cursors.
"""

import json
import sqlite3
from contextlib import closing
from pathlib import Path


def _open(path):
    if path.is_symlink() or path.resolve() != path:
        raise PermissionError("conversation_store_outside_home")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def read_pending(universe_dir, session):
    """Accepted main-thread admissions, excluding already projected/deleted pairs."""
    root = Path(universe_dir).resolve()
    path = root.parent / ".runs.db"
    active = _read_active(root, session)
    if not session.startswith("principal:") or not path.exists():
        return exclude_saved(root, session, active)
    owner = session.removeprefix("principal:")
    with closing(_open(path)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                            "name='conversation_run_admissions'").fetchone():
            return exclude_saved(root, session, active)
        rows = conn.execute(
            "SELECT a.admission_id,a.intent_json,a.created_at,a.projection_state,r.status "
            "FROM conversation_run_admissions a JOIN runs r ON r.run_id=a.run_id "
            "WHERE a.owner_user_id=? AND a.universe_id=? AND a.session_id=? "
            "AND r.owner_user_id=? AND r.queue_universe_id=? AND r.actor=? "
            "AND a.projection_state NOT IN ('committed','expired') ORDER BY a.created_at",
            (owner, root.name, session, owner, root.name, f"universe:{root.name}"),
        ).fetchall()
    entries = active + [{"speaker": "founder", "text": json.loads(r["intent_json"])["message"],
             "ts": r["created_at"], "consumer_turn_id": r["admission_id"],
             "pending": True, "state": r["status"], "projection": r["projection_state"]}
            for r in rows]
    return exclude_saved(root, session, entries)


def exclude_saved(universe_dir, session, entries):
    """Reconcile after a retained read too: completion can race either snapshot."""
    path = Path(universe_dir).resolve() / ".conversation_memory.db"
    if not entries or not path.exists():
        return entries
    with closing(_open(path)) as conn:
        # The marker survives deletion; never resurrect a deleted projected pair.
        markers = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND "
                               "name='conversation_terminal_projections'").fetchone()
        columns = {r[1] for r in conn.execute("PRAGMA table_info(conversation_turns)")}
        remaining = []
        for entry in entries:
            admission = entry.get("consumer_turn_id")
            if admission and markers and conn.execute(
                "SELECT 1 FROM conversation_terminal_projections WHERE session_id=? "
                "AND admission_id=?", (session, admission),
            ).fetchone():
                continue
            send_id = entry.get("client_send_id")
            if send_id and "client_send_id" in columns and conn.execute(
                "SELECT 1 FROM conversation_turns WHERE session_id=? AND client_send_id=?",
                (session, send_id),
            ).fetchone():
                continue
            remaining.append(entry)
        return remaining


def _read_active(root, session):
    from tinyassets.agent_sessions import RECORDS_DIR

    path = root.parent / RECORDS_DIR / root.name / "steering.db"
    if not path.exists():
        return []
    with closing(_open(path)) as conn:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(open_turns)")}
        if not {"message", "client_send_id"}.issubset(columns):
            return []
        rows = conn.execute(
            "SELECT message,opened_at,client_send_id FROM open_turns WHERE session_key=?",
            (f"thread:{session}",),
        ).fetchall()
    return [{"speaker": "founder", "text": r["message"], "ts": r["opened_at"],
             "client_send_id": r["client_send_id"], "pending": True, "state": "running"}
            for r in rows if r["message"] and r["client_send_id"]]
