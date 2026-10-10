"""Owner-issued REST credentials in the existing protected outside authority store."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from contextlib import contextmanager

from tinyassets.outside_authority import OutsideClientAuthority, OutsideRefused

LEVELS = frozenset({"read", "message", "control", "costly"})
TOKEN = re.compile(r"ta_key_[A-Za-z0-9_-]{43}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
LIMIT = 60


class InvalidKey(PermissionError):
    pass


class RateLimited(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after


def current_store():
    from tinyassets.api.helpers import _base_path

    return KeyStore(_base_path())


class KeyStore(OutsideClientAuthority):
    @contextmanager
    def keys(self):
        conn = None
        try:
            with self.db() as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS api_keys (
                key_id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
                digest TEXT UNIQUE NOT NULL, scopes TEXT NOT NULL, generation INTEGER NOT NULL,
                created_at REAL NOT NULL, last_used_at REAL, revoked_at REAL,
                window INTEGER NOT NULL DEFAULT 0, calls INTEGER NOT NULL DEFAULT 0)""")
                yield conn
        finally:
            if conn is not None:
                conn.close()

    def owned(self, owner, center):
        from tinyassets.universe_owner import owner_of

        if not isinstance(center, str) or not IDENTIFIER.fullmatch(center):
            raise ValueError("invalid command center")
        if owner_of(self.path.parent, center) != owner:
            raise OutsideRefused("command center must belong to this owner")

    def validate(self, owner, name, scopes):
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ValueError("name must contain 1-80 characters")
        if not isinstance(scopes, list) or not 1 <= len(scopes) <= 50:
            raise ValueError("select 1-50 command centers")
        seen = set()
        for scope in scopes:
            if not isinstance(scope, dict) or set(scope) != {
                    "command_center_id", "agents", "levels"}:
                raise ValueError("invalid scope")
            center, agents, levels = (scope[k] for k in ("command_center_id", "agents", "levels"))
            self.owned(owner, center)
            if center in seen:
                raise ValueError("duplicate command center")
            seen.add(center)
            if (not isinstance(agents, list) or not 1 <= len(agents) <= 100
                    or any(not isinstance(a, str) or (a != "*" and not IDENTIFIER.fullmatch(a))
                           for a in agents) or ("*" in agents and agents != ["*"])):
                raise ValueError("select exact agent ids or all agents")
            if (not isinstance(levels, list) or not levels
                    or any(not isinstance(v, str) or v not in LEVELS for v in levels)):
                raise ValueError("invalid levels")
        return name.strip(), json.dumps(scopes)

    @staticmethod
    def public(row):
        return {k: json.loads(row[k]) if k == "scopes" else row[k] for k in (
            "key_id", "name", "scopes", "generation", "created_at", "last_used_at", "revoked_at")}

    def create(self, owner, name, scopes):
        name, encoded = self.validate(owner, name, scopes)
        secret = "ta_key_" + secrets.token_urlsafe(32)
        key_id = secrets.token_hex(16)
        with self.keys() as conn:
            conn.execute("INSERT INTO api_keys "
                         "(key_id,owner,name,digest,scopes,generation,created_at) "
                         "VALUES (?,?,?,?,?,1,?)",
                         (key_id, owner, name, hashlib.sha256(secret.encode()).hexdigest(),
                          encoded, time.time()))
            row = conn.execute("SELECT * FROM api_keys WHERE key_id=?", (key_id,)).fetchone()
            return {**self.public(row), "secret": secret}

    def inspect_keys(self, owner):
        with self.keys() as conn:
            return [self.public(r) for r in conn.execute(
                "SELECT * FROM api_keys WHERE owner=? ORDER BY created_at DESC", (owner,))]

    def change_key(self, owner, key_id, generation, *, name=None, scopes=None, revoke=False):
        if type(generation) is not int or generation < 1:
            raise ValueError("expected_generation required")
        if not revoke:
            name, scopes = self.validate(owner, name, scopes)
        with self.keys() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM api_keys WHERE key_id=? AND owner=?",
                               (key_id, owner)).fetchone()
            if row is None or row["generation"] != generation or row["revoked_at"] is not None:
                raise OutsideRefused("key absent, revoked or changed; refresh")
            if revoke:
                conn.execute("UPDATE api_keys SET revoked_at=?,generation=generation+1 "
                             "WHERE key_id=?",
                             (time.time(), key_id))
            else:
                conn.execute("UPDATE api_keys SET name=?,scopes=?,generation=generation+1 "
                             "WHERE key_id=?", (name, scopes, key_id))
            return self.public(conn.execute("SELECT * FROM api_keys WHERE key_id=?",
                                            (key_id,)).fetchone())

    def authenticate(self, secret):
        from tinyassets.auth.provider import Identity

        if not TOKEN.fullmatch(secret):
            raise InvalidKey("invalid API key")
        now = time.time()
        with self.keys() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM api_keys WHERE digest=? AND revoked_at IS NULL",
                               (hashlib.sha256(secret.encode()).hexdigest(),)).fetchone()
            if row is None:
                raise InvalidKey("invalid API key")
            bound = {"api_key": row["key_id"], "generation": row["generation"]}
            self.live(conn, row["owner"], bound)
            window = int(now // 60)
            count = row["calls"] if row["window"] == window else 0
            if count >= LIMIT:
                raise RateLimited(max(1, 60 - int(now % 60)))
            conn.execute("UPDATE api_keys SET last_used_at=?,window=?,calls=? WHERE key_id=?",
                         (now, window, count + 1, row["key_id"]))
            return Identity(row["owner"], row["owner"], capabilities=["read", "write", "costly"],
                            metadata={"outside_origin": bound})

    def live(self, conn, owner, bound):
        if os.environ.get("TINYASSETS_OUTSIDE_DENY") == "1":
            raise OutsideRefused("outside work is disabled")
        row = conn.execute("SELECT * FROM api_keys WHERE key_id=? AND owner=?",
                           (bound["api_key"], owner)).fetchone()
        if (row is None or row["revoked_at"] is not None
                or row["generation"] != bound["generation"]):
            raise OutsideRefused("API key revoked or changed")
        return row

    def authorize(self, owner, bound, center, agent, levels):
        with self.keys() as conn:
            row = self.live(conn, owner, bound)
            self._scope(row, bound, center, agent, levels)

    def _scope(self, row, bound, center, agent, levels):
        self.owned(row["owner"], center)
        if bound.get("universe") not in (None, center) or (
                agent != "*" and bound.get("agent") not in (None, "*", agent)):
            raise OutsideRefused("saved API key work cannot change its scope")
        for scope in json.loads(row["scopes"]):
            if (scope["command_center_id"] == center and ("*" in scope["agents"]
                    or agent in scope["agents"]) and set(levels) <= set(scope["levels"])):
                return
        raise OutsideRefused("API key scope does not allow this action")

    def admit_key(self, owner, bound, *, universe=None, agent=None, capability=None):
        from tinyassets.ta_capabilities import _is_read

        with self.keys() as conn:
            row = self.live(conn, owner, bound)
            if capability is None:
                if bound.get("universe"):
                    self.owned(owner, bound["universe"])
                return bound
            center = universe or bound.get("universe")
            agent = agent or bound.get("agent", "main")
            if capability == "converse":
                levels = {"message"}
            elif capability in {"rest:conversation", "ta:catalog"}:
                levels = {"read"}
            elif _is_read(capability) or capability == "read":
                levels = {"read"}
                # Existing shared reads do not prove per-agent provenance.
                if capability in {"read_graph", "get_status", "read"}:
                    agent = "*"
            else:
                # Unknown effects cannot assume they are free or read-only.
                levels = {"control", "costly"}
                if capability in {"write_graph", "run_graph", "write", "edit", "bash"}:
                    agent = "*"
            self._scope(row, bound, center, agent, levels)
            return bound

    @contextmanager
    def effect(self, identity):
        bound = identity.metadata["outside_origin"]
        if not bound.get("universe"):
            raise OutsideRefused("API key effect requires a bound command center")
        lease = secrets.token_hex(16)
        with self.keys() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = self.live(conn, identity.user_id, bound)
            self._scope(row, bound, bound.get("universe"), bound.get("agent", "main"),
                        {"control", "costly"})
            conn.execute("INSERT INTO outside_effects VALUES (?,?,?,?,?,?)",
                         (lease, identity.user_id, "api:" + bound["api_key"],
                          bound["generation"], "in_flight", time.time()))
        try:
            yield
        except BaseException:
            with self.keys() as conn:
                conn.execute("UPDATE outside_effects SET state='unknown' WHERE lease=?", (lease,))
            raise
        else:
            with self.keys() as conn:
                conn.execute("UPDATE outside_effects SET state='finished' WHERE lease=?", (lease,))
