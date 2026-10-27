"""Broker-owned owner policy: action rules, once-approvals, egress, spending and
outside-client grants (N10, P0-3).

The policy database is never opened by the daemon or by a box. Every widening
(a policy write, a one-use approval) is called only by the broker's policy RPC
on the router's owner channel after it has received a protected-session proof;
the broker checks the live router row itself. The box channel reads, decides
and proposes. The binding gate for a box's OPEN is ``broker/authority_gate.py``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import stat
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from tinyassets.broker.owner_identities import validate_principal

_LOG = logging.getLogger(__name__)

BEHAVIOURS = ("do", "do_if_preapproved", "ask_first", "hand_off")
DEFAULT_RULES = {
    "workspace.files": "do", "workspace.shell": "do", "workspace.workflows": "do",
    "shell.egress": "do", "app.read": "do", "app.write": "do",
    "people.message": "ask_first", "commons.publish": "ask_first", "share": "ask_first",
    "browser.action": "ask_first", "spend": "ask_first", "money.move": "hand_off",
    "security.change": "hand_off", "access.grant": "hand_off",
}
OPERATION_KINDS = {"read": "app.read", "write": "app.write", "delete": "app.write",
                   "message": "people.message", "payment": "money.move",
                   "security": "security.change", "access": "access.grant"}
TABLES = {"rules", "approval_grants", "operation_kinds", "review_on", "review_off"}
SCHEMA_VERSION = 2
ONCE_TTL_S = 300
#: Held agent proposals: pending ones are never evicted; a full set refuses more.
PROPOSAL_TTL_S = 30 * 86400
PENDING_PROPOSALS = 200
#: Receipt kinds a box can mint at will; listed apart so they never push others out.
BOX_RECEIPT_KINDS = ("proposal", "decision")
#: P0-3 grant modes (decision A1): ``full`` is the owner's normal authority and the
#: default; ``scoped`` holds exact ``{universe, agent, capability}`` triples.
OUTSIDE_MODES = ("full", "scoped", "revoked")
OUTSIDE_SOURCES = ("mcp", "api_key")
#: An outside client's ``last_seen`` is coarse, so admission is a read most calls.
SEEN_INTERVAL_S = 60
#: A sign-in this close to a revoke counts as before it: the identity provider's
#: ``auth_time`` and the broker's clock need not agree to the second.
REVOKE_CLOCK_MARGIN_S = 5

def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class OwnerPolicy:
    owner: str
    generation: int
    digest: str
    document: dict

    def projection(self) -> dict:
        return {"owner": self.owner, "generation": self.generation,
                "digest": self.digest, "policy": self.document}


class PolicyRefused(PermissionError):
    """No valid interactive owner authority or the requested policy is invalid."""


def account_path(root: Path, owner: str, name: str) -> Path:
    """An owner's broker-private store: ``.broker/accounts/<sha256(owner)>/<name>``."""
    validate_principal(owner)
    account = hashlib.sha256(owner.encode()).hexdigest()
    return Path(root) / ".broker" / "accounts" / account / name


def open_account_db(path: Path, admissions, owner: str,
                    refused: type[Exception] = PermissionError) -> sqlite3.Connection:
    """Open one per-owner broker store: 0700 parents, an admitted owner, a 0600 single link.

    The account directory is created only by the policy bootstrap, which reads an
    existing directory as "not fresh". Every other store opens inside a bootstrapped
    account, so it can never make an owner's policy look missing.
    """
    if path.name != "policy.db" and not path.with_name("policy.db").is_file():
        raise refused("account store is not bootstrapped")
    broker = path.parent.parent.parent
    broker.mkdir(mode=0o700, exist_ok=True)
    path.parent.parent.mkdir(mode=0o700, exist_ok=True)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    from tinyassets.broker.admission import read

    try:
        read(admissions, owner).require_work()
    except (PermissionError, LookupError) as exc:
        raise refused("account store is not admitted") from exc
    if os.name == "posix":
        for directory in (broker, path.parent.parent, path.parent):
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) & 0o077):
                raise PermissionError("account store parent is not broker-private")
        if not path.exists():
            try:
                os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
            except FileExistsError:
                pass  # a racing first open made it; it is checked like any other
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise PermissionError("account store is not broker-private")
    db = sqlite3.connect(path, timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA synchronous=FULL")
    return db


def outside_scopes(value: object) -> list:
    """Exact ``{universe, agent, capability}`` triples; a wildcard is never a scope."""
    if (not isinstance(value, list) or len(value) > 64 or any(
            not isinstance(scope, dict) or set(scope) != {"universe", "agent", "capability"}
            or any(not isinstance(v, str) or not v or v == "*" or len(v) > 512
                   for v in scope.values())
            for scope in value)):
        raise PolicyRefused("outside scopes require exact universe, agent and capability")
    return value


def _outside_client(client: object, source: object = "mcp") -> None:
    if (not isinstance(client, str) or len(client) != 64
            or any(c not in "0123456789abcdef" for c in client)):
        raise PolicyRefused("invalid outside client")
    if source not in OUTSIDE_SOURCES:
        raise PolicyRefused("invalid outside client source")


def erase_account_db(path: Path) -> int:
    existed = path.exists()
    for suffix in ("", "-wal", "-shm", "-journal"):
        path.with_name(path.name + suffix).unlink(missing_ok=True)
    return int(existed)


class PolicyStore:
    """The broker's sole policy writer, with a receipt in the same transaction."""

    def __init__(self, data_root: Path, admissions) -> None:
        self._root = Path(data_root)
        self._admissions = admissions
        self._unavailable: dict[str, str] = {}
        self._locks = {}

    def lock(self, owner):
        return self._locks.setdefault(owner, threading.RLock())

    def _path(self, owner: str) -> Path:
        return account_path(self._root, owner, "policy.db")

    def _connect(self, owner: str, *, provision=False) -> sqlite3.Connection:
        if owner in self._unavailable:
            raise PolicyRefused(self._unavailable[owner])
        path = self._path(owner)
        fresh = not path.parent.exists()
        if provision and not fresh and not path.is_file():
            raise PolicyRefused("owner policy is missing")
        if not provision and not path.is_file():
            raise PolicyRefused("owner policy has not been bootstrapped")
        db = open_account_db(path, self._admissions, owner, PolicyRefused)
        db.executescript("""
            CREATE TABLE IF NOT EXISTS policy (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                generation INTEGER NOT NULL, digest TEXT NOT NULL,
                document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                kind TEXT NOT NULL, owner TEXT NOT NULL, session_id TEXT,
                old_digest TEXT, new_digest TEXT, request_digest TEXT,
                generation INTEGER, rule TEXT, decision TEXT, outcome TEXT);
            CREATE TABLE IF NOT EXISTS spend_reservations (
                request_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
                day TEXT NOT NULL, month TEXT NOT NULL, amount INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS once_approvals (
                token_hash TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
                session_id TEXT NOT NULL, generation INTEGER NOT NULL,
                expires_at REAL NOT NULL, consumed_at REAL);
            CREATE TABLE IF NOT EXISTS proposals (
                request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, document TEXT NOT NULL,
                at REAL NOT NULL, applied_at REAL);
            CREATE TABLE IF NOT EXISTS outside_clients (
                client TEXT PRIMARY KEY,
                source TEXT NOT NULL CHECK(source IN ('mcp','api_key')),
                mode TEXT NOT NULL CHECK(mode IN ('full','scoped','revoked')),
                scopes TEXT NOT NULL, generation INTEGER NOT NULL, revoked_at REAL,
                name TEXT NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL);
        """)
        document = {"entries": [], "egress": {"mode": "open", "destinations": []},
                    "spend": {"daily": None, "monthly": None, "transaction": None,
                              "ask_above": None, "source_ceilings": {}}}
        if provision and fresh:
            db.execute("INSERT OR IGNORE INTO policy VALUES (1,1,?,?)",
                       (_digest(document), _canonical(document)))
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        elif provision and db.execute("PRAGMA user_version").fetchone()[0] == 0:
            # One explicit provisioning upgrade, never a read-time default.
            # No retired egress source is opened or imported.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT generation,digest,document FROM policy WHERE singleton=1"
                             ).fetchone()
            if row is None:
                raise PolicyRefused("owner policy is missing")
            prior = json.loads(row["document"])
            if set(prior) != {"entries", "spend"} or _digest(prior) != row["digest"]:
                raise PolicyRefused("invalid legacy policy")
            prior["egress"] = document["egress"]
            self._validate(owner, prior)
            generation, digest = row["generation"] + 1, _digest(prior)
            db.execute("UPDATE policy SET generation=?,digest=?,document=? WHERE singleton=1",
                       (generation, digest, _canonical(prior)))
            db.execute("INSERT INTO receipts(at,kind,owner,old_digest,new_digest,generation,"
                       "outcome) VALUES (?,?,?,?,?,?,?)",
                       (time.time(), "provision", owner, row["digest"], digest, generation, "open"))
            db.execute("PRAGMA user_version=1")
            db.commit()
        if provision and db.execute("PRAGMA user_version").fetchone()[0] == 1:
            # N10: action rules and once-approvals moved here. Owners start from
            # the default rules; no box-held rule is read or imported.
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        return db

    def erase(self, owner: str) -> int:
        """Erase policy custody under terminal admission."""
        return erase_account_db(self._path(owner))

    def _session(self, owner: str, cookie: str | dict) -> str:
        from tinyassets.owner_runtime import refuse_router_store

        refuse_router_store("owner_sessions")
        if isinstance(cookie, dict) and set(cookie) == {"session_hash"}:
            session_hash = cookie["session_hash"]
            if (not isinstance(session_hash, str) or len(session_hash) != 64
                    or any(c not in "0123456789abcdef" for c in session_hash)):
                raise PolicyRefused("interactive owner session required")
        elif isinstance(cookie, str) and 32 <= len(cookie) <= 512:
            session_hash = hashlib.sha256(cookie.encode()).hexdigest()
        else:
            raise PolicyRefused("interactive owner session required")
        path = self._root / ".runtime" / "owner-sessions.db"
        if not path.is_file():
            raise PolicyRefused("interactive owner session unavailable")
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                row = db.execute(
                    "SELECT session_hash, identity_json FROM owner_sessions "
                    "WHERE session_hash=? AND expires_at>?",
                    (session_hash, time.time()),
                ).fetchone()
        except sqlite3.Error as exc:
            raise PolicyRefused("interactive owner session unavailable") from exc
        try:
            session_owner = json.loads(row[1]).get("user_id") if row else None
        except (TypeError, ValueError, AttributeError) as exc:
            raise PolicyRefused("interactive owner session unavailable") from exc
        if session_owner != owner:
            raise PolicyRefused("interactive owner session required")
        return row[0]

    def bootstrap(self) -> None:
        """Initialize owners independently; a failure refuses only that owner."""
        if self._admissions is None:
            return
        with self._admissions._connect(write=False) as db:
            owners = {row[0] for row in db.execute("SELECT principal FROM owner_identities")}
        for owner in sorted(owners):
            self.bootstrap_owner(owner)

    def bootstrap_owner(self, owner: str) -> None:
        try:
            with closing(self._connect(owner, provision=True)):
                pass
        except (PolicyRefused, OSError, sqlite3.Error, RuntimeError) as exc:
            reason = (str(exc) if isinstance(exc, PolicyRefused)
                      else "policy storage unavailable")
            self._unavailable[owner] = reason
            _LOG.error("ALARM: owner policy refused for %s: %s", owner, reason)

    def receipts(self, owner: str) -> list[dict]:
        """The last 50 receipts of each bucket: box-mintable kinds are counted apart."""
        columns = ("SELECT id,at,kind,old_digest,new_digest,request_digest,generation,rule,"
                   "decision,outcome FROM receipts WHERE kind {} (?,?) ORDER BY id DESC LIMIT 50")
        with closing(self._connect(owner)) as db:
            rows = [*db.execute(columns.format("NOT IN"), BOX_RECEIPT_KINDS),
                    *db.execute(columns.format("IN"), BOX_RECEIPT_KINDS)]
        rows.sort(key=lambda row: -row["id"])
        return [{key: row[key] for key in row.keys() if key != "id"} for row in rows]

    def read(self, owner: str) -> OwnerPolicy:
        with closing(self._connect(owner)) as db:
            row = db.execute("SELECT generation,digest,document FROM policy WHERE singleton=1"
                             ).fetchone()
        if row is None:
            raise PolicyRefused("owner policy has not been bootstrapped")
        document = json.loads(row["document"])
        if _digest(document) != row["digest"]:
            raise RuntimeError("policy digest mismatch")
        self._validate(owner, document)
        return OwnerPolicy(owner, row["generation"], row["digest"], document)

    def prepare_write(self, owner: str, cookie: str | dict, expected_generation: int,
                      document: dict) -> None:
        """Validate a policy write without committing it.

        Live public tunnels close before the row changes. A close that fails
        or times out must leave the generation and document untouched, so the
        broker checks the write first and commits only after those tunnels close.
        """
        self._session(owner, cookie)
        self._validate(owner, document)
        if type(expected_generation) is not int or expected_generation < 1:
            raise PolicyRefused("expected policy generation required")
        with closing(self._connect(owner)) as db:
            row = db.execute("SELECT generation FROM policy WHERE singleton=1").fetchone()
        if row is None:
            raise PolicyRefused("owner policy has not been bootstrapped")
        if row["generation"] != expected_generation:
            raise PolicyRefused("policy generation changed")

    def write(self, owner: str, cookie: str | dict, expected_generation: int,
              document: dict, proposal: dict | None = None) -> OwnerPolicy:
        """Commit a policy; with ``proposal``, only as that held card's one apply.

        ``proposal`` names the card (``request_id``) and the digest of the
        proposal the owner was shown. The held proposal must be pending, unexpired,
        carry exactly that digest and this document, and is marked applied in the
        same transaction, so it applies at most once.
        """
        session_id = self._session(owner, cookie)
        self._validate(owner, document)
        if type(expected_generation) is not int or expected_generation < 1:
            raise PolicyRefused("expected policy generation required")
        if proposal is not None and (
                not isinstance(proposal, dict) or set(proposal) != {"request_id", "digest"}
                or not all(isinstance(value, str) and 0 < len(value) <= 128
                           for value in proposal.values())):
            raise PolicyRefused("invalid proposal apply")
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT generation,digest FROM policy WHERE singleton=1").fetchone()
            if row is None:
                raise PolicyRefused("owner policy has not been bootstrapped")
            if row["generation"] != expected_generation:
                raise PolicyRefused("policy generation changed")
            if proposal is not None:
                held = db.execute(
                    "SELECT digest,document FROM proposals WHERE request_id=? "
                    "AND applied_at IS NULL AND at>?",
                    (proposal["request_id"], time.time() - PROPOSAL_TTL_S)).fetchone()
                change = json.loads(held["document"]) if held is not None else {}
                if (held is None or held["digest"] != proposal["digest"]
                        or change.get("policy") != document
                        or change.get("expected_generation") != expected_generation):
                    raise PolicyRefused("proposal is not the pending card shown")
                db.execute("UPDATE proposals SET applied_at=? WHERE request_id=?",
                           (time.time(), proposal["request_id"]))
            generation, digest = row["generation"] + 1, _digest(document)
            db.execute("UPDATE policy SET generation=?,digest=?,document=? WHERE singleton=1",
                       (generation, digest, _canonical(document)))
            db.execute("INSERT INTO receipts(at,kind,owner,session_id,old_digest,new_digest,"
                       "generation,outcome) VALUES (?,?,?,?,?,?,?,?)",
                       (time.time(), "write", owner, session_id, row["digest"], digest,
                        generation, "committed"))
            db.commit()
        return OwnerPolicy(owner, generation, digest, document)

    def receipt_volume(self, owner: str, session_id: str, *, snapshot_id: str,
                       generation: int, outcome: str) -> None:
        """Receipt a protected restore or checkpoint without storing the session proof."""
        if (not isinstance(snapshot_id, str) or not isinstance(session_id, str)
                or type(generation) is not int or outcome not in {"accepted", "committed"}):
            raise PolicyRefused("invalid restore receipt")
        with closing(self._connect(owner)) as db, db:
            row = db.execute("SELECT generation FROM policy WHERE singleton=1").fetchone()
            db.execute(
                "INSERT INTO receipts(at,kind,owner,session_id,request_digest,generation,"
                "rule,decision,outcome) VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), "restore", owner, session_id,
                 _digest({"snapshot_id": snapshot_id, "volume_generation": generation}),
                 row["generation"] if row else generation, "owner_restore", "allow", outcome))

    def receipt_refusal(self, owner: str, action: str, request: object) -> None:
        """Record denied mutation attempts without ever storing a cookie."""
        with closing(self._connect(owner)) as db, db:
            row = db.execute("SELECT generation FROM policy WHERE singleton=1").fetchone()
            db.execute("INSERT INTO receipts(at,kind,owner,request_digest,generation,rule,"
                       "decision,outcome) VALUES (?,?,?,?,?,?,?,?)",
                       (time.time(), "refusal", owner, _digest({"action": action,
                                                               "request": request}),
                        row["generation"] if row else 0, "interactive_owner_session",
                        "deny", "refused"))

    def receipt_proposal(self, owner: str, center: str, change: dict,
                         request_id: str | None = None) -> dict:
        """Receipt an agent's proposal; with its card's id, hold it for the owner.

        The router applies a held proposal only through an owner-session write.
        A card's held proposal is immutable: refiling its id with another document
        is refused (the same document again is a no-op). Pending proposals are
        never evicted; a full pending set refuses new ones, and only applied or
        expired proposals are pruned.
        """
        if not isinstance(change, dict) or len(_canonical(change)) > 16000:
            raise PolicyRefused("invalid policy proposal")
        if request_id is not None and (not isinstance(request_id, str) or not request_id
                                       or len(request_id) > 128):
            raise PolicyRefused("invalid policy proposal")
        policy = self.read(owner)
        digest = _digest(change)
        now = time.time()
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            if request_id is not None:
                db.execute("DELETE FROM proposals WHERE at<? OR (applied_at IS NOT NULL "
                           "AND request_id NOT IN (SELECT request_id FROM proposals "
                           "WHERE applied_at IS NOT NULL ORDER BY applied_at DESC LIMIT ?))",
                           (now - PROPOSAL_TTL_S, PENDING_PROPOSALS))
                held = db.execute("SELECT digest FROM proposals WHERE request_id=?",
                                  (request_id,)).fetchone()
                if held is not None and held["digest"] != digest:
                    raise PolicyRefused("a proposal is already held for this card")
                if held is None:
                    pending = db.execute("SELECT count(*) FROM proposals "
                                         "WHERE applied_at IS NULL").fetchone()[0]
                    if pending >= PENDING_PROPOSALS:
                        raise PolicyRefused("too many pending policy proposals")
                    db.execute("INSERT INTO proposals VALUES (?,?,?,?,NULL)",
                               (request_id, digest, _canonical(change), now))
            db.execute("INSERT INTO receipts(at,kind,owner,request_digest,generation,"
                       "rule,decision,outcome) VALUES (?,?,?,?,?,?,?,?)",
                       (now, "proposal", owner, digest, policy.generation,
                        "owner_policy", "ask", "requested"))
            db.commit()
        return {"request_digest": digest, "generation": policy.generation}

    def proposal(self, owner: str, request_id: object) -> dict | None:
        """The pending, unexpired proposal held under a card id, or None."""
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            return None
        with closing(self._connect(owner)) as db:
            row = db.execute("SELECT document FROM proposals WHERE request_id=? "
                             "AND applied_at IS NULL AND at>?",
                             (request_id, time.time() - PROPOSAL_TTL_S)).fetchone()
        return json.loads(row["document"]) if row is not None else None

    # -- P0-3: outside-client grants (decision A1) ------------------------------

    @staticmethod
    def _grant(row, authenticated_at) -> dict:
        """The grant a call is admitted under, or a refusal naming why."""
        if os.environ.get("TINYASSETS_OUTSIDE_DENY") == "1":
            raise PolicyRefused("outside clients are switched off")
        if row is None:
            raise PolicyRefused("outside client has no grant")
        if row["mode"] == "revoked":
            raise PolicyRefused("outside client revoked")
        if row["revoked_at"] is not None and (
                type(authenticated_at) is not int
                or authenticated_at <= row["revoked_at"] + REVOKE_CLOCK_MARGIN_S):
            # A credential signed in before a revoke never comes back with a re-grant.
            raise PolicyRefused("a fresh sign-in is required after a revoke")
        return {"mode": row["mode"], "scopes": json.loads(row["scopes"]),
                "generation": row["generation"]}

    def outside_admit(self, owner: str, client: str, source: str,
                      authenticated_at: int | None) -> dict:
        """Router only: the grant of the verified outside client making this call.

        An MCP client's first verified call records it ``full`` (A1). An API key's
        row is written with the key, so a key without one is refused, never widened.
        """
        _outside_client(client, source)
        now = time.time()
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM outside_clients WHERE client=?", (client,)).fetchone()
            if row is None and source == "mcp":
                db.execute("INSERT INTO outside_clients VALUES (?,?,'full','[]',1,NULL,'',?,?)",
                           (client, source, now, now))
                row = db.execute("SELECT * FROM outside_clients WHERE client=?",
                                 (client,)).fetchone()
            elif row is not None and row["source"] != source:
                raise PolicyRefused("outside client source changed")
            elif row is not None and now - row["last_seen"] >= SEEN_INTERVAL_S:
                db.execute("UPDATE outside_clients SET last_seen=? WHERE client=?",
                           (now, client))
            grant = self._grant(row, authenticated_at)
            db.commit()
        return grant

    def outside_check(self, owner: str, client: str, authenticated_at: int | None) -> dict:
        """The grant as it stands now; a read that never records a client."""
        _outside_client(client)
        with closing(self._connect(owner)) as db:
            row = db.execute("SELECT * FROM outside_clients WHERE client=?", (client,)).fetchone()
        return self._grant(row, authenticated_at)

    def outside_list(self, owner: str) -> list[dict]:
        with closing(self._connect(owner)) as db:
            rows = db.execute("SELECT * FROM outside_clients ORDER BY first_seen, client")
            return [{**dict(row), "scopes": json.loads(row["scopes"])} for row in rows]

    def outside_revoke(self, owner: str, client: str) -> dict:
        """Router only, a narrowing (no session proof): revoke one client's grant now.

        A key's revoke calls it, so durable work and OPEN stop with the key. A client
        with no grant has nothing to revoke; an already revoked one stays as it is.
        """
        _outside_client(client)
        now = time.time()
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM outside_clients WHERE client=?", (client,)).fetchone()
            if row is None or row["mode"] == "revoked":
                db.commit()
                return {"client": client, "revoked": row is not None,
                        "generation": row["generation"] if row else 0}
            generation = row["generation"] + 1
            db.execute("UPDATE outside_clients SET mode='revoked', scopes='[]', generation=?, "
                       "revoked_at=? WHERE client=?", (generation, now, client))
            db.execute("INSERT INTO receipts(at,kind,owner,request_digest,generation,rule,"
                       "decision,outcome) VALUES (?,?,?,?,?,?,?,?)",
                       (now, "outside_grant", owner, _digest({"client": client,
                                                               "mode": "revoked"}),
                        generation, client, "revoked", "committed"))
            db.commit()
        return {"client": client, "revoked": True, "generation": generation}

    def outside_set(self, owner: str, proof: str | dict, *, client: str, source: str,
                    mode: str, scopes: list, expected_generation: int, name: str = "",
                    proposal: dict | None = None) -> dict:
        """Owner widening (router only): one client's mode, from a protected session.

        ``expected_generation`` 0 creates the row, as a key's mint does. With
        ``proposal``, only as the one apply of an agent's held key request.
        """
        session_id = self._session(owner, proof)
        _outside_client(client, source)
        if mode not in OUTSIDE_MODES:
            raise PolicyRefused("invalid outside grant mode")
        outside_scopes(scopes)
        if (mode == "scoped") != bool(scopes):
            raise PolicyRefused("a scoped grant, and only a scoped one, names scopes")
        if type(expected_generation) is not int or expected_generation < 0:
            raise PolicyRefused("expected outside grant generation required")
        if not isinstance(name, str) or len(name) > 80:
            raise PolicyRefused("invalid outside client name")
        now = time.time()
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM outside_clients WHERE client=?", (client,)).fetchone()
            if (row["generation"] if row else 0) != expected_generation:
                raise PolicyRefused("outside grant changed")
            if row is not None and row["source"] != source:
                raise PolicyRefused("outside client source changed")
            if proposal is not None:
                self._apply_key_request(db, owner, proposal, client, mode, scopes, now)
            generation = expected_generation + 1
            revoked_at = now if mode == "revoked" else row["revoked_at"] if row else None
            db.execute("INSERT INTO outside_clients VALUES (?,?,?,?,?,?,?,?,?) "
                       "ON CONFLICT(client) DO UPDATE SET mode=excluded.mode, "
                       "scopes=excluded.scopes, generation=excluded.generation, "
                       "revoked_at=excluded.revoked_at, name=CASE WHEN excluded.name='' "
                       "THEN name ELSE excluded.name END",
                       (client, source, mode, _canonical(scopes), generation, revoked_at,
                        name, now, now))
            db.execute("INSERT INTO receipts(at,kind,owner,session_id,request_digest,"
                       "generation,rule,decision,outcome) VALUES (?,?,?,?,?,?,?,?,?)",
                       (now, "outside_grant", owner, session_id,
                        _digest({"client": client, "mode": mode, "scopes": scopes}),
                        generation, client, mode, "committed"))
            db.commit()
        return {"client": client, "mode": mode, "scopes": scopes, "generation": generation}

    @staticmethod
    def _apply_key_request(db, owner, proposal, client, mode, scopes, now) -> None:
        """The held key request the owner was shown, applied once with its grant."""
        from tinyassets.api_keys import ApiKeyStore, _client

        if (not isinstance(proposal, dict) or set(proposal) != {"request_id", "digest"}
                or not all(isinstance(v, str) and 0 < len(v) <= 128
                           for v in proposal.values())):
            raise PolicyRefused("invalid proposal apply")
        held = db.execute("SELECT digest,document FROM proposals WHERE request_id=? "
                          "AND applied_at IS NULL AND at>?",
                          (proposal["request_id"], now - PROPOSAL_TTL_S)).fetchone()
        change = json.loads(held["document"]).get("api_key") if held is not None else None
        if (held is None or held["digest"] != proposal["digest"] or not isinstance(change, dict)
                or mode != "scoped" or change.get("scopes") != scopes
                or not isinstance(change.get("request_ref"), str)
                or client != _client(ApiKeyStore.request_key_id(owner, change["request_ref"]))):
            raise PolicyRefused("proposal is not the pending card shown")
        db.execute("UPDATE proposals SET applied_at=? WHERE request_id=?",
                   (now, proposal["request_id"]))

    def receipt_egress(self, owner, generation, destination, decision):
        with closing(self._connect(owner)) as db, db:
            db.execute("INSERT INTO receipts(at,kind,owner,generation,rule,decision,outcome) "
                       "VALUES (?,?,?,?,?,?,?)", (time.time(), "egress", owner, generation,
                       _canonical(destination), decision, decision))

    def _validate(self, owner: str, document: dict) -> None:
        if not isinstance(document, dict) or set(document) != {"entries", "spend", "egress"}:
            raise PolicyRefused("invalid policy document")
        from tinyassets.broker.egress import normalize_destination

        egress = document["egress"]
        if (not isinstance(egress, dict) or set(egress) != {"mode", "destinations"}
                or egress["mode"] not in {"open", "allowlist", "ask"}
                or not isinstance(egress["destinations"], list)):
            raise PolicyRefused("invalid egress policy")
        for entry in egress["destinations"]:
            if (not isinstance(entry, dict) or set(entry) != {"host", "port"}
                    or normalize_destination(entry["host"], entry["port"]) != entry):
                raise PolicyRefused("invalid egress destination")
            from ipaddress import ip_address

            from tinyassets.storage.outbound_connections import (
                SsrfValidationError,
                _classify_global_address,
            )

            try:
                literal = ip_address(entry["host"])
            except ValueError:
                continue
            try:
                _classify_global_address(str(literal))
            except SsrfValidationError as exc:
                raise PolicyRefused("egress entries require public IP addresses") from exc
        if not isinstance(document["entries"], list):
            raise PolicyRefused("invalid policy entries")
        for entry in document["entries"]:
            if (not isinstance(entry, dict) or set(entry) != {"scope", "table", "values"}
                    or entry["scope"] != "account" or entry["table"] not in TABLES
                    or not isinstance(entry["values"], dict)):
                raise PolicyRefused("invalid scoped policy entry")
            values = entry["values"]
            if entry["table"] == "rules" and (values.get("behaviour") not in BEHAVIOURS
                                              or values.get("action_class") not in DEFAULT_RULES):
                raise PolicyRefused("invalid approval rule")
            if entry["table"] == "operation_kinds" and values.get("kind") not in OPERATION_KINDS:
                raise PolicyRefused("invalid declared operation")
        spend = document["spend"]
        if not isinstance(spend, dict) or set(spend) != {
                "daily", "monthly", "transaction", "ask_above", "source_ceilings"}:
            raise PolicyRefused("invalid spend policy")
        if any(value is not None and (type(value) is not int or value < 0)
               for key, value in spend.items() if key != "source_ceilings"):
            raise PolicyRefused("invalid spend cap")
        if (not isinstance(spend["source_ceilings"], dict)
                or any(not isinstance(key, str) or type(value) is not int or value < 0
                       for key, value in spend["source_ceilings"].items())):
            raise PolicyRefused("invalid source ceiling")

    def decide(self, owner: str, *, center: str, action_class: str,
               connection: str = "", operation: str = "", destination: str = "",
               amount: int = 0, source: str = "", agent: str = "*",
               request_id: str = "", approved: bool = False) -> dict:
        """Apply the owner's rules and the fixed floor, and receipt the decision.

        The box's advisory checks and the binding gate in OPEN call this same
        method, so they share one decision source. Amounts are minor currency
        units; a nonzero amount is reserved only after all rules permit it.
        """
        if action_class not in DEFAULT_RULES:
            raise PolicyRefused("unknown action class")
        if (type(amount) is not int or amount < 0
                or (amount and (not isinstance(request_id, str) or not request_id))):
            raise PolicyRefused("invalid spend amount")
        request = {"center": center, "action_class": action_class,
                   "connection": connection, "operation": operation,
                   "destination": destination, "amount": amount, "source": source,
                   "agent": agent, "request_id": request_id}
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT generation,digest,document FROM policy WHERE singleton=1"
                             ).fetchone()
            if row is None:
                raise PolicyRefused("owner policy has not been bootstrapped")
            policy = json.loads(row["document"])
            generation, digest = row["generation"], row["digest"]
            if _digest(policy) != digest:
                raise RuntimeError("policy digest mismatch")
            matches = []
            for entry in policy["entries"]:
                if entry["table"] != "rules" or entry["scope"] != "account":
                    continue
                values = entry["values"]
                if (values.get("action_class") == action_class
                        and (agent == "*" or values.get("agent", "main") == agent)
                        and values.get("connection", "") in ("", connection)
                        and values.get("operation", "") in ("", operation.upper())
                        and values.get("record_kind", "behavior") == "behavior"):
                    matches.append(entry)
            strictest = max(matches, key=lambda e: BEHAVIOURS.index(e["values"]["behaviour"]),
                            default=None)
            behaviour = (strictest["values"]["behaviour"] if strictest
                         else DEFAULT_RULES.get(action_class, "ask_first"))
            rule = (f"{strictest['scope']}:{strictest['values'].get('id', '')}"
                    if strictest else "uncovered")
            decision = {"do": "allow", "do_if_preapproved": "hold",
                        "ask_first": "ask", "hand_off": "deny"}[behaviour]
            if action_class in ("money.move", "spend") and amount == 0:
                decision, rule = "deny", "spend_amount_required"
            spend = policy["spend"]
            cap = spend["transaction"]
            source_cap = spend["source_ceilings"].get(source)
            if amount and ((cap is not None and amount > cap)
                           or (source_cap is not None and amount > source_cap)):
                decision, rule = "deny", "spend_cap"
            elif (amount and not approved and spend["ask_above"] is not None
                  and amount > spend["ask_above"]):
                if decision == "allow":
                    decision, rule = "ask", "spend_threshold"
            if amount and decision == "allow":
                clock = time.gmtime()
                day = time.strftime("%Y-%m-%d", clock)
                month = time.strftime("%Y-%m", clock)
                prior = db.execute(
                    "SELECT request_digest FROM spend_reservations WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if prior is not None and prior["request_digest"] != _digest(request):
                    decision, rule = "deny", "spend_request_reused"
                elif prior is None:
                    for period, key in ((day, "daily"), (month, "monthly")):
                        limit = spend[key]
                        if limit is None:
                            continue
                        column = "day" if key == "daily" else "month"
                        used = db.execute(
                            f"SELECT COALESCE(SUM(amount),0) FROM spend_reservations "
                            f"WHERE {column}=?", (period,),
                        ).fetchone()[0]
                        if used + amount > limit:
                            decision, rule = "deny", f"spend_{key}_cap"
                            break
                    if decision == "allow":
                        db.execute("INSERT INTO spend_reservations VALUES (?,?,?,?,?)",
                                   (request_id, _digest(request), day, month, amount))
            # A hold/ask never reserves. The P5 gate rechecks this generation
            # when an owner answer returns, before any dispatch is admitted.
            result = {"decision": decision, "behaviour": behaviour, "rule": rule,
                      "generation": generation, "policy_digest": digest,
                      "request_digest": _digest(request)}
            db.execute("INSERT INTO receipts(at,kind,owner,request_digest,generation,rule,"
                       "decision,outcome) VALUES (?,?,?,?,?,?,?,?)",
                       (time.time(), "decision", owner, result["request_digest"],
                        generation, rule, decision, "held" if decision in
                        ("ask", "hold") else "refused" if decision == "deny" else "allowed"))
            db.commit()
        return result

    def declared_kind(self, owner: str, connection: str, method: str,
                      path: str) -> str | None:
        """The owner's most severe declared kind for this call, or None."""
        policy = self.read(owner).document
        method = str(method or "").upper()
        path = str(path or "/").rstrip("/") or "/"
        matches = []
        for entry in policy["entries"]:
            if entry["table"] != "operation_kinds":
                continue
            values = entry["values"]
            prefix = str(values.get("path_prefix") or "/").rstrip("/") or "/"
            if (values.get("connection") == connection
                    and values.get("method", "") in ("", method)
                    and (prefix == "/" or path == prefix or path.startswith(prefix + "/"))):
                matches.append(values.get("kind"))
        severity = {"read": 0, "write": 1, "delete": 2, "message": 3,
                    "payment": 4, "security": 4, "access": 4}
        return max(matches, key=lambda kind: severity.get(kind, 4), default=None)

    def classify(self, owner: str, connection: str, method: str,
                 path: str) -> tuple[str, str]:
        """Declared HTTP meaning; undeclared calls conservatively count as writes."""
        kind = self.declared_kind(owner, connection, method, path)
        return OPERATION_KINDS.get(kind, "app.write"), str(method or "").upper()

    def receipt_gate(self, owner: str, request_digest: str, rule: str, decision: str) -> None:
        """One receipt for the binding gate's outcome on a box OPEN."""
        with closing(self._connect(owner)) as db, db:
            row = db.execute("SELECT generation FROM policy WHERE singleton=1").fetchone()
            db.execute("INSERT INTO receipts(at,kind,owner,request_digest,generation,rule,"
                       "decision,outcome) VALUES (?,?,?,?,?,?,?,?)",
                       (time.time(), "gate", owner, request_digest,
                        row["generation"] if row else 0, rule, decision,
                        "allowed" if decision == "allow" else "refused"))

    def issue_once(self, owner: str, proof: str | dict, expected_generation: int,
                   request_digest: str) -> dict:
        """Owner widening (router only): one use of exactly this request, this generation."""
        session_id = self._session(owner, proof)
        if (type(expected_generation) is not int or not isinstance(request_digest, str)
                or not request_digest.startswith("sha256:") or len(request_digest) != 71):
            raise PolicyRefused("invalid once approval")
        token = secrets.token_urlsafe(32)
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT generation FROM policy WHERE singleton=1").fetchone()
            if row is None or row["generation"] != expected_generation:
                raise PolicyRefused("policy changed before approval")
            db.execute("DELETE FROM once_approvals WHERE expires_at<?", (time.time(),))
            db.execute("INSERT INTO once_approvals VALUES (?,?,?,?,?,NULL)",
                       (hashlib.sha256(token.encode()).hexdigest(), request_digest,
                        session_id, expected_generation, time.time() + ONCE_TTL_S))
            db.execute("INSERT INTO receipts(at,kind,owner,session_id,request_digest,"
                       "generation,decision,outcome) VALUES (?,?,?,?,?,?,?,?)",
                       (time.time(), "once_approval", owner, session_id, request_digest,
                        expected_generation, "allow_once", "issued"))
            db.commit()
        return {"token": token, "generation": expected_generation}

    def consume_once(self, owner: str, token: object, request_digest: str) -> bool:
        """Spend one approval of exactly this request at the current generation."""
        if not isinstance(token, str) or not token or len(token) > 512:
            return False
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT generation FROM policy WHERE singleton=1").fetchone()
            if current is None:
                return False
            cursor = db.execute(
                "UPDATE once_approvals SET consumed_at=? WHERE token_hash=? "
                "AND request_digest=? AND generation=? AND expires_at>? "
                "AND consumed_at IS NULL",
                (time.time(), hashlib.sha256(token.encode()).hexdigest(), request_digest,
                 current["generation"], time.time()),
            )
            if cursor.rowcount != 1:
                return False
            db.execute("INSERT INTO receipts(at,kind,owner,request_digest,generation,"
                       "decision,outcome) VALUES (?,?,?,?,?,?,?)",
                       (time.time(), "once_approval", owner, request_digest,
                        current["generation"], "allow_once", "consumed"))
            db.commit()
        return True

    def standing_grant(self, owner: str, candidate: dict) -> str | None:
        """The decision id of a live site or always grant covering ``candidate``.

        A task-scoped grant never matches here: the task's identity is the box's
        assertion, so a call it would cover is asked again.
        """
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT generation,digest,document FROM policy WHERE singleton=1"
                             ).fetchone()
            if row is None:
                raise PolicyRefused("owner policy has not been bootstrapped")
            document = json.loads(row["document"])
            if _digest(document) != row["digest"]:
                raise RuntimeError("policy digest mismatch")
            for entry in document["entries"]:
                values = entry["values"]
                if (entry["table"] not in ("rules", "approval_grants")
                        or values.get("record_kind", "behavior") != "preapproval"):
                    continue
                try:
                    grant = json.loads(values.get("grant_json") or "{}")
                except (TypeError, ValueError):
                    continue
                if (not isinstance(grant, dict) or grant.get("revoked")
                        or any(grant.get(key) != value for key, value in candidate.items())
                        or grant.get("scope") not in ("site", "always")):
                    continue
                expires = grant.get("expires_at")
                if expires is not None and (type(expires) not in (int, float)
                                            or expires <= time.time()):
                    continue
                decision_id = str(values.get("decision_id") or values.get("id") or "")
                db.execute("INSERT INTO receipts(at,kind,owner,generation,rule,decision,outcome)"
                           " VALUES (?,?,?,?,?,?,?)",
                           (time.time(), "standing_grant", owner, row["generation"],
                            decision_id[:512], "allow", "matched"))
                db.commit()
                return decision_id
        return None
