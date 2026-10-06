"""Exact owner-view receipts. Viewing is observational and never approval."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from tinyassets.storage import DB_FILENAME

_SCHEMA = """CREATE TABLE IF NOT EXISTS owner_view_receipts (
 owner_user_id TEXT NOT NULL, universe_id TEXT NOT NULL,
 kind TEXT NOT NULL, item_id TEXT NOT NULL,
 PRIMARY KEY(owner_user_id, universe_id, kind, item_id))"""


def attention(base, root, owner, universe, *, messages=(), asks=(), pending=None):
    """Read counts and acknowledge only IDs present in this owner's own stores."""
    from tinyassets.storage.pending_requests import list_pending

    available_messages = set()
    transcript = Path(root) / ".conversation_memory.db"
    if transcript.exists():
        session = "principal:" + owner
        suffix = ":" + session
        with closing(sqlite3.connect(transcript.as_uri() + "?mode=ro", uri=True)) as conn:
            available_messages = {str(row[0]) for row in conn.execute(
                "SELECT id FROM conversation_turns WHERE speaker='universe' AND "
                "(session_id=? OR (session_id LIKE 'agent:%' AND substr(session_id, -?)=?))",
                (session, len(suffix), suffix))}
    # The app supplies its authoritative projection, including derived setup
    # and reconnect asks. Store-level callers use the retained request queue.
    rows = list_pending(Path(root)) if pending is None else pending
    available_asks = {row["request_id"] for row in rows
                      if row["status"] == "pending"}
    with closing(sqlite3.connect(Path(base) / DB_FILENAME, timeout=10)) as conn:
        conn.execute(_SCHEMA)
        with conn:
            for kind, wanted, available in (("message", messages, available_messages),
                                             ("ask", asks, available_asks)):
                conn.executemany("INSERT OR IGNORE INTO owner_view_receipts VALUES (?, ?, ?, ?)",
                                 [(owner, universe, kind, ident)
                                  for ident in set(wanted) & available])
        seen = {"message": set(), "ask": set()}
        for kind, ident in conn.execute(
            "SELECT kind, item_id FROM owner_view_receipts WHERE owner_user_id=? AND universe_id=?",
            (owner, universe)):
            seen[kind].add(ident)
    return {"messages": len(available_messages - seen["message"]),
            "asks": len(available_asks - seen["ask"])}
