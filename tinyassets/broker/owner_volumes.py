"""T3/C2: broker-owned permanent volume bindings and recoverable lifecycle work.

This database contains references, never paths into owner contents. boxhostd is
a pure executor; the broker derives every UID from the permanent owner identity.
The one-shot migration populates existing bindings before runtime admission.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from contextlib import closing, contextmanager

COLD_AFTER = 7 * 86400


def schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS owner_volumes ("
               "principal TEXT PRIMARY KEY NOT NULL, uid INTEGER NOT NULL UNIQUE, "
               "volume_generation INTEGER NOT NULL CHECK(volume_generation > 0), "
               "snapshot_id TEXT, snapshot_sha256 TEXT, work TEXT, "
               "local INTEGER NOT NULL DEFAULT 1 CHECK(local IN (0,1)), asleep_at REAL, "
               "CHECK((snapshot_id IS NULL) = (snapshot_sha256 IS NULL)))")
    db.execute("CREATE TRIGGER IF NOT EXISTS permanent_volume_binding "
               "BEFORE UPDATE OF principal, uid ON owner_volumes BEGIN "
               "SELECT RAISE(ABORT, 'volume bindings are permanent'); END")
    db.execute("CREATE TRIGGER IF NOT EXISTS retain_volume_binding "
               "BEFORE DELETE ON owner_volumes BEGIN "
               "SELECT RAISE(ABORT, 'volume bindings are permanent'); END")
    db.execute("CREATE TABLE IF NOT EXISTS owner_snapshots ("
               "sequence INTEGER PRIMARY KEY AUTOINCREMENT, principal TEXT NOT NULL, "
               "snapshot_id TEXT NOT NULL UNIQUE, snapshot_sha256 TEXT NOT NULL, "
               "volume_generation INTEGER NOT NULL)")


def provision(db, owner, uid):
    """Called only by broker admission, in its box-record transaction."""
    db.execute("INSERT INTO owner_volumes "
               "(principal,uid,volume_generation,work) VALUES (?,?,1,?)",
               (owner, uid, json.dumps({"kind": "create"})))


@contextmanager
def transaction(identities, owner, *, active=True):
    from tinyassets.broker.owner_identities import validate_principal

    validate_principal(owner)
    with closing(identities._connect(write=True)) as db, db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT v.uid,v.volume_generation,v.snapshot_id,v.snapshot_sha256,v.work,"
            "v.local,v.asleep_at "
            "FROM owner_volumes v JOIN owner_identities i "
            "ON i.principal=v.principal AND i.machine_id=v.uid WHERE v.principal=?",
            (owner,)).fetchone()
        if row is None:
            raise LookupError("owner has no permanent volume binding")
        # C2: publication and erasure share the admission database transaction.
        if active:
            from tinyassets.broker.admission import admission

            if admission(db, owner).state not in {"admitted", "suspended"}:
                raise PermissionError("owner admission is erased")
        record = dict(zip(("uid", "volume_generation", "snapshot_id", "snapshot_sha256",
                           "work", "local", "asleep_at"), row))
        record["local"] = bool(record["local"])
        record["work"] = json.loads(record["work"]) if record["work"] else None
        yield db, record


def binding(identities, owner):
    with transaction(identities, owner, active=False) as (_, record):
        return record


def pending(identities, *, include_running=True):
    with closing(identities._connect(write=False)) as db:
        candidates = [row[0] for row in db.execute(
            "SELECT v.principal FROM owner_volumes v JOIN owner_identities i "
            "ON i.principal=v.principal AND i.machine_id=v.uid "
            "WHERE ((v.work IS NOT NULL AND "
            "(? OR json_extract(v.work,'$.kind')!='running')) OR "
            "(SELECT COUNT(*) FROM owner_snapshots s WHERE s.principal=v.principal)>7 OR "
            "(v.local=1 AND v.asleep_at<? AND v.snapshot_id IS NOT NULL))",
            (include_running, time.time() - COLD_AFTER,))]
        from tinyassets.broker.admission import admission

        return [owner for owner in candidates
                if admission(db, owner).state in {"admitted", "suspended"}]


def awake_owners(identities, *, stopping=False):
    """Explicit running owners, not the pending-work queue (C15)."""
    with closing(identities._connect(write=False)) as db:
        return [row[0] for row in db.execute(
            "SELECT principal FROM owner_volumes WHERE json_extract(work,'$.kind')='running' "
            "OR (? AND json_extract(work,'$.kind') IN ('snapshot','restore'))", (stopping,))]


def reference(snapshot_id, sha256):
    if (not isinstance(snapshot_id, str) or not re.fullmatch(r"[a-f0-9]{32}", snapshot_id)
            or not isinstance(sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", sha256)):
        raise ValueError("verified snapshot reference required")


def dispatch(identities, document):
    """Broker-internal transitions; never exposed on the host protocol."""
    if set(document) != {"op", "principal", "action", "arguments"}:
        raise ValueError("invalid volume lifecycle request")
    owner, action, arguments = (document["principal"], document["action"],
                                document["arguments"])
    fields = {"binding": set(), "created": set(), "sleep": set(),
              "publish": {"snapshot_id", "sha256"},
              "restore": {"snapshot_id"}, "restored": {"snapshot_id"}, "pending": set(),
              "snapshots": set(), "forget": {"snapshot_id"},
              "wake": set(), "evict": set(), "evicted": set(), "erase": set()}
    if action not in fields or not isinstance(arguments, dict) or set(arguments) != fields[action]:
        raise ValueError("invalid volume lifecycle arguments")
    if action == "binding":
        return binding(identities, owner)
    with transaction(identities, owner, active=action != "erase") as (db, record):
        work = record["work"]
        if action == "erase":
            from tinyassets.broker.admission import admission

            if admission(db, owner).state != "erased":
                raise PermissionError("snapshot release requires erased admission")
            if work is None or work["kind"] != "erase":
                released = {row[0] for row in db.execute(
                    "SELECT snapshot_id FROM owner_snapshots WHERE principal=?", (owner,))}
                if record["snapshot_id"]:
                    released.add(record["snapshot_id"])
                if work and work.get("snapshot_id"):
                    released.add(work["snapshot_id"])
                work = {"kind": "erase", "released": sorted(released)}
                db.execute("DELETE FROM owner_snapshots WHERE principal=?", (owner,))
                db.execute("UPDATE owner_volumes SET snapshot_id=NULL,snapshot_sha256=NULL,"
                           "local=0 WHERE principal=?", (owner,))
                record.update(snapshot_id=None, snapshot_sha256=None, local=False)
        elif action == "evict":
            # C10: only the admitted binding's published snapshot permits eviction.
            if (work is not None or not record["local"] or record["asleep_at"] is None
                    or record["asleep_at"] >= time.time() - COLD_AFTER):
                return None
            reference(record["snapshot_id"], record["snapshot_sha256"])
            work = {"kind": "evict"}
        elif action == "evicted":
            if work != {"kind": "evict"}:
                raise PermissionError("eviction is not pending")
            db.execute("UPDATE owner_volumes SET local=0 WHERE principal=?", (owner,))
            record["local"] = False
            work = None
        elif action == "wake":
            if (work and work["kind"] not in {"running", "snapshot"}) or not record["local"]:
                raise PermissionError("volume must be local and committed before wake")
            work = {"kind": "running"}
            db.execute("UPDATE owner_volumes SET asleep_at=NULL WHERE principal=?", (owner,))
            record["asleep_at"] = None
        elif action == "created":
            if work != {"kind": "create"}:
                raise PermissionError("volume creation is not pending")
            work = None
        elif action == "sleep":
            if work in (None, {"kind": "running"}):
                work = {"kind": "snapshot", "snapshot_id": uuid.uuid4().hex}
            elif work["kind"] != "snapshot":
                raise PermissionError("restore must complete before sleep")
        elif action == "publish":
            reference(arguments["snapshot_id"], arguments["sha256"])
            if (not work or work["kind"] != "snapshot"
                    or work["snapshot_id"] != arguments["snapshot_id"]):
                raise PermissionError("no completed sleep awaiting snapshot")
            db.execute("INSERT INTO owner_snapshots "
                       "(principal,snapshot_id,snapshot_sha256,volume_generation) VALUES (?,?,?,?)",
                       (owner, arguments["snapshot_id"], arguments["sha256"],
                        record["volume_generation"]))
            db.execute("UPDATE owner_volumes SET snapshot_id=?,snapshot_sha256=?,asleep_at=? "
                       "WHERE principal=?", (arguments["snapshot_id"], arguments["sha256"],
                                             time.time(), owner))
            work = None
        elif action == "restore":
            snapshot_id = arguments["snapshot_id"]
            if work is not None and work["kind"] == "restore":
                if work.get("snapshot_id") != snapshot_id:
                    raise PermissionError("another volume operation is pending")
            else:
                if snapshot_id == record["snapshot_id"]:
                    sha256 = record["snapshot_sha256"]
                else:
                    found = db.execute("SELECT snapshot_sha256 FROM owner_snapshots "
                                       "WHERE principal=? AND snapshot_id=?",
                                       (owner, snapshot_id)).fetchone()
                    if found is None:
                        raise LookupError("owner snapshot is not retained")
                    sha256 = found[0]
                reference(snapshot_id, sha256)
                work = {"kind": "restore", "snapshot_id": snapshot_id, "sha256": sha256,
                        "volume_generation": record["volume_generation"] + 1}
        elif action == "restored":
            if (work is None or work["kind"] != "restore"
                    or work["snapshot_id"] != arguments["snapshot_id"]):
                raise PermissionError("no matching restore awaiting commit")
            db.execute("UPDATE owner_volumes SET local=1,asleep_at=NULL,"
                       "volume_generation=?,snapshot_id=?,snapshot_sha256=? WHERE principal=?",
                       (work["volume_generation"], work["snapshot_id"], work["sha256"], owner))
            record["volume_generation"] = work["volume_generation"]
            record["snapshot_id"] = work["snapshot_id"]
            record["snapshot_sha256"] = work["sha256"]
            record["local"], record["asleep_at"] = True, None
            work = None
        elif action == "snapshots":
            return [dict(zip(("snapshot_id", "sha256"), row)) for row in db.execute(
                "SELECT snapshot_id,snapshot_sha256 FROM owner_snapshots "
                "WHERE principal=? ORDER BY sequence DESC", (owner,))]
        elif action == "forget":
            if arguments["snapshot_id"] == record["snapshot_id"]:
                raise PermissionError("map references this snapshot")
            db.execute("DELETE FROM owner_snapshots WHERE principal=? AND snapshot_id=?",
                       (owner, arguments["snapshot_id"]))
        db.execute("UPDATE owner_volumes SET work=? WHERE principal=?",
                   (json.dumps(work) if work else None, owner))
        record["work"] = work
        return record
