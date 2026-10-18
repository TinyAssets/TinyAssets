"""Router-owned durable cutover admission and disaster credential fence."""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing

NAME = ".router-traffic.sqlite3"


def initialize(root):
    with closing(sqlite3.connect(root / NAME)) as db, db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("CREATE TABLE IF NOT EXISTS traffic (singleton INTEGER PRIMARY KEY "
                   "CHECK(singleton=1), traffic_opened_at REAL, credentials_after REAL NOT NULL)")
        db.execute("INSERT OR IGNORE INTO traffic VALUES (1,NULL,0)")


def state(root):
    with closing(sqlite3.connect((root / NAME).as_uri() + "?mode=ro", uri=True)) as db:
        row = db.execute("SELECT traffic_opened_at,credentials_after FROM traffic "
                         "WHERE singleton=1").fetchone()
        if row is None:
            raise ValueError("router traffic marker is missing")
        return row


def open_traffic(root):
    # Only the router calls this, after the deploy's root-authenticated RPC.
    with closing(sqlite3.connect((root / NAME).as_uri() + "?mode=rw", uri=True)) as db, db:
        db.execute("PRAGMA synchronous=FULL")
        if db.execute("UPDATE traffic SET traffic_opened_at=COALESCE(traffic_opened_at,?) "
                      "WHERE singleton=1", (time.time(),)).rowcount != 1:
            raise ValueError("router traffic marker is missing")
    return {"traffic_opened": True}


def admits(root, owner):
    import os

    if not (root / NAME).exists():
        # Pre-cutover code/tests have no admission store. The owner-box layout
        # must always have one; a damaged live store must never open traffic.
        import json

        marker = root / ".layout.json"
        return (not marker.exists()
                or json.loads(marker.read_text()).get("box_layout") != "owner-box")
    opened, _ = state(root)
    return (opened is not None or owner == "canary"
            or bool(owner) and owner == os.environ.get("TINYASSETS_CANARY_USER_ID"))


def accepts_issued_at(root, issued_at):
    if not (root / NAME).exists():
        return True
    _, cutoff = state(root)
    return not cutoff or (type(issued_at) in (int, float) and issued_at > cutoff)


def credential(root, name, configured):
    """Disaster replacements override old process environment credentials."""
    import json

    path = root / ".router-credentials.json"
    return json.loads(path.read_text())[name] if path.exists() else configured
