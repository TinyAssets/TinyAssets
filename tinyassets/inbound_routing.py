"""Inbound routing metadata only: failure budget, never an envelope or owner path."""
import sqlite3
import time
from contextlib import closing
from pathlib import Path


class DeliveryBreakers:
    def __init__(self, root: Path):
        self.path = Path(root) / ".inbound-routing.sqlite3"

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE IF NOT EXISTS breakers (binding TEXT, revision INTEGER, "
                   "owner TEXT NOT NULL, tripped INTEGER NOT NULL DEFAULT 0, "
                   "PRIMARY KEY(binding,revision))")
        db.execute("CREATE TABLE IF NOT EXISTS failures (binding TEXT, revision INTEGER, at REAL)")
        return db

    def tripped(self, binding, revision):
        with closing(self.connect()) as db:
            row = db.execute("SELECT tripped FROM breakers WHERE binding=? AND revision=?",
                             (binding, revision)).fetchone()
            return bool(row and row[0])

    def failure(self, owner, binding, revision, *, now=None):
        at = time.time() if now is None else now
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("DELETE FROM failures WHERE at<=?", (at - 60,))
                db.execute("INSERT OR IGNORE INTO breakers VALUES (?,?,?,0)",
                           (binding, revision, owner))
                db.execute("INSERT INTO failures VALUES (?,?,?)", (binding, revision, at))
                count = db.execute("SELECT count(*) FROM failures WHERE binding=? AND revision=?",
                                   (binding, revision)).fetchone()[0]
                if count >= 3:
                    db.execute("UPDATE breakers SET tripped=1 WHERE binding=? AND revision=?",
                               (binding, revision))
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def for_owner(self, owner):
        with closing(self.connect()) as db:
            return [dict(row) for row in db.execute(
                "SELECT binding,revision,tripped FROM breakers WHERE owner=?", (owner,))]

    def ready(self, owner, binding, revision):
        """Called after box reconciliation of this exact installation revision."""
        with closing(self.connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT owner FROM breakers WHERE binding=? AND revision=?",
                                 (binding, revision)).fetchone()
                if row is not None and row[0] != owner:
                    raise PermissionError("installation readiness is not owner-bound")
                if row is not None:
                    db.execute("DELETE FROM failures WHERE binding=? AND revision=?",
                               (binding, revision))
                    db.execute("UPDATE breakers SET tripped=0 WHERE binding=? AND revision=?",
                               (binding, revision))
                db.commit()
            except BaseException:
                db.rollback()
                raise
