"""Center membership belongs to the owner's mounted volume (C11)."""

import re
import sqlite3
from contextlib import closing
from pathlib import Path


class Centers:
    def __init__(self, root: Path):
        self.path = Path(root) / ".centers.db"
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS centers ("
                       "center TEXT PRIMARY KEY, retired INTEGER NOT NULL DEFAULT 0)")

    def create(self, center: str) -> None:
        if not isinstance(center, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", center):
            raise ValueError("invalid command center name")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT retired FROM centers WHERE center=?", (center,)).fetchone()
            if row == (1,):
                raise PermissionError("a retired center cannot be recreated")
            db.execute("INSERT OR IGNORE INTO centers(center) VALUES (?)", (center,))

    def retire(self, center: str) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            if not db.execute("UPDATE centers SET retired=1 WHERE center=?", (center,)).rowcount:
                raise LookupError("center is not registered")

    def active(self) -> list[str]:
        with closing(sqlite3.connect(self.path)) as db:
            return [row[0] for row in db.execute(
                "SELECT center FROM centers WHERE retired=0 ORDER BY center")]


def admitted_owner(data_root: Path, *, center: str) -> str:
    from tinyassets.owner_runtime import require

    runtime = require(Path(data_root) / center)
    return runtime.principal
