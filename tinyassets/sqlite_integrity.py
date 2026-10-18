"""One index-only integrity policy for cutover census and stopped apply."""
from __future__ import annotations

import re
import sqlite3


def check(db: sqlite3.Connection, *, repair: bool = False) -> list[str]:
    """Return named repairable indexes; reject every other integrity failure."""
    errors = [row[0] for row in db.execute("PRAGMA integrity_check")]
    if errors == ["ok"]:
        return []
    indexes = set()
    for error in errors:
        match = re.fullmatch(r"(?:row \d+ missing from index|wrong # of entries in index) (.+)",
                             error)
        if match is None:
            raise ValueError("database integrity failure is not index-only")
        name = match[1]
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
                      (name,)).fetchone() is None:
            raise ValueError("database integrity failure names an absent index")
        indexes.add(name)
    if repair:
        for name in sorted(indexes):
            db.execute('REINDEX "' + name.replace('"', '""') + '"')
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("database integrity failed after index repair")
    return sorted(indexes)
