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
SNAPSHOT_QUIET = 900
SNAPSHOT_DEADLINE = 86400


def _add_column(db, table, column, ddl):
    present = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in present:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS owner_volumes ("
               "principal TEXT PRIMARY KEY NOT NULL, uid INTEGER NOT NULL UNIQUE, "
               "volume_generation INTEGER NOT NULL CHECK(volume_generation > 0), "
               "snapshot_id TEXT, snapshot_sha256 TEXT, work TEXT, "
               "local INTEGER NOT NULL DEFAULT 1 CHECK(local IN (0,1)), asleep_at REAL, "
               "rewind_generation INTEGER, "
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
               "volume_generation INTEGER NOT NULL, published_at REAL)")
    _add_column(db, "owner_volumes", "rewind_generation", "INTEGER")
    _add_column(db, "owner_snapshots", "published_at", "REAL")


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
            "v.local,v.asleep_at,v.rewind_generation "
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
                           "work", "local", "asleep_at", "rewind_generation"), row))
        record["local"] = bool(record["local"])
        record["work"] = json.loads(record["work"]) if record["work"] else None
        yield db, record


def binding(identities, owner):
    with transaction(identities, owner, active=False) as (_, record):
        return record


def deferred(work, last_published, now):
    """A confirmed stop whose offsite copy is not owed yet (ADR-026).

    A box stops as soon as no work runs, so pauses between turns are frequent.
    Its full image is copied offsite only after it stays stopped for
    SNAPSHOT_QUIET, or at once when its newest copy is a day old or it has
    none. A wake before then replaces the intent, and the local image is the
    volume, the same as for an awake box.
    """
    return (isinstance(work, dict) and work.get("kind") == "snapshot"
            and "stopped_at" in work and not work.get("restore_snapshot_id")
            and now - work["stopped_at"] < SNAPSHOT_QUIET
            and last_published is not None and now - last_published < SNAPSHOT_DEADLINE)


def _owed(db, *, include_running, now):
    """(principal, reasons): recoverable work, excess snapshots, or a cold image.

    ``{"kind": "create"}`` is not recoverable. VolumeLifecycle.recover leaves
    it alone and the volume is formatted on first wake, so counting it made
    deploy readiness wait for a wake that the readiness window never issues.
    A deferred stop is not owed until its quiet period ends.
    """
    from tinyassets.broker.admission import admission

    rows = db.execute(
        "SELECT v.principal, v.work, v.local, v.asleep_at, v.snapshot_id, "
        "(SELECT COUNT(*) FROM owner_snapshots s WHERE s.principal=v.principal), "
        "(SELECT MAX(published_at) FROM owner_snapshots s WHERE s.principal=v.principal) "
        "FROM owner_volumes v JOIN owner_identities i "
        "ON i.principal=v.principal AND i.machine_id=v.uid").fetchall()
    for principal, work, local, asleep_at, snapshot_id, snapshots, published in rows:
        work = json.loads(work) if work else None
        kind = work.get("kind") if work else None
        reasons = []
        if (work and kind != "create" and (include_running or kind != "running")
                and not deferred(work, published, now)):
            reasons.append(kind if isinstance(kind, str) else "work")
        if snapshots > 7:
            reasons.append("excess_snapshots")
        if local and asleep_at is not None and asleep_at < now - COLD_AFTER and snapshot_id:
            reasons.append("cold")
        if reasons and admission(db, principal).state in {"admitted", "suspended"}:
            yield principal, reasons


def pending(identities, *, include_running=True):
    with closing(identities._connect(write=False)) as db:
        return [owner for owner, _ in _owed(
            db, include_running=include_running, now=time.time())]


def pending_counts(identities, *, include_running=False):
    """Counts for the same rows as pending(). Principals stay in the database."""
    counts = {}
    with closing(identities._connect(write=False)) as db:
        for _, reasons in _owed(db, include_running=include_running, now=time.time()):
            for key in reasons:
                counts[key] = counts.get(key, 0) + 1
    return counts


def snapshot_deferred(identities, owner):
    with closing(identities._connect(write=False)) as db:
        work, published = db.execute(
            "SELECT work, (SELECT MAX(published_at) FROM owner_snapshots s "
            "WHERE s.principal=v.principal) FROM owner_volumes v WHERE principal=?",
            (owner,)).fetchone()
    return deferred(json.loads(work) if work else None, published, time.time())


def _pin_set(pinned):
    if not isinstance(pinned, list):
        raise ValueError("pinned snapshot report is not a list")
    pins = set()
    for item in pinned:
        if (not isinstance(item, (list, tuple)) or len(item) != 2
                or type(item[0]) is not int
                or not isinstance(item[1], str)
                or not re.fullmatch(r"[a-f0-9]{32}", item[1])):
            raise ValueError("pinned snapshot report is malformed")
        pins.add((item[0], item[1]))
    return pins


def actionable_excess(identities, pinned):
    """Owners whose prune can still delete a snapshot.

    The retained set matches VolumeLifecycle.prune: the current snapshot,
    then the newest rows until seven, plus an in-flight restore or
    pre-snapshot. A row outside that set that a platform backup pins cannot
    be deleted, so it is not unfinished deploy work. pending() still returns
    the owner, and the tick still prunes.
    """
    pins = _pin_set(pinned)
    count = 0
    with closing(identities._connect(write=False)) as db:
        rows = db.execute(
            "SELECT v.principal FROM owner_volumes v JOIN owner_identities i "
            "ON i.principal=v.principal AND i.machine_id=v.uid "
            "WHERE (SELECT COUNT(*) FROM owner_snapshots s "
            "WHERE s.principal=v.principal)>7").fetchall()
        from tinyassets.broker.admission import admission

        for (owner,) in rows:
            if admission(db, owner).state not in {"admitted", "suspended"}:
                continue
            if _has_deletable_excess(db, owner, pins):
                count += 1
    return count


def _has_deletable_excess(db, owner, pins):
    row = db.execute(
        "SELECT v.uid,v.snapshot_id,v.work FROM owner_volumes v "
        "JOIN owner_identities i ON i.principal=v.principal AND i.machine_id=v.uid "
        "WHERE v.principal=?", (owner,)).fetchone()
    if row is None:
        return False
    uid, snapshot_id, work_json = row
    work = json.loads(work_json) if work_json else {}
    retained = {snapshot_id}
    snapshots = [item[0] for item in db.execute(
        "SELECT snapshot_id FROM owner_snapshots WHERE principal=? "
        "ORDER BY sequence DESC", (owner,))]
    for snap in snapshots:
        if len(retained) < 7:
            retained.add(snap)
    for key in ("restore_snapshot_id", "pre_snapshot_id"):
        if work.get(key):
            retained.add(work[key])
    return any(snap not in retained and (uid, snap) not in pins for snap in snapshots)


def awake_owners(identities, *, stopping=False):
    """Explicit running owners, not the pending-work queue (C15).

    ``stopping`` adds intents whose stop is not confirmed. A dated stop is
    confirmed: a restarted broker that counted it would hold a phantom slot.
    """
    with closing(identities._connect(write=False)) as db:
        return [row[0] for row in db.execute(
            "SELECT principal FROM owner_volumes WHERE json_extract(work,'$.kind')='running' "
            "OR (? AND json_extract(work,'$.kind') IN ('snapshot','restore') AND NOT ("
            "json_extract(work,'$.stopped_at') IS NOT NULL AND "
            "json_extract(work,'$.restore_snapshot_id') IS NULL))", (stopping,))]


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
    fields = {"binding": set(), "created": set(), "sleep": set(), "stopped": set(),
              "publish": {"snapshot_id", "sha256"},
              "restore": {"snapshot_id"}, "continue_restore": set(),
              "restored": {"snapshot_id"}, "pending": set(),
              "snapshots": set(), "forget": {"snapshot_id"},
              "wake": set(), "evict": set(), "evicted": set(), "erase": set(),
              "sleep_refused": set(),
              "image_absent": set()}
    if action == "restore":
        if (not isinstance(arguments, dict) or "snapshot_id" not in arguments
                or set(arguments) - {"expected_generation"} != {"snapshot_id"}):
            raise ValueError("invalid volume lifecycle arguments")
    elif (action not in fields or not isinstance(arguments, dict)
            or set(arguments) != fields[action]):
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
                if work:
                    for key in ("snapshot_id", "restore_snapshot_id", "pre_snapshot_id"):
                        if work.get(key):
                            released.add(work[key])
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
            if work and work.get("restore_snapshot_id"):
                raise PermissionError("restore must complete before wake")
            if (work and work["kind"] not in {"running", "snapshot"}) or not record["local"]:
                raise PermissionError("volume must be local and committed before wake")
            work = {"kind": "running"}
            db.execute("UPDATE owner_volumes SET asleep_at=NULL WHERE principal=?", (owner,))
            record["asleep_at"] = None
        elif action == "created":
            if work != {"kind": "create"}:
                raise PermissionError("volume creation is not pending")
            work = None
        elif action == "image_absent":
            # The copy cannot be taken: the local file is already gone. A
            # published snapshot remains the volume. With no snapshot at all,
            # the next wake formats, which is the create path.
            if (not work or work.get("kind") != "snapshot"
                    or work.get("restore_snapshot_id")):
                raise PermissionError("snapshot is not blocked on a missing image")
            if record["snapshot_id"]:
                db.execute("UPDATE owner_volumes SET local=0 WHERE principal=?", (owner,))
                record["local"] = False
                work = None
            else:
                work = {"kind": "create"}
        elif action == "sleep_refused":
            # The box is reported awake and not idle: the sleep never happened,
            # so no snapshot is owed. A restore's pre-snapshot is never withdrawn.
            if (work and work.get("kind") == "snapshot"
                    and not work.get("restore_snapshot_id")):
                work = {"kind": "running"}
        elif action == "sleep":
            if work in (None, {"kind": "running"}):
                work = {"kind": "snapshot", "snapshot_id": uuid.uuid4().hex}
            elif work["kind"] != "snapshot":
                raise PermissionError("restore must complete before sleep")
        elif action == "stopped":
            # The host confirmed the stop. The first confirmation dates it.
            if not work or work["kind"] != "snapshot":
                raise PermissionError("no sleep awaiting its stop")
            work = {"stopped_at": time.time(), **work}
        elif action == "publish":
            reference(arguments["snapshot_id"], arguments["sha256"])
            if (not work or work["kind"] != "snapshot"
                    or work["snapshot_id"] != arguments["snapshot_id"]):
                raise PermissionError("no completed sleep awaiting snapshot")
            published_at = time.time()
            db.execute("INSERT INTO owner_snapshots "
                       "(principal,snapshot_id,snapshot_sha256,volume_generation,published_at) "
                       "VALUES (?,?,?,?,?)",
                       (owner, arguments["snapshot_id"], arguments["sha256"],
                        record["volume_generation"], published_at))
            db.execute("UPDATE owner_volumes SET snapshot_id=?,snapshot_sha256=?,asleep_at=? "
                       "WHERE principal=?", (arguments["snapshot_id"], arguments["sha256"],
                                             published_at, owner))
            record.update(snapshot_id=arguments["snapshot_id"],
                          snapshot_sha256=arguments["sha256"], asleep_at=published_at)
            if work.get("restore_snapshot_id"):
                # The pre-restore image is published. Only now may install replace it.
                work = {"kind": "restore", "phase": "install",
                        "snapshot_id": work["restore_snapshot_id"],
                        "sha256": work["restore_sha256"],
                        "volume_generation": record["volume_generation"] + 1,
                        "pre_snapshot_id": arguments["snapshot_id"],
                        "rewind": True}
            else:
                work = None
        elif action == "continue_restore":
            # The local image is already gone, so there is no current state to publish.
            if (not work or work.get("kind") != "snapshot"
                    or "restore_snapshot_id" not in work):
                raise PermissionError("no pre-restore snapshot to skip")
            work = {"kind": "restore", "phase": "install",
                    "snapshot_id": work["restore_snapshot_id"],
                    "sha256": work["restore_sha256"],
                    "volume_generation": record["volume_generation"] + 1,
                    "rewind": False}
        elif action == "restore":
            snapshot_id = arguments["snapshot_id"]
            if "expected_generation" in arguments:
                expected = arguments["expected_generation"]
                if type(expected) is not int or expected != record["volume_generation"]:
                    raise PermissionError("volume generation changed")
            if work is not None and work.get("kind") == "restore":
                if work.get("snapshot_id") != snapshot_id:
                    raise PermissionError("another volume operation is pending")
            elif work is not None and work.get("kind") == "snapshot":
                # Keep the pending sleep id. Publishing must not be replaced by install.
                if work.get("restore_snapshot_id") not in (None, snapshot_id):
                    raise PermissionError("another volume operation is pending")
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
                work = {**work, "restore_snapshot_id": snapshot_id, "restore_sha256": sha256}
            elif work not in (None, {"kind": "running"}):
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
                if record["local"]:
                    work = {"kind": "snapshot", "snapshot_id": uuid.uuid4().hex,
                            "restore_snapshot_id": snapshot_id, "restore_sha256": sha256}
                else:
                    work = {"kind": "restore", "phase": "install", "snapshot_id": snapshot_id,
                            "sha256": sha256, "volume_generation": record["volume_generation"] + 1,
                            "rewind": False}
        elif action == "restored":
            if (work is None or work["kind"] != "restore"
                    or work["snapshot_id"] != arguments["snapshot_id"]):
                raise PermissionError("no matching restore awaiting commit")
            rewind_generation = (work["volume_generation"] if work.get("rewind")
                                 else record.get("rewind_generation"))
            db.execute("UPDATE owner_volumes SET local=1,asleep_at=NULL,"
                       "volume_generation=?,snapshot_id=?,snapshot_sha256=?,rewind_generation=? "
                       "WHERE principal=?",
                       (work["volume_generation"], work["snapshot_id"], work["sha256"],
                        rewind_generation, owner))
            record["volume_generation"] = work["volume_generation"]
            record["snapshot_id"] = work["snapshot_id"]
            record["snapshot_sha256"] = work["sha256"]
            record["rewind_generation"] = rewind_generation
            record["local"], record["asleep_at"] = True, None
            work = None
        elif action == "snapshots":
            return [dict(zip(("snapshot_id", "sha256", "published_at"), row)) for row in db.execute(
                "SELECT snapshot_id,snapshot_sha256,published_at FROM owner_snapshots "
                "WHERE principal=? ORDER BY sequence DESC", (owner,))]
        elif action == "forget":
            protected = {record["snapshot_id"]}
            if work:
                protected.update(work.get(key) for key in (
                    "snapshot_id", "restore_snapshot_id", "pre_snapshot_id"))
            if arguments["snapshot_id"] in protected:
                raise PermissionError("map references this snapshot")
            db.execute("DELETE FROM owner_snapshots WHERE principal=? AND snapshot_id=?",
                       (owner, arguments["snapshot_id"]))
        db.execute("UPDATE owner_volumes SET work=? WHERE principal=?",
                   (json.dumps(work) if work else None, owner))
        record["work"] = work
        return record
