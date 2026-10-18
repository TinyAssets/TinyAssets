"""Root-owned append-only erasure authority, shared by deletion and both restores."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

NAME = ".erasure-ledger.sqlite3"
PHASES = ("owner_tree", "broker_egress", "billing", "credentials", "identity")


def principal_digest(principal):
    return hashlib.sha256(principal.encode()).hexdigest()


def protect_root(root):
    """The router may create shared files, but cannot replace root authority."""
    os.chown(root, 0, 1001)
    root.chmod(0o1775)


def private_file(path):
    if path.exists():
        info = path.stat()
        if info.st_uid != 0 or info.st_mode & 0o077:
            raise PermissionError("erasure ledger must be root-private")


class ErasureLedger:
    def __init__(self, root: Path):
        from tinyassets.owner_runtime import refuse_router_store

        refuse_router_store("erasure_ledger")
        self.root, self.path = Path(root), Path(root) / NAME

    def _call(self, operation, **args):
        if os.geteuid() != 0:
            from tinyassets.boxes.daemon_control import request

            return request("erasure", ledger_op=operation, arguments=args)["result"]
        return self.execute(operation, args)

    def contains(self, owner):
        return self._call("contains", digest=principal_digest(owner))

    def add(self, owner, receipt=None):
        return self._call("add", digest=principal_digest(owner), principal=owner,
                          receipt=receipt or {})

    def receipt(self, owner):
        return self._call("receipt", digest=principal_digest(owner))

    def save_receipt(self, owner, receipt):
        return self._call("save", digest=principal_digest(owner), receipt=receipt)

    def pending(self):
        if os.geteuid() == 0:
            return self.execute("pending", {})
        entries, after = [], ""
        while page := self._call("pending_page", after=after):
            entries.extend(page)
            after = page[-1]["digest"]
        return entries

    def entries(self):
        return self.execute("entries", {})

    def execute(self, operation, args):
        if os.geteuid() != 0:
            raise PermissionError("erasure ledger requires root")
        from tinyassets.restore_recovery import _safe

        _safe(self.root, NAME)
        for suffix in ("-journal", "-wal", "-shm"):
            private_file(_safe(self.root, NAME + suffix))
        if self.path.exists():
            private_file(self.path)
            info = self.root.stat()
            if info.st_uid != 0 or (info.st_mode & 0o022 and not info.st_mode & 0o1000):
                raise PermissionError("erasure ledger parent permits replacement")
        elif operation in {"contains", "pending", "pending_page", "entries", "export"}:
            return False if operation == "contains" else []
        else:
            protect_root(self.root)
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                             0o600)
                os.close(fd)
            except FileExistsError:
                private_file(_safe(self.root, NAME))
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, "
                       "digest TEXT NOT NULL, at REAL NOT NULL, payload TEXT NOT NULL)")
            db.execute("CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON events "
                       "BEGIN SELECT RAISE(ABORT,'append-only erasure ledger'); END")
            db.execute("CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON events "
                       "BEGIN SELECT RAISE(ABORT,'append-only erasure ledger'); END")
            db.execute("BEGIN IMMEDIATE")
            events = db.execute("SELECT id,digest,at,payload FROM events ORDER BY at,id").fetchall()
            entries = _project(events)
            digest = args.get("digest")
            if digest is not None and re.fullmatch("[0-9a-f]{64}", digest) is None:
                raise ValueError("invalid erasure digest")
            if operation == "export":
                return events
            if operation == "union":
                # Import immutable event identities; conflicting bytes fail closed.
                existing = {row[0]: tuple(row) for row in events}
                for row in args["events"]:
                    if row[0] in existing and existing[row[0]] != tuple(row):
                        raise ValueError("conflicting erasure event")
                    db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?)", row)
                return
            if operation == "contains":
                return digest in entries
            if operation == "pending_page":
                return [{"digest": key, "principal": entries[key]["principal"]}
                        for key in sorted(entries) if key > args["after"]
                        and entries[key]["receipt"]["unfinished_phases"]][:64]
            if operation in {"pending", "entries"}:
                return [value for value in entries.values() if operation == "entries"
                        or value["receipt"]["unfinished_phases"]]
            if operation == "receipt":
                return entries[digest]["receipt"]
            payload = {}
            if operation == "add":
                principal = args.get("principal")
                if principal:
                    from tinyassets.broker.owner_identities import validate_principal

                    validate_principal(principal)
                if any(type(uid) is not int or not 300001 <= uid <= 399999
                       for uid in args.get("uids", [])):
                    raise ValueError("invalid erased UID")
                if principal and principal_digest(principal) != digest:
                    raise ValueError("principal digest mismatch")
                payload = {"principal": principal, "uids": args.get("uids", [])}
                previous = entries.get(digest)
                if previous and (not principal or principal == previous["principal"]) and set(
                        payload["uids"]).issubset(previous["uids"]):
                    return
                if digest not in entries:
                    payload["tasks"] = {phase: phase for phase in PHASES}
                    payload["receipt"] = args.get("receipt", {})
                    payload["done"] = args.get("receipt", {}).get("completed_phases", [])
            elif operation == "save":
                entry = entries[digest]
                receipt = args["receipt"]
                completed = set(receipt.get("completed_phases", []))
                # A caller can never declare the home gone over pending path work.
                if entry["receipt"]["quarantines"] or entry["work"]:
                    completed.discard("owner_tree")
                payload = {"receipt": receipt, "done": [key for key, phase in entry["tasks"].items()
                                                        if phase in completed]}
            elif operation == "work":
                entries[digest]  # A fence always precedes destructive work.
                key = uuid.uuid4().hex
                payload = {"work": {key: args["target"]}, "tasks": {key: "owner_tree"}}
            elif operation == "finish":
                payload = {"done": args["ids"]}
            elif operation == "restart":
                entries[digest]
                payload = {"tasks": {uuid.uuid4().hex: phase for phase in PHASES},
                           "receipt": {"local_cleanup": "done"}}
            else:
                raise ValueError("unknown erasure operation")
            db.execute("INSERT INTO events VALUES (?,?,?,?)",
                       (uuid.uuid4().hex, digest, time.time(), json.dumps(payload, sort_keys=True)))


def _project(events):
    entries = {}
    for _, digest, _, raw in events:
        event = json.loads(raw)
        entry = entries.setdefault(digest, {"digest": digest, "principal": None,
                                           "uids": [], "tasks": {}, "work": {},
                                           "done": set(), "receipt": {}, "local_done": False})
        if event.get("principal"):
            entry["principal"] = event["principal"]
        entry["uids"] = sorted(set(entry["uids"]) | set(event.get("uids", [])))
        entry["tasks"].update(event.get("tasks", {}))
        entry["work"].update(event.get("work", {}))
        entry["done"].update(event.get("done", []))
        receipt = event.get("receipt", {})
        entry["local_done"] |= receipt.get("local_cleanup") == "done"
        cleanup = {**entry["receipt"].get("billing_cleanup", {}),
                   **receipt.get("billing_cleanup", {})}
        entry["receipt"].update(receipt)
        if cleanup:
            entry["receipt"]["billing_cleanup"] = cleanup
    for entry in entries.values():
        done = entry.pop("done")
        entry["tasks"] = {key: phase for key, phase in entry["tasks"].items() if key not in done}
        entry["work"] = {key: target for key, target in entry["work"].items() if key not in done}
        pending = set(entry["tasks"].values())
        entry["receipt"].update(
            unfinished_phases=[phase for phase in PHASES if phase in pending],
            completed_phases=[phase for phase in PHASES if phase not in pending],
            home_removed="owner_tree" not in pending,
            quarantines=[target["quarantine"] for target in entry["work"].values()])
        if entry.pop("local_done"):
            entry["receipt"]["local_cleanup"] = "done"
        entry["receipt"].pop("restore_pending", None)
    return entries


def carry_forward(current, staged, unavailable_sources=None):
    """Offline cutover/restore union, including every pre-ledger erasure fact."""
    from tinyassets.restore_recovery import UnreadableFacts, _rows

    if (staged / NAME).exists():
        protect_root(staged)
    ledger = ErasureLedger(staged)
    unavailable = [] if unavailable_sources is None else unavailable_sources
    sources = ((".router-deleted-owners.sqlite3", "deleted_owners"),
               (".broker/credential-custody.db", "deleted_principals"),
               (".boxhost/boxhost.db", "owner_destroys"),
               (".broker/state/owner-identities.db", "owner_box_revocations"))
    for root in dict.fromkeys((staged, current)):
        try:
            private_file(root / NAME)
            for suffix in ("-journal", "-wal", "-shm"):
                private_file(root / (NAME + suffix))
            events = _rows(root, NAME, "events")
            if events:
                ledger.execute("union", {"events": [
                    [row[k] for k in ("id", "digest", "at", "payload")] for row in events]})
        except (OSError, sqlite3.DatabaseError, UnreadableFacts, RuntimeError):
            if root == staged:
                raise
            unavailable.append("live " + NAME)
        for relative, table in sources:
            try:
                rows = _rows(root, relative, table)
            except (OSError, sqlite3.DatabaseError, UnreadableFacts, RuntimeError):
                if root == staged:
                    raise
                unavailable.append("live " + relative)
                continue
            for row in rows:
                if table == "owner_box_revocations" and not row["deleted"]:
                    continue
                owner = row.get("owner", row.get("owner_id", row.get("principal")))
                digest = row["digest"] if table == "deleted_principals" else principal_digest(owner)
                receipt = json.loads(row.get("receipt") or "null") or {}
                ledger.execute("add", {"digest": digest, "principal": owner, "receipt": receipt,
                                       "uids": [row["uid"]] if table == "owner_destroys" else []})
                for path in receipt.get("quarantines", []):
                    destination = Path(path)
                    ledger.execute("work", {"digest": digest, "target": {
                        "uid": int(destination.name), "store": str(destination.parent),
                        "quarantine": str(destination)}})
    # Older restore reports carried paths separately. Absorb that last legacy
    # fact before replacing reports with diagnostics; never discard unknown work.
    for root in dict.fromkeys((staged, current)):
        from tinyassets.restore_recovery import _safe

        try:
            identities = _rows(root, ".broker/state/owner-identities.db", "owner_identities")
            for row in identities:
                if ledger.contains(row["principal"]):
                    ledger.execute("add", {"digest": principal_digest(row["principal"]),
                                           "principal": row["principal"],
                                           "uids": [row["machine_id"]]})
        except (OSError, sqlite3.DatabaseError, UnreadableFacts, RuntimeError):
            if root == staged:
                raise
            unavailable.append("live legacy erasure identities")
        try:
            report_path = _safe(root, ".disaster-restore.json")
            report = json.loads(report_path.read_text()) if report_path.exists() else {}
        except (OSError, json.JSONDecodeError):
            if root == staged:
                raise
            unavailable.append("live legacy erasure cleanup")
            continue
        if "pending_erasures" in report:
            for target in report["pending_erasures"] + report.get("erasure_failures", []):
                matches = [entry for entry in ledger.entries() if target["uid"] in entry["uids"]]
                if len(matches) != 1:
                    raise ValueError("legacy quarantine has no unique erased UID reservation")
                ledger.execute("work", {"digest": matches[0]["digest"], "target": {
                    "uid": target["uid"], "quarantine": target["quarantine"],
                    "store": target.get("store", str(Path(target["quarantine"]).parent))}})
    for suffix in ("", "-journal", "-wal", "-shm"):
        (staged / (".router-deleted-owners.sqlite3" + suffix)).unlink(missing_ok=True)
    return ledger
