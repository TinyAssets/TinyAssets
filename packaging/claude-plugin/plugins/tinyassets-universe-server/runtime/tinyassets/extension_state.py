"""Owner-bound extension lifecycle in the existing private package store.

Callers establish live authority. Identity is constructor-bound, never accepted
from a manifest or a dispatch argument. No package code executes in this module.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

from tinyassets import command_center_packages as packages
from tinyassets.extension_manifest import ExtensionError, Revision, build_revision

_SCHEMA = """
CREATE TABLE IF NOT EXISTS extension_revisions (
 owner_id TEXT NOT NULL, universe_id TEXT NOT NULL, agent_id TEXT NOT NULL,
 name TEXT NOT NULL, revision TEXT NOT NULL,
 PRIMARY KEY(owner_id, universe_id, agent_id, name, revision)
);
CREATE TABLE IF NOT EXISTS extension_activations (
 owner_id TEXT NOT NULL, universe_id TEXT NOT NULL, agent_id TEXT NOT NULL,
 name TEXT NOT NULL, revision TEXT NOT NULL, generation INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('active','revoked')),
 ceiling_json TEXT NOT NULL,
 PRIMARY KEY(owner_id, universe_id, agent_id, name)
);
"""


class ExtensionStore:
    def __init__(self, base, *, owner, universe, agent):
        if not all(isinstance(x, str) and x for x in (owner, universe, agent)):
            raise ExtensionError("extension lifecycle requires a bound identity")
        self.base = base
        self.identity = (owner, universe, agent)

    @contextmanager
    def _db(self):
        with packages._db(self.base) as conn:
            conn.executescript(_SCHEMA)
            yield conn

    def install(self, files):
        from tinyassets import storage_accounting

        revision = build_revision(files)
        owner = self.identity[0]
        if not packages.blob_owned(self.base, owner, revision.digest):
            with storage_accounting.charged(
                self.base, account_id=storage_accounting.account_for_actor(self.base, owner),
                store="packages", nbytes=len(revision.blob),
            ):
                packages.store_blob(self.base, author_id=owner, blob=revision.blob)
        with self._db() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO extension_revisions VALUES (?, ?, ?, ?, ?)",
                (*self.identity, revision.name, revision.digest),
            )
        return {"name": revision.name, "revision": revision.digest, "state": "installed"}

    def _installed(self, name, revision):
        with self._db() as conn:
            row = conn.execute(
                "SELECT 1 FROM extension_revisions WHERE owner_id=? AND universe_id=? "
                "AND agent_id=? AND name=? AND revision=?",
                (*self.identity, name, revision),
            ).fetchone()
        # Check bound installation before reading or probing a content-addressed blob.
        if row is None:
            raise ExtensionError("extension revision not installed here")

    def load(self, name, revision):
        self._installed(name, revision)
        return Revision(name, revision, packages.read_blob(self.base, revision))

    def list(self):
        with self._db() as conn:
            rows = conn.execute(
                "SELECT r.name, r.revision, CASE WHEN a.revision=r.revision THEN a.state "
                "ELSE 'installed' END AS state, COALESCE(a.generation,0) AS generation "
                "FROM extension_revisions r LEFT JOIN extension_activations a ON "
                "r.owner_id=a.owner_id AND r.universe_id=a.universe_id AND "
                "r.agent_id=a.agent_id AND r.name=a.name "
                "WHERE r.owner_id=? AND r.universe_id=? AND r.agent_id=? "
                "ORDER BY r.name,r.revision", self.identity,
            ).fetchall()
        return [dict(row) for row in rows]

    def transition(self, name, revision, *, expected_generation, active, ceiling=()):
        if active:
            self.load(name, revision).content()
        else:
            self._installed(name, revision)  # Revoke survives missing/corrupt package bytes.
        if type(expected_generation) is not int or expected_generation < 0:
            raise ExtensionError("expected_generation must be a nonnegative integer")
        if not isinstance(ceiling, (list, tuple)) or any(not isinstance(x, str) for x in ceiling):
            raise ExtensionError("invalid capability ceiling")
        with self._db() as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(
                "SELECT * FROM extension_activations WHERE owner_id=? AND universe_id=? "
                "AND agent_id=? AND name=?", (*self.identity, name),
            ).fetchone()
            if (old["generation"] if old else 0) != expected_generation:
                raise ExtensionError("extension generation changed; inspect before retrying")
            if not active and (old is None or old["revision"] != revision):
                raise ExtensionError("only the current revision can be revoked")
            generation = expected_generation + 1
            state = "active" if active else "revoked"
            conn.execute(
                "INSERT INTO extension_activations VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(owner_id,universe_id,agent_id,name) DO UPDATE SET "
                "revision=excluded.revision,generation=excluded.generation,"
                "state=excluded.state,ceiling_json=excluded.ceiling_json",
                (*self.identity, name, revision, generation, state,
                 json.dumps(sorted(set(ceiling)) if active else [])),
            )
        return {"name": name, "revision": revision, "generation": generation, "state": state}

    def active(self, name, revision, generation, *, current_capabilities):
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM extension_activations WHERE owner_id=? AND universe_id=? "
                "AND agent_id=? AND name=? AND revision=? AND generation=? AND state='active'",
                (*self.identity, name, revision, generation),
            ).fetchone()
        if row is None:
            raise ExtensionError("extension activation unavailable")
        return set(json.loads(row["ceiling_json"])) & set(current_capabilities)
