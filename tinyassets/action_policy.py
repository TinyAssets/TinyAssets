"""C8/T8: owner action rules and approvals are evaluated in the owner's box."""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from tinyassets.broker.policy import BEHAVIOURS, PolicyRefused, PolicyStore, _canonical, _digest

DEFAULT_RULES = {
    "workspace.files": "do", "workspace.shell": "do", "workspace.workflows": "do",
    "shell.egress": "do", "app.read": "do", "app.write": "do",
    "people.message": "ask_first", "commons.publish": "ask_first", "share": "ask_first",
    "browser.action": "ask_first",
    "security.change": "hand_off", "access.grant": "hand_off",
}
OPERATION_KINDS = {"read": "app.read", "write": "app.write", "delete": "app.write",
                   "message": "people.message", "payment": "money.move",
                   "security": "security.change", "access": "access.grant"}

def initialize(path, document=None):
    """Initialize one box-local policy; cutover is the sole shared caller."""
    fresh = not Path(path).exists()
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS policy (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                generation INTEGER NOT NULL, digest TEXT NOT NULL, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
                kind TEXT NOT NULL, owner TEXT NOT NULL, session_id TEXT,
                old_digest TEXT, new_digest TEXT, request_digest TEXT,
                generation INTEGER, rule TEXT, decision TEXT, outcome TEXT);
            CREATE TABLE IF NOT EXISTS once_approvals (
                token_hash TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
                session_id TEXT NOT NULL, generation INTEGER NOT NULL,
                expires_at REAL NOT NULL, consumed_at REAL);
        """)
        document = document if document is not None else {"entries": []}
        if fresh:
            db.execute("INSERT INTO policy VALUES (1,1,?,?)",
                       (_digest(document), _canonical(document)))


class ActionPolicyStore(PolicyStore):
    """Reuse document receipts; storage, session proof and decisions are local."""

    def __init__(self, home):
        from tinyassets.owner_runtime import require

        self.runtime = require(Path(home))
        self.path = self.runtime.root / ".action-policy.db"

    def _path(self, owner):
        if owner != self.runtime.principal:
            raise PolicyRefused("policy belongs to this box")
        return self.path

    def _connect(self, owner):
        db = sqlite3.connect(self._path(owner).as_uri() + "?mode=rw", uri=True,
                             timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        return db

    def _session(self, owner, proof):
        from tinyassets.onboarding.owner_sessions import router_owner_session

        session = router_owner_session()
        if (session is None or json.loads(session["identity_json"])["user_id"] != owner
                or proof != {"session_hash": session["session_hash"]}):
            raise PolicyRefused("interactive owner session required")
        return session["session_hash"]

    def _validate(self, owner, document):
        if not isinstance(document, dict) or set(document) != {"entries"}:
            raise PolicyRefused("invalid action policy document")
        if not isinstance(document["entries"], list):
            raise PolicyRefused("invalid policy entries")
        for entry in document["entries"]:
            if (not isinstance(entry, dict) or set(entry) != {"scope", "table", "values"}
                    or entry["scope"] != "account"
                    or entry["table"] not in {"rules", "approval_grants", "operation_kinds",
                                               "review_on", "review_off"}
                    or not isinstance(entry["values"], dict)):
                raise PolicyRefused("invalid scoped policy entry")
            if entry["table"] == "rules":
                if (entry["values"].get("behaviour") not in BEHAVIOURS
                        or entry["values"].get("action_class") not in DEFAULT_RULES):
                    raise PolicyRefused("invalid approval rule")
            if (entry["table"] == "operation_kinds"
                    and entry["values"].get("kind") not in OPERATION_KINDS):
                raise PolicyRefused("invalid declared operation")

    def decide(self, owner, *, center, action_class, connection="", operation="",
               destination="", amount=0, source="", agent="*", request_id="", approved=False):
        request = dict(center=center, action_class=action_class, connection=connection,
                       operation=operation, destination=destination, amount=amount,
                       source=source, agent=agent, request_id=request_id)
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT generation,digest,document FROM policy WHERE singleton=1"
                             ).fetchone()
            if row is None:
                raise PolicyRefused("action policy unavailable")
            policy = json.loads(row["document"])
            if _digest(policy) != row["digest"]:
                raise PolicyRefused("policy digest mismatch")
            matches = []
            for entry in policy["entries"]:
                if entry["table"] != "rules":
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
            rule = (f"account:{strictest['values'].get('id', '')}" if strictest else "uncovered")
            decision = {"do": "allow", "do_if_preapproved": "hold",
                        "ask_first": "ask", "hand_off": "deny"}[behaviour]
            result = {"decision": decision, "behaviour": behaviour, "rule": rule,
                      "generation": row["generation"], "policy_digest": row["digest"],
                      "request_digest": _digest(request)}
            db.execute("INSERT INTO receipts(at,kind,owner,request_digest,generation,rule,"
                       "decision,outcome) VALUES (?,?,?,?,?,?,?,?)",
                       (time.time(), "decision", owner, result["request_digest"],
                        row["generation"], rule, decision, decision))
            db.commit()
        return result

    def classify(self, owner: str, center: str, connection: str,
                 method: str, path: str) -> tuple[str, str]:
        """Declared HTTP meaning; undeclared calls conservatively count as writes."""
        policy = self.read(owner).document
        method = str(method or "").upper()
        path = str(path or "/").rstrip("/") or "/"
        matches = []
        for entry in policy["entries"]:
            if entry["table"] != "operation_kinds" or entry["scope"] != "account":
                continue
            values = entry["values"]
            prefix = str(values.get("path_prefix") or "/").rstrip("/") or "/"
            if (values.get("connection") == connection
                    and values.get("method", "") in ("", method)
                    and (prefix == "/" or path == prefix or path.startswith(prefix + "/"))):
                matches.append((len(prefix), bool(values.get("method")), values.get("kind")))
        if not matches:
            return "app.write", method
        severity = {"read": 0, "write": 1, "delete": 2, "message": 3,
                    "payment": 4, "security": 4, "access": 4}
        kind = max(matches, key=lambda match: severity.get(match[2], 4))[2]
        return OPERATION_KINDS.get(kind, "app.write"), method


    def issue_once(self, owner: str, cookie: str | dict, expected_generation: int,
                   request_digest: str) -> dict:
        session_id = self._session(owner, cookie)
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


    def consume_once(self, owner: str, token: str, request_digest: str,
                     generation: int) -> bool:
        if not isinstance(token, str) or len(token) > 512:
            return False
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT generation FROM policy WHERE singleton=1").fetchone()
            if current is None or current["generation"] != generation:
                return False
            cursor = db.execute(
                "UPDATE once_approvals SET consumed_at=? WHERE token_hash=? "
                "AND request_digest=? AND generation=? AND expires_at>? "
                "AND consumed_at IS NULL",
                (time.time(), hashlib.sha256(token.encode()).hexdigest(), request_digest,
                 generation, time.time()),
            )
            if cursor.rowcount != 1:
                return False
            db.execute("INSERT INTO receipts(at,kind,owner,request_digest,generation,"
                       "decision,outcome) VALUES (?,?,?,?,?,?,?)",
                       (time.time(), "once_approval", owner, request_digest, generation,
                        "allow_once", "consumed"))
            db.commit()
        return True


    def allows_grant(self, owner: str, *, center: str, generation: int,
                     decision_id: str, action_class: str, connection: str,
                     operation: str, origin: str, agent: str, task_id: str = "") -> bool:
        """Match an owner-issued standing grant at the current generation."""
        if not isinstance(decision_id, str) or not decision_id or len(decision_id) > 512:
            return False
        with closing(self._connect(owner)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT generation,document FROM policy WHERE singleton=1").fetchone()
            if row is None or row["generation"] != generation:
                return False
            document = json.loads(row["document"])
            for entry in document["entries"]:
                if entry["table"] != "approval_grants" or entry["scope"] != "account":
                    continue
                values = entry["values"]
                if values.get("decision_id") != decision_id:
                    continue
                grant = json.loads(values.get("grant_json") or "{}")
                if (grant.get("revoked") or grant.get("owner") != owner
                        or grant.get("agent") != agent
                        or grant.get("action_class") != action_class
                        or grant.get("connection") != connection
                        or grant.get("operation") != operation
                        or grant.get("origin") != origin
                        or grant.get("scope") not in ("task", "site", "always")
                        or (grant.get("expires_at") is not None
                            and grant["expires_at"] <= time.time())
                        or (grant["scope"] == "task" and grant.get("task_id") != task_id)):
                    return False
                db.execute("INSERT INTO receipts(at,kind,owner,generation,rule,decision,outcome)"
                           " VALUES (?,?,?,?,?,?,?)",
                           (time.time(), "standing_grant", owner, generation, decision_id,
                            "allow", "matched"))
                db.commit()
                return True
        return False


def owner_policy_operation(data_root, owner, action, *, center, **fields):
    store = ActionPolicyStore(Path(data_root) / center)
    store._path(owner)
    if action == "read":
        return store.read(owner).projection()
    if action == "receipts":
        return {"receipts": store.receipts(owner)}
    if action == "proposal":
        return store.receipt_proposal(owner, center, fields["change"])
    if action == "write":
        return store.write(owner, fields["session_proof"], fields["expected_generation"],
                           fields["policy"]).projection()
    if action == "classify":
        action_class, operation = store.classify(owner, center, fields["connection"],
                                                 fields["method"], fields["path"])
        return {"action_class": action_class, "operation": operation}
    if action == "decide":
        request = {k: v for k, v in fields["request"].items() if k != "center"}
        return store.decide(owner, center=center, **request)
    if action == "issue_once":
        from tinyassets.broker.server import request_digest

        wire = {"url": fields["destination"]}
        wire.update({k: v for k, v in fields["request"].items()
                     if k in {"headers", "body", "header_name"}})
        digest = request_digest(grant_id=fields["grant_id"],
                                connection_id=fields["connection_id"],
                                verb=fields["verb"], request=wire)
        return store.issue_once(owner, fields["session_proof"], fields["expected_generation"],
                                digest)
    raise PolicyRefused("unsupported action policy operation")
