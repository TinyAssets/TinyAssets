"""Broker-owned egress/spending policy and interactive session verification.

The policy database is never opened by the daemon or by a box. All mutation
methods here are called only by the broker's policy RPC after it has received
a protected-session proof (the router's hash, or a legacy owner cookie).
The broker checks the live router row itself. Owner action policy lives in
action_policy.py, which reuses document receipts with its own local storage.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
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
DEFAULT_RULES = {"spend": "ask_first", "money.move": "hand_off"}

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
        """)
        document = {"entries": [], "egress": {"mode": "open", "destinations": []},
                    "spend": {"daily": None, "monthly": None, "transaction": None,
                              "ask_above": None, "source_ceilings": {}}}
        if provision and fresh:
            db.execute("INSERT OR IGNORE INTO policy VALUES (1,1,?,?)",
                       (_digest(document), _canonical(document)))
            db.execute("PRAGMA user_version=1")
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
        return db

    def erase(self, owner: str) -> int:
        """Erase policy custody under terminal admission."""
        path = self._path(owner)
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
            if (not isinstance(entry, dict) or set(entry) != {"scope", "table", "values"}
                    or entry["scope"] != "account"
                    or entry["table"] != "rules"
                    or not isinstance(entry["values"], dict)):
                raise PolicyRefused("invalid scoped policy entry")
            if entry["table"] == "rules":
                if (entry["values"].get("behaviour") not in BEHAVIOURS
                        or entry["values"].get("action_class") not in DEFAULT_RULES):
                    raise PolicyRefused("invalid approval rule")
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
        """Classify a broker request, apply the fixed floor, and receipt its decision.

        A later box-channel gate calls this same method.  Present connector and
        spend paths can call it already, so they share one decision source.
        Amounts are minor currency units; a nonzero amount is reserved only
        after all rules permit the request.
        """
        if action_class not in DEFAULT_RULES:
            raise PolicyRefused("spending action required")
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
