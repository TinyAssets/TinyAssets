"""Durable per-client authority, independent of extension authorship.

Only verified resource-token claims identify a client and credential family.
Cutover is configured after the founder's AuthKit evidence check. A missing or
invalid durable switch denies all classified outside work across workers.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class OutsideRefused(PermissionError):
    pass


def origin(claims):
    """Called only after signature, issuer, audience and expiry verification."""
    claim = os.environ.get("TINYASSETS_OUTSIDE_CLIENT_CLAIM", "")
    if not claim:
        return None  # Explicit migration gate; never guess AuthKit claim semantics.
    client, issuer = claims.get(claim), claims.get("iss")
    family_claim = os.environ.get("TINYASSETS_OUTSIDE_FAMILY_CLAIM", "sid")
    family = claims.get(family_claim)
    if not all(isinstance(v, str) and 0 < len(v) <= 2048 for v in (client, issuer)):
        raise OutsideRefused("verified outside client/family evidence missing")
    try:
        first_party = json.loads(os.environ.get("TINYASSETS_FIRST_PARTY_CLIENTS", "[]"))
    except ValueError:
        raise OutsideRefused("invalid first-party client configuration") from None
    if not isinstance(first_party, list) or any(not isinstance(v, str) for v in first_party):
        raise OutsideRefused("invalid first-party client configuration")
    if issuer + "|" + client in first_party:
        return None
    if not isinstance(family, str) or not 0 < len(family) <= 2048:
        raise OutsideRefused("verified outside credential family missing")
    authenticated_at = claims.get("auth_time")
    if type(authenticated_at) is not int or authenticated_at <= 0:
        raise OutsideRefused("verified interactive authentication time missing")
    return {"client": hashlib.sha256((issuer + "\0" + client).encode()).hexdigest(),
            "family": hashlib.sha256((issuer + "\0" + family).encode()).hexdigest(),
            "authenticated_at": authenticated_at}


class OutsideClientAuthority:
    def __init__(self, base):
        self.path = Path(base) / ".outside-client-authority.sqlite3"

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS outside_switch (id INTEGER PRIMARY KEY, enabled INTEGER);
                CREATE TABLE IF NOT EXISTS outside_families (
                  owner TEXT, client TEXT, family TEXT, retired INTEGER NOT NULL DEFAULT 0,
                  authenticated_at INTEGER NOT NULL,
                  PRIMARY KEY(owner,client,family));
                CREATE TABLE IF NOT EXISTS outside_revocations (
                  owner TEXT, client TEXT, revoked_at REAL NOT NULL, PRIMARY KEY(owner,client));
                CREATE TABLE IF NOT EXISTS outside_grants (
                  owner TEXT, client TEXT, generation INTEGER NOT NULL, family TEXT NOT NULL,
                  scopes TEXT NOT NULL, revoked INTEGER NOT NULL,
                  PRIMARY KEY(owner,client));
                CREATE TABLE IF NOT EXISTS outside_effects (
                  lease TEXT PRIMARY KEY, owner TEXT, client TEXT, generation INTEGER,
                  state TEXT NOT NULL, admitted_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS outside_audit (
                  owner TEXT, client TEXT, generation INTEGER, action TEXT,
                  at TEXT DEFAULT CURRENT_TIMESTAMP);
            """)
            yield conn

    def observe(self, owner, bound):
        with self.db() as conn:
            conn.execute("INSERT OR IGNORE INTO outside_families VALUES (?,?,?,0,?)",
                         (owner, bound["client"], bound["family"], bound["authenticated_at"]))

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("outside switch requires boolean")
        with self.db() as conn:
            conn.execute("INSERT OR REPLACE INTO outside_switch VALUES (1,?)", (int(enabled),))

    def change(self, owner, client, *, expected_generation, family, scopes, revoke=False):
        if type(expected_generation) is not int or expected_generation < 0:
            raise ValueError("expected outside grant generation required")
        if (not isinstance(scopes, list) or any(not isinstance(s, dict) or set(s) != {
                "universe", "agent", "capability"} or any(
                    not isinstance(v, str) or not v or v == "*" for v in s.values())
                for s in scopes)):
            raise ValueError("outside scopes require exact universe, agent and capability")
        with self.db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM outside_grants WHERE owner=? AND client=?",
                               (owner, client)).fetchone()
            if (old["generation"] if old else 0) != expected_generation:
                raise OutsideRefused("outside grant changed")
            observed = conn.execute("SELECT retired,authenticated_at FROM outside_families "
                                    "WHERE owner=? "
                                    "AND client=? AND family=?", (owner, client, family)).fetchone()
            fence = conn.execute("SELECT revoked_at FROM outside_revocations WHERE owner=? "
                                 "AND client=?", (owner, client)).fetchone()
            if not revoke and (observed is None or observed[0]
                               or fence is not None and observed[1] <= fence[0]):
                raise OutsideRefused("fresh verified credential family required")
            if revoke:
                if old is None:
                    raise OutsideRefused("outside grant absent")
                conn.execute("UPDATE outside_families SET retired=1 WHERE owner=? AND client=?",
                             (owner, client))
                conn.execute("INSERT OR REPLACE INTO outside_revocations VALUES (?,?,?)",
                             (owner, client, time.time()))
                family = old["family"]
            generation = expected_generation + 1
            conn.execute("INSERT OR REPLACE INTO outside_grants VALUES (?,?,?,?,?,?)",
                         (owner, client, generation, family,
                          json.dumps([] if revoke else scopes), int(revoke)))
            conn.execute("INSERT INTO outside_audit(owner,client,generation,action) "
                         "VALUES (?,?,?,?)",
                         (owner, client, generation, "revoke" if revoke else "grant"))
        return {"client": client, "generation": generation, "revoked": revoke}

    def inspect(self, owner):
        with self.db() as conn:
            return {"clients": [dict(row) for row in conn.execute(
                "SELECT client,generation,family,scopes,revoked FROM outside_grants WHERE owner=?",
                (owner,))], "families": [dict(row) for row in conn.execute(
                    "SELECT client,family,retired FROM outside_families WHERE owner=?", (owner,))]}

    def admit(self, owner, bound, *, universe=None, agent=None, capability=None):
        try:
            with self.db() as conn:
                switch = conn.execute("SELECT enabled FROM outside_switch WHERE id=1").fetchone()
                row = conn.execute("SELECT * FROM outside_grants WHERE owner=? AND client=?",
                                   (owner, bound["client"])).fetchone()
                if (os.environ.get("TINYASSETS_OUTSIDE_DENY") == "1" or switch is None
                        or switch[0] != 1 or row is None or row["revoked"]
                        or row["family"] != bound["family"]
                        or bound.get("generation", row["generation"]) != row["generation"]):
                    raise OutsideRefused("outside client authority unavailable")
                scopes = json.loads(row["scopes"])
                if capability is not None and {"universe": universe, "agent": agent,
                                               "capability": capability} not in scopes:
                    raise OutsideRefused("outside client capability not granted")
                return {**bound, "generation": row["generation"]}
        except (sqlite3.Error, ValueError, KeyError, OSError):
            raise OutsideRefused("outside authority store unavailable") from None


def current_store():
    from tinyassets.api.helpers import _base_path

    return OutsideClientAuthority(_base_path())


def check_identity(identity, *, universe=None, agent=None, capability=None):
    bound = identity.metadata.get("outside_origin")
    if bound is not None:
        current_store().admit(identity.user_id, bound, universe=universe,
                              agent=agent, capability=capability)


@contextmanager
def effect_admission():
    """Admit once under a short transaction; in-flight outcomes are never replayed."""
    from tinyassets.auth.middleware import current_identity_or_none

    identity = current_identity_or_none()
    bound = identity.metadata.get("outside_origin") if identity is not None else None
    if bound is None:
        yield
        return
    import uuid

    store = current_store()
    check_identity(identity)
    lease = uuid.uuid4().hex
    with store.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM outside_grants WHERE owner=? AND client=?",
                           (identity.user_id, bound["client"])).fetchone()
        enabled = conn.execute("SELECT enabled FROM outside_switch WHERE id=1").fetchone()
        if (row is None or row["revoked"] or row["generation"] != bound["generation"]
                or row["family"] != bound["family"] or enabled is None or enabled[0] != 1
                or os.environ.get("TINYASSETS_OUTSIDE_DENY") == "1"):
            raise OutsideRefused("outside effect authority changed")
        conn.execute("INSERT INTO outside_effects VALUES (?,?,?,?,?,?)",
                     (lease, identity.user_id, bound["client"], bound["generation"],
                      "in_flight", time.time()))
    try:
        yield
    except BaseException:
        with store.db() as conn:
            conn.execute("UPDATE outside_effects SET state='unknown' WHERE lease=?", (lease,))
        raise
    else:
        with store.db() as conn:
            conn.execute("UPDATE outside_effects SET state='finished' WHERE lease=?", (lease,))


def check_mcp_request(identity, document):
    if not isinstance(document, dict):
        raise OutsideRefused("outside batches unavailable")
    check_identity(identity)
    method = document.get("method")
    if method in {"initialize", "notifications/initialized", "ping", "tools/list"}:
        return
    if method != "tools/call":
        raise OutsideRefused("outside protocol method unavailable")
    params = document.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("arguments", {}), dict):
        raise OutsideRefused("invalid outside tool request")
    arguments = params.get("arguments", {})
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import get_founder_home

    scopes = {arguments[k] for k in ("universe_id", "graph_id", "command_center_id")
              if arguments.get(k)}
    agents = {arguments[k] for k in ("agent_id", "agent_binding_id") if arguments.get(k)}
    indirect = {"run_id", "branch_def_id", "branch_id", "branch_version_id", "automation_id",
                "agent_definition_id", "source_id", "goal_id"}
    if any(arguments.get(k) for k in indirect) and not scopes:
        raise OutsideRefused("outside resource calls require an explicit universe")
    if arguments.get("webhook_op") == "mint" or arguments.get("source_op") == "create":
        raise OutsideRefused("channel credentials require the protected owner surface")
    if len(scopes) > 1 or len(agents) > 1:
        raise OutsideRefused("ambiguous outside resource scope")
    universe = next(iter(scopes), None) or get_founder_home(_base_path(), identity.user_id)
    agent = next(iter(agents), "main")
    check_identity(identity, universe=universe, agent=agent, capability=params.get("name", ""))
    identity.metadata["outside_resource"] = {"universe": universe, "agent": agent,
                                             "capability": params.get("name", "")}


def captured_identity(owner):
    """Private run admission marker, written atomically with the queued run."""
    from tinyassets.auth.middleware import current_identity_or_none

    identity = current_identity_or_none()
    if identity is None or identity.metadata.get("outside_origin") is None:
        return None
    if identity.user_id != owner:
        raise OutsideRefused("outside run owner changed")
    check_identity(identity)
    return json.dumps({"owner": owner, "bound": identity.metadata["outside_origin"],
                       "capabilities": identity.capabilities})


@contextmanager
def run_identity(base, run_id):
    """A restarted/queued/resumed run cannot become unrestricted owner work."""
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.runs import _connect

    with _connect(base) as conn:
        row = conn.execute("SELECT owner_user_id,outside_origin_json FROM runs WHERE run_id=?",
                           (run_id,)).fetchone()
    if row is None or row[1] is None:
        yield
        return
    document = json.loads(row[1])
    if document["owner"] != row[0]:
        raise OutsideRefused("outside run owner changed")
    identity = Identity(document["owner"], document["owner"],
                        capabilities=document["capabilities"],
                        metadata={"outside_origin": document["bound"]})
    check_identity(identity)
    with identity_context(identity):
        yield


@contextmanager
def stored_identity(raw, owner):
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    if raw is None:
        yield
        return
    document = json.loads(raw)
    if document["owner"] != owner:
        raise OutsideRefused("outside saved work owner changed")
    identity = Identity(owner, owner, capabilities=document["capabilities"],
                        metadata={"outside_origin": document["bound"]})
    check_identity(identity)
    with identity_context(identity):
        yield


@contextmanager
def automation_identity(base, automation):
    from tinyassets.automations import AutomationStore

    conn = AutomationStore(base)._connect(create=False)
    if conn is None:
        raise OutsideRefused("automation authority unavailable")
    try:
        row = conn.execute("SELECT outside_origin_json FROM automations WHERE automation_id=?",
                           (automation.automation_id,)).fetchone()
    finally:
        conn.close()
    # Unsaved automation objects are used by trusted admission checks.
    with stored_identity(row[0] if row else None, automation.owner_principal_id):
        yield


def check_resource(universe):
    """Check the resolved object scope, including indirect IDs and mixed reads."""
    from tinyassets.auth.middleware import current_identity_or_none

    identity = current_identity_or_none()
    if identity is None or identity.metadata.get("outside_origin") is None:
        return
    requested = identity.metadata.get("outside_resource")
    if requested is not None:
        if requested["universe"] != universe:
            raise OutsideRefused("outside resource is not in the admitted universe")
        check_identity(identity, **requested)
    else:
        check_identity(identity)
