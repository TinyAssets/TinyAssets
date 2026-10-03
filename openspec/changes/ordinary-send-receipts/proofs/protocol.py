"""Executable DESIGN model, not production code or proof of runtime integration.

Three separate SQLite files, committed crash boundaries, concurrent initial sends.
No TinyAssets provider/account/device API is imported or invoked. Run with:
    python openspec/changes/ordinary-send-receipts/proofs/protocol.py
"""

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path


class Held(Exception):
    pass


class Crash(Exception):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class Model:
    def __init__(self, root, *, boot=None, issuer=None, issuer_alive=None):
        self.root = Path(root)
        self.boot = boot or uuid.uuid4().hex
        self.issuer = issuer or self.boot
        # Default is this single-process fixture's liveness observation. The
        # distinct-process proof injects the existing OS-lock observer instead.
        self.issuer_alive = issuer_alive or (lambda token: token == self.issuer)
        self.author = self.root / "author.db"
        self.steering = self.root / "steering.db"
        self.history = self.root / "history.db"

    @staticmethod
    @contextmanager
    def db(path, mode="rw"):
        conn = sqlite3.connect(path.as_uri() + "?mode=" + mode, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def initialize_fixture(self):
        # Explicit test setup only. NEVER reached from prepare/start/read.
        with sqlite3.connect(self.author) as c:
            c.executescript("""
                CREATE TABLE homes(owner TEXT PRIMARY KEY, home TEXT, deleted INTEGER);
                INSERT INTO homes VALUES ('alice','home-a',0),('bob','home-b',0);
                CREATE TABLE receipts(id TEXT PRIMARY KEY, owner TEXT, home TEXT, agent TEXT,
                    boot TEXT, phase TEXT, intent TEXT, digest TEXT, inputs TEXT,
                    terminal TEXT, issuer TEXT);
            """)
        with sqlite3.connect(self.steering) as c:
            c.executescript("""
                CREATE TABLE inputs(id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT,
                    home TEXT, agent TEXT, receipt TEXT, state TEXT, body TEXT);
                CREATE TABLE open_receipts(receipt TEXT PRIMARY KEY, owner TEXT,
                    home TEXT, agent TEXT, frozen INTEGER);
            """)
        with sqlite3.connect(self.history) as c:
            c.execute(
                "CREATE TABLE projections(receipt TEXT PRIMARY KEY, owner TEXT, "
                "home TEXT, agent TEXT, digest TEXT, body TEXT)"
            )

    @contextmanager
    def guard(self, scope):
        # Models existing author writer / current-home + deletion guard. Production
        # also needs the existing maintenance barrier and validated path/schema.
        with self.db(self.author) as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM homes WHERE owner=?", (scope[0],)).fetchone()
            if not row or row["home"] != scope[1] or row["deleted"]:
                raise Held("scope unavailable")
            try:
                yield c
                c.commit()
            except BaseException:
                c.rollback()
                raise

    @staticmethod
    def receipt(c, key, scope):
        row = c.execute(
            "SELECT * FROM receipts WHERE id=? AND owner=? AND home=? AND agent=?", (key, *scope)
        ).fetchone()
        if row is None:
            raise Held("unknown receipt; never insert by a supplied key")
        return row

    def rows(self, key):
        with self.db(self.steering, "ro") as c:
            return [
                dict(r)
                for r in c.execute("SELECT * FROM inputs WHERE receipt=? ORDER BY id", (key,))
            ]

    def queue(self, scope, body):
        with self.guard(scope), self.db(self.steering) as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "INSERT INTO inputs(owner,home,agent,state,body) VALUES (?,?,?,'queued',?)",
                (*scope, body),
            )
            c.commit()
            return row.lastrowid

    def prepare(self, scope, body, ids=(), *, crash=None):
        # Caller cannot select an existing/deleted receipt ID. Preparing is not
        # dispatch; lost responses may strand a held draft, never execute it.
        key = self.boot + ":" + uuid.uuid4().hex
        intent = {"body": body, "ids": list(ids)}
        with self.guard(scope) as c:
            c.execute(
                "INSERT INTO receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    *scope,
                    self.boot,
                    "preparing",
                    json.dumps(intent),
                    digest(intent),
                    "[]",
                    None,
                    self.issuer,
                ),
            )
        if crash == "reserved":
            raise Crash(key)
        with self.guard(scope), self.db(self.steering) as s:
            s.execute("BEGIN IMMEDIATE")
            for ident in ids:
                changed = s.execute(
                    "UPDATE inputs SET receipt=?,state='claimed' WHERE id=? "
                    "AND owner=? AND home=? AND agent=? AND state='queued' "
                    "AND receipt IS NULL",
                    (key, ident, *scope),
                ).rowcount
                if changed != 1:
                    raise Held("input already held elsewhere")
            s.commit()
        if crash == "custody":
            raise Crash(key)
        with self.guard(scope) as c:
            inputs = self.rows(key)
            c.execute(
                "UPDATE receipts SET phase='prepared',inputs=? WHERE id=?",
                (json.dumps(inputs), key),
            )
        return key

    def start(self, key, scope, body, ids=(), *, selected_consumer=False, crash=None):
        # Existing exact ordinary identity precedes all dynamic route selection.
        del selected_consumer
        with self.guard(scope) as c:
            row = self.receipt(c, key, scope)
            if row["digest"] != digest({"body": body, "ids": list(ids)}):
                raise Held("payload conflict")
            if (
                row["phase"] != "prepared"
                or row["boot"] != self.boot
                or row["issuer"] != self.issuer
            ):
                return False
            if self.rows(key) != json.loads(row["inputs"]):
                raise Held("custody changed")
            c.execute("UPDATE receipts SET phase='started' WHERE id=?", (key,))
        # Dispatch is OUTSIDE this transaction, only by its winning call.
        if crash == "started":
            raise Crash("started committed before opening steering")
        with self.guard(scope) as c, self.db(self.steering) as s:
            self.running(c, key, scope)
            s.execute("INSERT INTO open_receipts VALUES (?,?,?,?,0)", (key, *scope))
            s.commit()
        return True

    def running(self, c, key, scope, *, delivery=False):
        row = self.receipt(c, key, scope)
        current = (
            self.issuer_alive(row["issuer"])
            if delivery
            else row["boot"] == self.boot and row["issuer"] == self.issuer
        )
        if row["phase"] != "started" or not current:
            raise Held("stale/non-running writer")
        return row

    @staticmethod
    def open_for_input(s, key):
        live = s.execute("SELECT frozen FROM open_receipts WHERE receipt=?", (key,)).fetchone()
        if not live or live[0]:
            raise Held("closed")

    def enqueue(self, key, scope, body):
        with self.guard(scope) as c, self.db(self.steering) as s:
            self.running(c, key, scope)
            s.execute("BEGIN IMMEDIATE")
            self.open_for_input(s, key)
            r = s.execute(
                "INSERT INTO inputs(owner,home,agent,receipt,state,body) "
                "VALUES (?,?,?,?,'claimed',?)",
                (*scope, key, body),
            )
            s.commit()
            return r.lastrowid

    def take(self, key, scope):
        with self.guard(scope) as c, self.db(self.steering) as s:
            receipt = self.running(c, key, scope, delivery=True)
            s.execute("BEGIN IMMEDIATE")
            self.open_for_input(s, key)
            rows = list(
                s.execute("SELECT * FROM inputs WHERE receipt=? AND state='claimed'", (key,))
            )
            # Before exposure. Attempted never means safe to repeat after crash.
            s.execute(
                "UPDATE inputs SET state='attempted' WHERE receipt=? AND state='claimed'", (key,)
            )
            s.commit()
            if not self.issuer_alive(receipt["issuer"]):
                # Attempt state stays committed; dying after the admission
                # observation never makes the input safe to repeat.
                raise Held("issuer lost after attempt commit")
            return [r["body"] for r in rows]

    def freeze(self, key, scope):
        with self.guard(scope) as c, self.db(self.steering) as s:
            self.receipt(c, key, scope)
            s.execute("BEGIN IMMEDIATE")
            s.execute("UPDATE open_receipts SET frozen=1 WHERE receipt=?", (key,))
            s.execute(
                "UPDATE inputs SET state='closed_'||state WHERE receipt=? "
                "AND state IN ('claimed','attempted')",
                (key,),
            )
            s.commit()

    def publish(self, key, scope, reply):
        with self.guard(scope) as c:
            row = self.running(c, key, scope)
            with self.db(self.steering, "ro") as s:
                live = s.execute(
                    "SELECT frozen FROM open_receipts WHERE receipt=?", (key,)
                ).fetchone()
                if not live or not live[0]:
                    raise Held("input frontier not frozen")
            terminal = {
                "reply": reply,
                "inputs": self.rows(key),
                "intent": json.loads(row["intent"]),
            }
            if any(not r["state"].startswith("closed_") for r in terminal["inputs"]):
                raise Held("input frontier not frozen")
            c.execute(
                "UPDATE receipts SET phase='terminal',terminal=? WHERE id=?",
                (json.dumps(terminal), key),
            )

    def project(self, key, scope, *, crash=False):
        with self.guard(scope) as c, self.db(self.history) as h:
            row = self.receipt(c, key, scope)
            if row["phase"] not in ("terminal", "projected"):
                raise Held("no terminal")
            h.execute("BEGIN IMMEDIATE")
            prior = h.execute("SELECT digest FROM projections WHERE receipt=?", (key,)).fetchone()
            if prior and prior[0] != digest(row["terminal"]):
                raise Held("terminal conflict")
            h.execute(
                "INSERT OR IGNORE INTO projections VALUES (?,?,?,?,?,?)",
                (key, *scope, digest(row["terminal"]), row["terminal"]),
            )
            h.commit()
            if crash:
                raise Crash("history committed, author acknowledgement lost")
            c.execute("UPDATE receipts SET phase='projected' WHERE id=?", (key,))

    def cleanup(self, key, scope):
        with self.guard(scope) as c, self.db(self.steering) as s:
            row = self.receipt(c, key, scope)
            if row["phase"] != "projected":
                raise Held("unknown inputs cannot be deleted/requeued")
            s.execute("BEGIN IMMEDIATE")
            # Exact terminal snapshot, not whichever rows now happen to be there.
            for item in json.loads(row["terminal"])["inputs"]:
                if item["state"] == "closed_attempted":
                    s.execute(
                        "DELETE FROM inputs WHERE id=? AND receipt=? AND state=?",
                        (item["id"], key, item["state"]),
                    )
                else:
                    s.execute(
                        "UPDATE inputs SET state='queued',receipt=NULL WHERE id=? "
                        "AND receipt=? AND state=?",
                        (item["id"], key, item["state"]),
                    )
            s.commit()

    def observe(self, key, scope):
        with self.db(self.author, "ro") as c:
            c.execute("BEGIN")
            home = c.execute("SELECT * FROM homes WHERE owner=?", (scope[0],)).fetchone()
            if not home or home["home"] != scope[1] or home["deleted"]:
                raise Held("scope unavailable")
            row = self.receipt(c, key, scope)
            return json.loads(row["terminal"]) if row["terminal"] else {"state": "held"}

    def erase_fixture_owner_content(self, owner):
        # Models required SQL-visible erasure only, not real deletion implementation
        # or forensic secure erase. Existing tombstone authorizes no future work.
        with self.db(self.author) as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE homes SET deleted=1 WHERE owner=?", (owner,))
            c.execute("DELETE FROM receipts WHERE owner=?", (owner,))
            c.commit()
        for path, table in (
            (self.steering, "inputs"),
            (self.steering, "open_receipts"),
            (self.history, "projections"),
        ):
            with self.db(path) as c:
                c.execute(f"DELETE FROM {table} WHERE owner=?", (owner,))
                c.commit()


A = ("alice", "home-a", "main")
B = ("bob", "home-b", "main")


class ProtocolProof(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ordinary-receipt-model-")
        self.addCleanup(self.tmp.cleanup)
        self.m = Model(self.tmp.name)
        self.m.initialize_fixture()

    def test_concurrent_start_same_key_dispatches_once(self):
        key = self.m.prepare(A, "message")
        barrier = threading.Barrier(8)

        def submit(_):
            barrier.wait()
            return self.m.start(key, A, "message")

        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(submit, range(8))), 1)

    def test_changed_payload_rejected_and_repeated_text_new_intent_is_distinct(self):
        k1 = self.m.prepare(A, "same")
        with self.assertRaises(Held):
            self.m.start(k1, A, "different")
        k2 = self.m.prepare(A, "same")
        self.assertNotEqual(k1, k2)
        self.assertTrue(self.m.start(k1, A, "same"))
        self.assertTrue(self.m.start(k2, A, "same"))

    def test_preparation_never_dispatches_and_custody_survives_each_gap(self):
        for point in ("reserved", "custody"):
            with self.subTest(point=point):
                ident = self.m.queue(A, point)
                with self.assertRaises(Crash) as failure:
                    self.m.prepare(A, "body", [ident], crash=point)
                key = str(failure.exception)
                self.assertFalse(self.m.start(key, A, "body", [ident]))
                with self.m.db(self.m.steering, "ro") as c:
                    self.assertEqual(
                        c.execute("SELECT body FROM inputs WHERE id=?", (ident,)).fetchone()[0],
                        point,
                    )

    def test_conflicting_preclaim_does_not_steal_or_execute(self):
        ident = self.m.queue(A, "one input")
        first = self.m.prepare(A, "body", [ident])
        with self.assertRaises(Held):
            self.m.prepare(A, "body", [ident])
        self.assertTrue(self.m.start(first, A, "body", [ident]))
        self.assertEqual(self.m.take(first, A), ["one input"])
        self.assertEqual(self.m.take(first, A), [])

    def test_delivery_crash_and_stale_cleanup_never_requeue_attempted(self):
        key = self.m.prepare(A, "body")
        self.m.start(key, A, "body")
        self.m.enqueue(key, A, "exact input")
        self.m.take(key, A)
        self.m.freeze(key, A)  # same retained transition for stale open_turn cleanup
        with self.assertRaises(Held):
            self.m.cleanup(key, A)
        self.assertEqual(self.m.rows(key)[0]["state"], "closed_attempted")
        restarted = Model(self.tmp.name)
        self.assertFalse(restarted.start(key, A, "body"))
        self.assertEqual(restarted.observe(key, A), {"state": "held"})

    def test_freeze_serializes_with_enqueue_and_prevents_late_input(self):
        key = self.m.prepare(A, "body")
        self.m.start(key, A, "body")
        self.m.freeze(key, A)
        with self.assertRaises(Held):
            self.m.enqueue(key, A, "late")
        with self.assertRaises(Held):
            self.m.take(key, A)

    def test_started_crash_never_grants_another_dispatch(self):
        key = self.m.prepare(A, "body")
        with self.assertRaises(Crash):
            self.m.start(key, A, "body", crash="started")
        self.assertFalse(self.m.start(key, A, "body"))
        with self.assertRaises(Held):
            self.m.take(key, A)
        with self.assertRaises(Held):
            self.m.publish(key, A, "unearned answer")

    def test_stale_boot_cannot_enqueue_take_or_publish(self):
        key = self.m.prepare(A, "body")
        self.m.start(key, A, "body")
        self.m.enqueue(key, A, "input")
        restarted = Model(self.tmp.name)
        for action in (
            lambda: restarted.enqueue(key, A, "late"),
            lambda: restarted.take(key, A),
            lambda: restarted.publish(key, A, "stale answer"),
        ):
            with self.assertRaises(Held):
                action()
        self.assertEqual(self.m.rows(key)[0]["state"], "claimed")

    def test_terminal_and_projection_crash_recovery_only_projects_once(self):
        key = self.m.prepare(A, "body")
        self.m.start(key, A, "body")
        self.m.enqueue(key, A, "attempted")
        self.m.take(key, A)
        self.m.enqueue(key, A, "untouched")
        self.m.freeze(key, A)
        self.m.publish(key, A, "answer")
        with self.assertRaises(Crash):
            self.m.project(key, A, crash=True)
        self.assertFalse(self.m.start(key, A, "body"))
        self.m.project(key, A)
        self.m.project(key, A)
        self.m.cleanup(key, A)
        self.m.cleanup(key, A)
        with self.m.db(self.m.history, "ro") as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM projections").fetchone()[0], 1)
        with self.m.db(self.m.steering, "ro") as c:
            self.assertEqual(
                [tuple(r) for r in c.execute("SELECT body,state,receipt FROM inputs")],
                [("untouched", "queued", None)],
            )

    def test_missing_receipt_or_schema_cannot_recreate_accepted_key(self):
        key = self.m.prepare(A, "body")
        self.m.start(key, A, "body")
        with self.m.db(self.m.author) as c:
            c.execute("DELETE FROM receipts WHERE id=?", (key,))
            c.commit()
        with self.assertRaises(Held):
            self.m.start(key, A, "body")
        with self.m.db(self.m.author) as c:
            c.execute("DROP TABLE receipts")
            c.commit()
        with self.assertRaises(sqlite3.OperationalError):
            self.m.prepare(A, "new body")
        with self.assertRaises(sqlite3.OperationalError):
            self.m.start(key, A, "body")
        with self.m.db(self.m.author, "ro") as c:
            self.assertIsNone(
                c.execute("SELECT name FROM sqlite_master WHERE name='receipts'").fetchone()
            )

    def test_restore_prepared_snapshot_from_older_boot_cannot_start(self):
        key = self.m.prepare(A, "body")
        restarted = Model(self.tmp.name)
        self.assertFalse(restarted.start(key, A, "body"))

    def test_read_missing_database_never_creates_it(self):
        missing = Model(self.m.root / "not-created")
        with self.assertRaises(sqlite3.OperationalError):
            missing.observe("unknown", A)
        self.assertFalse(missing.root.exists())

    def test_fresh_schema_after_reset_does_not_reissue_old_id(self):
        key = self.m.prepare(A, "body")
        self.m.start(key, A, "body")
        # Explicit fixture reset/reinitialization, never request-path repair.
        for path in (self.m.author, self.m.steering, self.m.history):
            path.unlink()
        reset = Model(self.tmp.name)
        reset.initialize_fixture()
        with self.assertRaises(Held):
            reset.start(key, A, "body")
        fresh = reset.prepare(A, "body")
        self.assertNotEqual(fresh, key)
        self.assertTrue(reset.start(fresh, A, "body"))

    def test_enqueue_racing_freeze_is_retained_or_refused(self):
        key = self.m.prepare(A, "body")
        self.m.start(key, A, "body")
        barrier = threading.Barrier(2)

        def enqueue():
            barrier.wait()
            try:
                return self.m.enqueue(key, A, "racing input")
            except Held:
                return None

        def freeze():
            barrier.wait()
            self.m.freeze(key, A)

        with ThreadPoolExecutor(max_workers=2) as pool:
            arrival = pool.submit(enqueue)
            closure = pool.submit(freeze)
            ident = arrival.result()
            closure.result()
        rows = self.m.rows(key)
        self.assertEqual(len(rows), int(ident is not None))
        if rows:
            self.assertEqual(rows[0]["state"], "closed_claimed")
            self.assertEqual(rows[0]["body"], "racing input")

    def test_tombstone_racing_enqueue_leaves_no_owner_content(self):
        key = self.m.prepare(A, "body")
        self.m.start(key, A, "body")
        barrier = threading.Barrier(2)

        def enqueue():
            barrier.wait()
            try:
                self.m.enqueue(key, A, "racing private input")
            except Held:
                pass

        def erase():
            barrier.wait()
            self.m.erase_fixture_owner_content("alice")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(enqueue), pool.submit(erase)]
            for future in futures:
                future.result()
        self.assertEqual(self.m.rows(key), [])
        with self.assertRaises(Held):
            self.m.enqueue(key, A, "late")

    def test_home_change_holds_old_scope_and_erasure_includes_former_home(self):
        old = self.m.prepare(A, "old home input")
        self.m.start(old, A, "old home input")
        self.m.enqueue(old, A, "old steering")
        with self.m.db(self.m.author) as c:
            c.execute("UPDATE homes SET home='home-new' WHERE owner='alice'")
            c.commit()
        with self.assertRaises(Held):
            self.m.take(old, A)
        new_scope = ("alice", "home-new", "main")
        new = self.m.prepare(new_scope, "new home input")
        self.m.start(new, new_scope, "new home input")
        self.m.enqueue(new, new_scope, "new steering")
        self.m.erase_fixture_owner_content("alice")
        self.assertEqual(self.m.rows(old), [])
        self.assertEqual(self.m.rows(new), [])

    def test_consumer_switch_never_reinterprets_existing_ordinary_key(self):
        key = self.m.prepare(A, "body")
        self.m.start(key, A, "body")
        self.assertFalse(self.m.start(key, A, "body", selected_consumer=True))
        with self.assertRaises(Held):
            self.m.start("missing", A, "body", selected_consumer=True)

    def test_scope_isolation_and_read_has_no_writes(self):
        key = self.m.prepare(A, "private")
        for scope in (B, ("alice", "home-b", "main"), ("alice", "home-a", "other")):
            with self.assertRaises(Held):
                self.m.observe(key, scope)
            with self.assertRaises(Held):
                self.m.start(key, scope, "private")
        before = self.m.author.read_bytes()
        self.assertEqual(self.m.observe(key, A), {"state": "held"})
        self.assertEqual(before, self.m.author.read_bytes())

    def test_erasure_removes_content_and_old_key_stays_non_executable(self):
        key = self.m.prepare(A, "erase this private text")
        self.m.start(key, A, "erase this private text")
        self.m.enqueue(key, A, "erase queued text")
        self.m.take(key, A)
        self.m.freeze(key, A)
        self.m.publish(key, A, "erase reply")
        self.m.project(key, A)
        other = self.m.prepare(B, "keep bob")
        self.m.erase_fixture_owner_content("alice")
        for path, table in (
            (self.m.author, "receipts"),
            (self.m.steering, "inputs"),
            (self.m.steering, "open_receipts"),
            (self.m.history, "projections"),
        ):
            with self.m.db(path, "ro") as c:
                self.assertEqual(
                    c.execute(f"SELECT COUNT(*) FROM {table} WHERE owner=?", ("alice",)).fetchone()[
                        0
                    ],
                    0,
                )
        with self.assertRaises(Held):
            self.m.start(key, A, "erase this private text")
        self.assertTrue(self.m.start(other, B, "keep bob"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
