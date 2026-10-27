"""Broker-owned owner authority: rules, approvals, consents, egress and spending (N10).

The policy database is never opened by the daemon or by a box. Every widening
(a policy write, a one-use approval, a consent grant) is called only by the
broker's policy RPC on the router's owner channel after it has received a
protected-session proof; the broker checks the live router row itself. The box
channel reads, previews, decides and narrows. The binding gate for a box's
OPEN is ``broker/authority_gate.py``.
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
    # N10: an inference call on an owner-declared model endpoint. Spend caps
    # govern it; it is never judged as an app write.
    "model.call": "do",
}
OPERATION_KINDS = {"read": "app.read", "write": "app.write", "delete": "app.write",
                   "model": "model.call",
                   "message": "people.message", "payment": "money.move",
                   "security": "security.change", "access": "access.grant"}
TABLES = {"rules", "approval_grants", "operation_kinds", "review_on", "review_off"}
#: The import of box-held authority (N10 cutover): pending once, then done or none.
IMPORT_STATES = ("none", "pending", "done")
#: A pending import this long after its first lift request ends with the
#: defaults and an ALARM. An owner who never wakes is never failed by it.
IMPORT_DEADLINE_S = 30 * 86400
CALL_SINK = "authenticated_external_call"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode()).hexdigest()


def _validate_entry(entry: object) -> None:
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


def model_kinds(connection_id: str, endpoints: object) -> list[dict]:
    """``operation_kinds`` entries declaring a connection's POST endpoints model calls.

    Written only by an owner act on the router (model connect) or by the v1->v2
    migration, so the box can never classify a call as a model call itself.
    """
    entries = []
    for endpoint in endpoints if isinstance(endpoints, list) else []:
        path = endpoint.get("path_template") if isinstance(endpoint, dict) else None
        methods = endpoint.get("methods") if isinstance(endpoint, dict) else None
        if (isinstance(path, str) and path.startswith("/") and isinstance(methods, list)
                and "POST" in methods):
            entries.append({"scope": "account", "table": "operation_kinds", "values": {
                "connection": connection_id, "method": "POST", "path_prefix": path,
                "kind": "model"}})
    return entries


def _consent_key(sink: object, destination: object) -> tuple[str, str]:
    if (not isinstance(sink, str) or not isinstance(destination, str)
            or not sink.strip() or not destination.strip()
            or len(sink) > 128 or len(destination) > 1024):
        raise PolicyRefused("invalid consent")
    return sink, destination


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


class ImportPending(PolicyRefused):
    """Retryable: boxhostd has not yet lifted this owner's old authority."""


class PolicyStore:
    """The broker's sole policy writer, with a receipt in the same transaction."""

    def __init__(self, data_root: Path, admissions) -> None:
        self._root = Path(data_root)
        self._admissions = admissions
        self._unavailable: dict[str, str] = {}
        self._locks = {}
        self._import_settled: set[str] = set()

    def lock(self, owner):
        return self._locks.setdefault(owner, threading.RLock())

    def _path(self, owner: str) -> Path:
        validate_principal(owner)
        account = hashlib.sha256(owner.encode()).hexdigest()
        return self._root / ".broker" / "accounts" / account / "policy.db"

    def _connect(self, owner: str, *, provision=False) -> sqlite3.Connection:
        if owner in self._unavailable:
            raise PolicyRefused(self._unavailable[owner])
        path = self._path(owner)
        fresh = not path.parent.exists()
        if provision and not fresh and not path.is_file():
            raise PolicyRefused("owner policy is missing")
        if not provision and not path.is_file():
            raise PolicyRefused("owner policy has not been bootstrapped")
        broker = path.parent.parent.parent
        broker.mkdir(mode=0o700, exist_ok=True)
        path.parent.parent.mkdir(mode=0o700, exist_ok=True)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        from tinyassets.broker.admission import read

        try:
            read(self._admissions, owner).require_work()
        except (PermissionError, LookupError) as exc:
            raise PolicyRefused("account policy is not admitted") from exc
        if os.name == "posix":
            for directory in (broker, path.parent.parent, path.parent):
                info = directory.lstat()
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) & 0o077):
                    raise PermissionError("policy parent is not broker-private")
            if path.exists():
                info = path.lstat()
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                    raise PermissionError("policy database is not broker-private")
            else:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
        db = sqlite3.connect(path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
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
            CREATE TABLE IF NOT EXISTS consents (
                sink TEXT NOT NULL, destination TEXT NOT NULL, granted_at REAL NOT NULL,
                granted_by TEXT NOT NULL, revoked_at REAL, PRIMARY KEY (sink, destination));
            CREATE TABLE IF NOT EXISTS import_state (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL,
                since REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
                notice TEXT NOT NULL DEFAULT '', consented TEXT NOT NULL DEFAULT '[]');
        """)
        document = {"entries": [], "egress": {"mode": "open", "destinations": []},
                    "spend": {"daily": None, "monthly": None, "transaction": None,
                              "ask_above": None, "source_ceilings": {}}}
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if provision and fresh:
            db.execute("INSERT OR IGNORE INTO policy VALUES (1,1,?,?)",
                       (_digest(document), _canonical(document)))
            db.execute("INSERT OR IGNORE INTO import_state VALUES (1,'none',0,0,'','[]')")
            db.execute("PRAGMA user_version=2")
            version = 2
        if provision and version == 0:
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
            version = 1
        if provision and version == 1:
            # N10: an owner admitted before authority moved here imports its
            # box-held rules and consents once, lifted by boxhostd on its next
            # wake. Its model sources keep working: before N10 no model call was
            # consent-gated, so each live model grant's connection is consented
            # and its inference endpoints declared model calls. The owner is
            # shown that list (``import_notice``) to review or revoke.
            db.execute("BEGIN IMMEDIATE")
            grants = self._model_grants(owner)
            consented = sorted({grant["destination"] for grant in grants})
            row = db.execute("SELECT generation,digest,document FROM policy WHERE singleton=1"
                             ).fetchone()
            if row is None:
                raise PolicyRefused("owner policy is missing")
            policy = json.loads(row["document"])
            kinds = [entry for grant in grants
                     for entry in model_kinds(grant["connection_id"], grant["endpoints"])]
            if kinds:
                policy["entries"] = policy["entries"] + [
                    entry for entry in kinds if entry not in policy["entries"]]
                self._validate(owner, policy)
                db.execute("UPDATE policy SET generation=?,digest=?,document=? "
                           "WHERE singleton=1", (row["generation"] + 1, _digest(policy),
                                                 _canonical(policy)))
            db.execute("INSERT OR REPLACE INTO import_state VALUES (1,'pending',0,0,'',?)",
                       (_canonical(consented),))
            for destination in consented:
                db.execute("INSERT OR IGNORE INTO consents VALUES (?,?,?,?,NULL)",
                           (CALL_SINK, destination, time.time(), "migration"))
            db.execute("INSERT INTO receipts(at,kind,owner,rule,outcome) VALUES (?,?,?,?,?)",
                       (time.time(), "import", owner,
                        _canonical({"consented": len(consented),
                                    "destinations": consented})[:512], "pending"))
            db.execute("PRAGMA user_version=2")
            db.commit()
        return db

    def _model_grants(self, owner: str) -> list[dict]:
        """This owner's live model grants, read from the ledger once at migration."""
        path = self._root / ".broker" / "outbound.db"
        if not path.is_file():
            return []
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as ledger:
            try:
                rows = ledger.execute(
                    "SELECT DISTINCT c.destination, c.connection_id, c.allowed_endpoints_json "
                    "FROM outbound_connections c "
                    "JOIN outbound_connection_grants g ON g.connection_id=c.connection_id "
                    "WHERE c.owner_user_id=? AND c.revoked_at IS NULL "
                    "AND g.revoked_at IS NULL AND g.inference_required=1", (owner,)).fetchall()
            except sqlite3.OperationalError:
                return []  # A ledger from before model metering holds no model grant.
        grants = []
        for destination, connection_id, endpoints in rows:
            if not (isinstance(destination, str) and destination
                    and isinstance(connection_id, str) and connection_id):
                continue
            try:
                parsed = json.loads(endpoints or "[]")
            except (TypeError, ValueError):
                parsed = []
            grants.append({"destination": destination, "connection_id": connection_id,
                           "endpoints": parsed})
        return grants

    def erase(self, owner: str) -> int:
        """Erase policy custody under terminal admission."""
        path = self._path(owner)
        self._import_settled.discard(owner)
        existed = path.exists()
        for suffix in ("", "-wal", "-shm", "-journal"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)
        return int(existed)

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
        self._import_settled.discard(owner)
        try:
            with closing(self._connect(owner, provision=True)):
                pass
        except (PolicyRefused, OSError, sqlite3.Error, RuntimeError) as exc:
            reason = (str(exc) if isinstance(exc, PolicyRefused)
                      else "policy storage unavailable")
            self._unavailable[owner] = reason
            _LOG.error("ALARM: owner policy refused for %s: %s", owner, reason)

    def receipts(self, owner: str) -> list[dict]:
        with closing(self._connect(owner)) as db:
            rows = db.execute(
                "SELECT at,kind,old_digest,new_digest,request_digest,generation,rule,"
                "decision,outcome FROM receipts ORDER BY id DESC LIMIT 50").fetchall()
        return [dict(row) for row in rows]

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
              document: dict) -> OwnerPolicy:
        session_id = self._session(owner, cookie)
        self._validate(owner, document)
        if type(expected_generation) is not int or expected_generation < 1:
            raise PolicyRefused("expected policy generation required")
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT generation,digest FROM policy WHERE singleton=1").fetchone()
            if row is None:
                raise PolicyRefused("owner policy has not been bootstrapped")
            if row["generation"] != expected_generation:
                raise PolicyRefused("policy generation changed")
            generation, digest = row["generation"] + 1, _digest(document)
            db.execute("UPDATE policy SET generation=?,digest=?,document=? WHERE singleton=1",
                       (generation, digest, _canonical(document)))
            db.execute("INSERT INTO receipts(at,kind,owner,session_id,old_digest,new_digest,"
                       "generation,outcome) VALUES (?,?,?,?,?,?,?,?)",
                       (time.time(), "write", owner, session_id, row["digest"], digest,
                        generation, "committed"))
            if not db.execute("UPDATE import_state SET state='none',notice='replaced' "
                              "WHERE singleton=1 AND state='pending'").rowcount:
                # Saving their rules is the owner's review: it clears the notice.
                db.execute("UPDATE import_state SET notice='',consented='[]' "
                           "WHERE singleton=1")
            else:
                # The old rules are replaced, not lost silently: the notice says so.
                # The owner's own write wins; old box rows arriving later are dropped.
                db.execute("INSERT INTO receipts(at,kind,owner,rule,decision,outcome) "
                           "VALUES (?,?,?,?,?,?)", (time.time(), "import", owner,
                                                   "superseded by the owner's write", "deny",
                                                   "superseded"))
            db.commit()
        self._import_settled.add(owner)
        return OwnerPolicy(owner, generation, digest, document)

    def declare_model(self, owner: str, proof: dict, connection_id: str,
                      endpoints: object) -> dict:
        """Owner widening (router only): this connection's POST endpoints are model calls.

        It neither supersedes a pending import nor counts as the owner's review,
        unlike a full policy write.
        """
        session_id = self._session(owner, proof)
        if not isinstance(connection_id, str) or not connection_id or len(connection_id) > 128:
            raise PolicyRefused("invalid model declaration")
        kinds = model_kinds(connection_id, endpoints)
        if not kinds:
            raise PolicyRefused("invalid model declaration")
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT generation,digest,document FROM policy WHERE singleton=1"
                             ).fetchone()
            if row is None:
                raise PolicyRefused("owner policy has not been bootstrapped")
            policy = json.loads(row["document"])
            if _digest(policy) != row["digest"]:
                raise RuntimeError("policy digest mismatch")
            added = [entry for entry in kinds if entry not in policy["entries"]]
            generation, digest = row["generation"], row["digest"]
            if added:
                policy["entries"] = policy["entries"] + added
                self._validate(owner, policy)
                generation, digest = generation + 1, _digest(policy)
                db.execute("UPDATE policy SET generation=?,digest=?,document=? "
                           "WHERE singleton=1", (generation, digest, _canonical(policy)))
            db.execute("INSERT INTO receipts(at,kind,owner,session_id,old_digest,new_digest,"
                       "generation,rule,decision,outcome) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (time.time(), "model_declare", owner, session_id, row["digest"],
                        digest, generation, connection_id, "allow", "committed"))
            db.commit()
        return {"generation": generation, "declared": len(added)}

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

    def receipt_proposal(self, owner: str, center: str, change: dict) -> dict:
        if not isinstance(change, dict):
            raise PolicyRefused("invalid policy proposal")
        policy = self.read(owner)
        digest = _digest(change)
        with closing(self._connect(owner)) as db, db:
            db.execute("INSERT INTO receipts(at,kind,owner,request_digest,generation,"
                       "rule,decision,outcome) VALUES (?,?,?,?,?,?,?,?)",
                       (time.time(), "proposal", owner, digest, policy.generation,
                        "owner_policy", "ask", "requested"))
        return {"request_digest": digest, "generation": policy.generation}

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
            _validate_entry(entry)
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
        # An owner's own write or stronger declaration outranks a model declaration.
        severity = {"read": 0, "model": 0.5, "write": 1, "delete": 2, "message": 3,
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

    @staticmethod
    def _advance(db: sqlite3.Connection) -> int:
        """One generation for every authority change, consents included."""
        db.execute("UPDATE policy SET generation=generation+1 WHERE singleton=1")
        row = db.execute("SELECT generation FROM policy WHERE singleton=1").fetchone()
        if row is None:
            raise PolicyRefused("owner policy has not been bootstrapped")
        return row["generation"]

    def issue_once(self, owner: str, proof: dict, expected_generation: int,
                   request_digest: str) -> dict:
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
            db.execute("INSERT INTO once_approvals VALUES (?,?,?,?,?,NULL)",
                       (hashlib.sha256(token.encode()).hexdigest(), request_digest,
                        session_id, expected_generation, time.time() + 300))
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
                            decision_id, "allow", "matched"))
                db.commit()
                return decision_id
        return None

    def consents(self, owner: str, *, sink: str | None = None,
                 active_only: bool = True) -> list[dict]:
        clauses, params = [], []
        if sink:
            clauses.append("sink=?")
            params.append(sink)
        if active_only:
            clauses.append("revoked_at IS NULL")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with closing(self._connect(owner)) as db:
            rows = db.execute(
                "SELECT sink,destination,granted_at,granted_by,revoked_at FROM consents "
                f"{where} ORDER BY granted_at DESC, sink, destination", params).fetchall()
        return [dict(row) for row in rows]

    def consent_active(self, owner: str, sink: str, destination: str) -> bool:
        if not sink or not destination:
            return False
        with closing(self._connect(owner)) as db:
            return db.execute("SELECT 1 FROM consents WHERE sink=? AND destination=? "
                              "AND revoked_at IS NULL", (sink, destination)).fetchone() is not None

    def grant_consent(self, owner: str, proof: dict, sink: str, destination: str) -> dict:
        """Widening: only an interactive owner session on the router's channel."""
        session_id = self._session(owner, proof)
        sink, destination = _consent_key(sink, destination)
        now = time.time()
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO consents VALUES (?,?,?,?,NULL) ON CONFLICT(sink,destination) "
                       "DO UPDATE SET granted_at=excluded.granted_at,"
                       "granted_by=excluded.granted_by,revoked_at=NULL",
                       (sink, destination, now, owner))
            generation = self._advance(db)
            db.execute("INSERT INTO receipts(at,kind,owner,session_id,request_digest,"
                       "generation,rule,decision,outcome) VALUES (?,?,?,?,?,?,?,?,?)",
                       (now, "consent_grant", owner, session_id,
                        _digest({"sink": sink, "destination": destination}), generation,
                        sink, "allow", "committed"))
            db.commit()
        return {"sink": sink, "destination": destination, "granted_at": now,
                "granted_by": owner, "revoked_at": None}

    def revoke_consent(self, owner: str, sink: str, destination: str) -> bool:
        """Narrowing: either channel may take a consent back."""
        sink, destination = _consent_key(sink, destination)
        now = time.time()
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            hit = db.execute("UPDATE consents SET revoked_at=? WHERE sink=? AND destination=? "
                             "AND revoked_at IS NULL", (now, sink, destination)).rowcount
            if hit:
                generation = self._advance(db)
                db.execute("INSERT INTO receipts(at,kind,owner,request_digest,generation,"
                           "rule,decision,outcome) VALUES (?,?,?,?,?,?,?,?)",
                           (now, "consent_revoke", owner,
                            _digest({"sink": sink, "destination": destination}), generation,
                            sink, "deny", "committed"))
            db.commit()
        return bool(hit)

    def import_alarm(self, owner: str) -> bool:
        """Whether the owner should be told their earlier rules were not all imported."""
        notice = self.import_notice(owner)
        return bool(notice and notice["reason"])

    def import_notice(self, owner: str) -> dict | None:
        """What the owner should review: why the import ended, and what was consented.

        ``reason`` is ``failed`` (not imported: defaults), ``partial`` (invalid
        rows dropped), ``replaced`` (their own save came first) or ``""``.
        ``consented`` lists the destinations the migration consented for them.
        Cleared by the owner's next save of their rules.
        """
        with closing(self._connect(owner)) as db:
            row = db.execute("SELECT notice,consented FROM import_state WHERE singleton=1"
                             ).fetchone()
        if row is None:
            return None
        consented = json.loads(row["consented"] or "[]")
        if not row["notice"] and not consented:
            return None
        return {"reason": row["notice"], "consented": consented}

    def import_pending(self, owner: str) -> bool:
        """Pending until imported, superseded or past its deadline (then defaults)."""
        if owner in self._import_settled:
            return False
        with closing(self._connect(owner)) as db:
            row = db.execute("SELECT state,since FROM import_state WHERE singleton=1").fetchone()
        if row is None or row["state"] != "pending":
            self._import_settled.add(owner)
            return False
        if row["since"] and time.time() - row["since"] > IMPORT_DEADLINE_S:
            self.abandon_import(owner, reason="import deadline passed")
            return False
        return True

    def require_settled(self, owner: str) -> None:
        """Box operations wait, retryably, while the import is pending; never abandon."""
        if self.import_pending(owner):
            raise ImportPending("owner authority import pending; retry shortly")

    def note_import_attempt(self, owner: str) -> int:
        """Count the wakes that asked boxhostd to lift; returns the count so far."""
        with closing(self._connect(owner)) as db, db:
            db.execute("UPDATE import_state SET attempts=attempts+1,since=CASE WHEN since=0 "
                       "THEN ? ELSE since END WHERE singleton=1 AND state='pending'",
                       (time.time(),))
            row = db.execute("SELECT attempts FROM import_state WHERE singleton=1").fetchone()
        return row["attempts"] if row else 0

    def abandon_import(self, owner: str, *, reason: str) -> None:
        """The import cannot be trusted: the defaults a new owner gets, loudly.

        An ALARM receipt and log, and an owner-visible notice (``import_notice``)
        that their earlier rules could not be imported and should be reviewed.
        The agent keeps working on the defaults; nothing is pinned stricter.
        """
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM import_state WHERE singleton=1").fetchone()
            current = db.execute("SELECT generation,digest FROM policy WHERE singleton=1"
                                 ).fetchone()
            if row is not None and row["state"] == "pending" and current is not None:
                db.execute("UPDATE import_state SET state='none',notice='failed' "
                           "WHERE singleton=1")
                db.execute("INSERT INTO receipts(at,kind,owner,old_digest,new_digest,generation,"
                           "rule,decision,outcome) VALUES (?,?,?,?,?,?,?,?,?)",
                           (time.time(), "import", owner, current["digest"], current["digest"],
                            current["generation"], reason[:512], "alarm", "defaults"))
                _LOG.error("ALARM: owner authority import abandoned for %s: %s", owner, reason)
            db.commit()
        self._import_settled.add(owner)

    def import_authority(self, owner: str, document: object) -> dict:
        """Accept boxhostd's one lift while pending; never a silent partial import.

        Valid rows are kept. If any row was invalid, the same transaction raises
        the owner-visible notice and an ALARM receipt, so the owner reviews the
        rules that could not be read.
        """
        if (not isinstance(document, dict)
                or set(document) != {"source_digest", "entries", "consents"}
                or not isinstance(document["source_digest"], str)
                or len(document["source_digest"]) > 80
                or not isinstance(document["entries"], list)
                or not isinstance(document["consents"], list)):
            raise PolicyRefused("invalid authority import")
        kept, consents, dropped = [], [], 0
        for entry in document["entries"]:
            try:
                _validate_entry(entry)
            except PolicyRefused:
                dropped += 1
                continue
            kept.append(entry)
        for row in document["consents"]:
            try:
                if (not isinstance(row, dict)
                        or set(row) != {"sink", "destination", "granted_at", "granted_by"}
                        or type(row["granted_at"]) not in (int, float)
                        or not isinstance(row["granted_by"], str)):
                    raise PolicyRefused("invalid consent")
                _consent_key(row["sink"], row["destination"])
            except PolicyRefused:
                dropped += 1
                continue
            consents.append(row)
        consents.sort(key=lambda row: -row["granted_at"])
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT state FROM import_state WHERE singleton=1").fetchone()
            if state is None or state["state"] != "pending":
                raise PolicyRefused("authority import is not pending")
            row = db.execute("SELECT generation,digest,document FROM policy WHERE singleton=1"
                             ).fetchone()
            if row is None:
                raise PolicyRefused("owner policy has not been bootstrapped")
            policy = json.loads(row["document"])
            if _digest(policy) != row["digest"]:
                raise RuntimeError("policy digest mismatch")
            policy["entries"] = policy["entries"] + kept
            self._validate(owner, policy)
            generation, digest = row["generation"] + 1, _digest(policy)
            db.execute("UPDATE policy SET generation=?,digest=?,document=? WHERE singleton=1",
                       (generation, digest, _canonical(policy)))
            inserted = sum(db.execute(
                "INSERT OR IGNORE INTO consents VALUES (?,?,?,?,NULL)",
                (item["sink"], item["destination"], item["granted_at"], item["granted_by"]),
            ).rowcount for item in consents)
            db.execute("UPDATE import_state SET state='done',notice=? WHERE singleton=1",
                       ("partial" if dropped else "",))
            counts = {"entries": len(kept), "consents": inserted, "dropped": dropped}
            db.execute("INSERT INTO receipts(at,kind,owner,old_digest,new_digest,request_digest,"
                       "generation,rule,decision,outcome) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (time.time(), "import", owner, row["digest"], digest,
                        document["source_digest"], generation, _canonical(counts),
                        "alarm" if dropped else "allow",
                        "partial" if dropped else "committed"))
            db.commit()
        if dropped:
            _LOG.error("ALARM: owner authority import for %s dropped %s invalid rows",
                       owner, dropped)
        self._import_settled.add(owner)
        return {"generation": generation, **counts}
