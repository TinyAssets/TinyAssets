"""Temporary erasure-only authority. C6 jobs own all deletion progress."""
# lane-b2: delete with the old authority
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

NAME = ".erasure-ledger.sqlite3"


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
        # lane-b2: delete with the old authority
        return self.execute(operation, args)

    def contains(self, owner):
        try:
            # lane-b2: delete with the old authority
            return self._call("contains", digest=principal_digest(owner))
        except Exception:
            logging.getLogger(__name__).exception(
                "erasure authority unavailable; refusing every authentication")
            return True

    def fenced(self, owners):
        """One authority read for the broker's whole set of live principals."""
        digests = {principal_digest(owner): owner for owner in owners}
        try:
            # lane-b2: delete with the old authority
            return [digests[digest] for digest in self._call(
                "contains_many", digests=list(digests))]
        except Exception:
            logging.getLogger(__name__).exception("erasure authority unavailable; closing streams")
            return list(owners)

    def initialize(self):
        """Explicit offline cutover/fresh-install step; never repair authority."""
        if os.geteuid() != 0:
            raise PermissionError("erasure initialization requires root")
        from tinyassets.restore_recovery import _safe

        _safe(self.root, NAME)
        if self.path.exists():
            # lane-b2: delete with the old authority
            self.execute("entries", {})  # Existing damage is never an empty ledger.
            return
        protect_root(self.root)
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE initialized (version INTEGER PRIMARY KEY CHECK(version=1))")
            db.execute("INSERT INTO initialized VALUES (1)")
            db.execute("CREATE TABLE events (id TEXT PRIMARY KEY, "
                       "digest TEXT NOT NULL, at REAL NOT NULL, payload TEXT NOT NULL)")
            for table in ("events", "initialized"):
                for action in ("UPDATE", "DELETE"):
                    db.execute(f"CREATE TRIGGER no_{table}_{action.lower()} BEFORE {action} "
                               f"ON {table} BEGIN SELECT "
                               "RAISE(ABORT,'append-only erasure ledger'); END")
        directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def add(self, owner):
        from tinyassets.broker.owner_identities import _owner_request

        # lane-b2: delete with the old authority
        answer = _owner_request(self.root, {"op": "OWNER_ADMISSION_ERASE", "principal": owner})
        if answer != {"op": "OWNER_ADMISSION_ERASED"}:
            raise PermissionError("admission erasure refused")
        # lane-b2: delete with the old authority
        return self._call("add", digest=principal_digest(owner), principal=owner,
                          uids=[])

    def entries(self):
        # lane-b2: delete with the old authority
        return self.execute("entries", {})

    def execute(self, operation, args):
        if os.geteuid() != 0:
            raise PermissionError("erasure ledger requires root")
        from tinyassets.restore_recovery import _safe

        _safe(self.root, NAME)
        for suffix in ("-journal", "-wal", "-shm"):
            private_file(_safe(self.root, NAME + suffix))
        private_file(self.path)
        info = self.root.stat()
        if info.st_uid != 0 or (info.st_mode & 0o022 and not info.st_mode & 0o1000):
            raise PermissionError("erasure ledger parent permits replacement")
        readonly = operation in {"contains", "contains_many", "entries"}
        mode = "ro" if readonly else "rw"
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=" + mode, uri=True)) as db, db:
            if not readonly:
                db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN" if readonly else "BEGIN IMMEDIATE")
            from tinyassets.sqlite_integrity import check

            try:
                check(db)
            except ValueError as error:
                raise RuntimeError("erasure ledger integrity check failed") from error
            if db.execute("SELECT version FROM initialized").fetchall() != [(1,)]:
                raise RuntimeError("erasure ledger initialization proof missing")
            events = db.execute("SELECT id,digest,at,payload FROM events ORDER BY at,id").fetchall()
            entries = _project(events)
            digest = args.get("digest")
            if digest is not None and re.fullmatch("[0-9a-f]{64}", digest) is None:
                raise ValueError("invalid erasure digest")
            if operation == "contains":
                return digest in entries
            if operation == "contains_many":
                return [key for key in args["digests"] if key in entries]
            if operation == "entries":
                return list(entries.values())
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
            else:
                raise ValueError("unknown erasure operation")
            db.execute("INSERT INTO events VALUES (?,?,?,?)",
                       (uuid.uuid4().hex, digest, time.time(), json.dumps(payload, sort_keys=True)))


def _project(events):
    entries = {}
    for _, digest, _, raw in events:
        event = json.loads(raw)
        entry = entries.setdefault(digest, {"digest": digest, "principal": None, "uids": []})
        if event.get("principal"):
            entry["principal"] = event["principal"]
        entry["uids"] = sorted(set(entry["uids"]) | set(event.get("uids", [])))
    return entries


def fresh_install(root):
    """Deploy-only initializer: a fresh data directory has no prior authority."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise RuntimeError("fresh erasure initialization requires an empty data directory")
    # lane-b2: delete with the old authority
    ErasureLedger(root).initialize()


if __name__ == "__main__":
    import sys

    fresh_install(Path(sys.argv[1]))
