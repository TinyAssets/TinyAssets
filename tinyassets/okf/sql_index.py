"""Disposable SQL indexes over admitted canonical records, never disk stores.

Only repository-owned initializers supply DDL. Every public consumer operation
reloads a coherent revision and publishes its entire diff in one canonical CAS.
The SQLite connection is an implementation detail and never a recovery source.
"""

from __future__ import annotations

import functools
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager

from tinyassets.boxes.provider import BoxOperationRefused, WriteConflict
from tinyassets.okf.bundle import InvalidBundle
from tinyassets.okf.migration import _quoted, restore_scalar, scalar
from tinyassets.okf.records import SourceRecords, record_entry, source_census


def require_source(source, name):
    if not isinstance(source, SourceRecords) or source.source != name:
        raise InvalidBundle(f"{name} requires an explicitly admitted canonical source")
    return source


def reviewed_rows(source, before):
    """Validate explicit namespace assignments, including deliberately assigned NULL."""
    center, owner, _ = source.bundle.scope
    tables = source.rows(before)
    for table, definition in source_census(before, source.source)["tables"].items():
        scope = definition["scope"]
        if len(scope) != 1:
            raise InvalidBundle("Consumer namespace needs one reviewed ownership column")
        column, values = next(iter(scope.items()))
        bindings = definition.get("reviewed_bindings", [])
        assigned = [
            scalar(b["namespace"])
            for b in bindings
            if b.get("owner") == owner
            and b.get("center") == center
            and b.get("source") == source.source
            and b.get("table") == table
            and b.get("snapshot_sha256") == source_census(before, source.source)["backup_sha256"]
            and b.get("reviewer")
            and b.get("review_ref")
            and b.get("evidence_sha256")
        ]
        if any(scalar(value) not in assigned for value in values):
            raise InvalidBundle("Canonical namespace lacks reviewed owner provenance")
        if any(scalar(row[column]) not in assigned for row in tables[table].values()):
            raise InvalidBundle("Canonical row is outside reviewed owner namespaces")
    return tables


class CanonicalConnection:
    def __init__(self, source, name, initialize, *, row_factory=None, rebuild=None):
        self.source = require_source(source, name)
        self.initialize, self.rebuild = initialize, rebuild
        self.row_factory = row_factory
        self._closed = False
        self._db = None
        self._depth = 0
        self._lock = threading.RLock()
        self._pending = None
        self._operation = None
        self._load()

    def _load(self):
        if self._closed:
            raise InvalidBundle("Canonical consumer session has ended")
        if self._pending is not None:
            raise InvalidBundle("Canonical outcome pending; retry the original transaction")
        before = self.source.snapshot()
        rows = reviewed_rows(self.source, before)
        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.row_factory = self.row_factory
        try:
            self.initialize(db)
            trusted_tables = {
                row[1]
                for row in db.execute("PRAGMA table_list")
                if row[0] == "main" and row[2] == "table" and not row[1].startswith("sqlite_")
            }
            if trusted_tables != set(rows):
                raise InvalidBundle("Canonical migration must include every consumer table")
            definitions = source_census(before, self.source.source)["tables"]
            columns = {}
            for table, records in rows.items():
                actual = list(db.execute("PRAGMA table_info(" + _quoted(table) + ")"))
                names = [row[1] for row in actual]
                declared = definitions[table]["columns"]
                if not names or not set(names) <= {c["name"] for c in declared}:
                    raise InvalidBundle("Canonical schema needs an explicit reviewed migration")
                if {r[1]: r[5] for r in actual if r[5]} != {
                    c["name"]: c["pk"] for c in declared if c["pk"]
                }:
                    raise InvalidBundle("Canonical identity differs from the trusted index schema")
                columns[table] = names
                db.executemany(
                    "INSERT OR ABORT INTO "
                    + _quoted(table)
                    + " ("
                    + ",".join(map(_quoted, names))
                    + ") VALUES ("
                    + ",".join("?" for _ in names)
                    + ")",
                    [tuple(row[name] for name in names) for row in records.values()],
                )
                expected = sorted(
                    repr([scalar(row[name]) for name in names]) for row in records.values()
                )
                actual_values = sorted(
                    repr([scalar(value) for value in row])
                    for row in db.execute("SELECT * FROM " + _quoted(table))
                )
                if actual_values != expected:
                    raise InvalidBundle("Canonical values cannot be indexed without a typed change")
            # AUTOINCREMENT state is derived too. Preserve identities of forgotten
            # records so a new row cannot reuse their canonical ID after rebuild.
            for table in columns:
                schema = db.execute(
                    "SELECT sql FROM sqlite_master WHERE name = ?", (table,)
                ).fetchone()[0]
                if "AUTOINCREMENT" not in schema.upper():
                    continue
                high = definitions[table].get("sequence", 0)
                if type(high) is not int or not 0 <= high < 2**63:
                    raise InvalidBundle("Reviewed integer sequence is malformed")
                deleted_high = before.manifest.get("deletion_sequences", {}).get(
                    json.dumps([self.source.source, table]), 0
                )
                if type(deleted_high) is not int or not 0 <= deleted_high < 2**63:
                    raise InvalidBundle("Deleted integer sequence is malformed")
                high = max(high, deleted_high)
                for deleted in before.manifest.get("tombstones", {}).values():
                    metadata = deleted["metadata"]
                    if (
                        metadata.get("source") != self.source.source
                        or metadata.get("table") != table
                    ):
                        continue
                    identity = metadata.get("identity")
                    if (
                        not isinstance(identity, list)
                        or len(identity) != 1
                        or not isinstance(identity[0], list)
                        or len(identity[0]) != 2
                        or identity[0][0] != "integer"
                    ):
                        raise InvalidBundle("Deleted integer identity is malformed")
                    try:
                        value = restore_scalar(identity[0])
                    except (TypeError, ValueError):
                        raise InvalidBundle("Deleted integer identity is malformed") from None
                    if scalar(value) != identity[0] or not 0 <= value < 2**63:
                        raise InvalidBundle("Deleted integer identity is malformed")
                    high = max(high, value)
                if high:
                    db.execute(
                        "INSERT INTO sqlite_sequence(name, seq) SELECT ?, ? "
                        "WHERE NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = ?)",
                        (table, high, table),
                    )
                    db.execute(
                        "UPDATE sqlite_sequence SET seq = max(seq, ?) WHERE name = ?", (high, table)
                    )
            if self.rebuild:
                self.rebuild(db)
            db.commit()
        except BaseException:
            db.close()
            raise
        if self._db is not None:
            self._db.close()
        self._db, self.before, self.original, self.columns = db, before, rows, columns

    @contextmanager
    def transaction(self, operation_id=None):
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._load()
                self._operation = operation_id or uuid.uuid4().hex
                if not isinstance(self._operation, str) or len(self._operation) > 128:
                    raise InvalidBundle("Name a bounded canonical operation ID")
                if self.source.bundle.operation_committed(self.before, self._operation):
                    raise InvalidBundle("Operation already committed; read its canonical result")
            self._depth += 1
            try:
                yield self
                if outer:
                    self._flush()
            except BaseException:
                self._db.rollback()
                raise
            finally:
                self._depth -= 1

    def execute(self, *args, **kwargs):
        return self._db.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._db.executemany(*args, **kwargs)

    def cursor(self):
        return self._db.cursor()

    def _flush(self):
        definitions = source_census(self.before, self.source.source)["tables"]
        upserts, deletions = {}, {}
        for table, old in self.original.items():
            names = self.columns[table]
            keys = [c["name"] for c in definitions[table]["columns"] if c["pk"]]
            if not keys:
                raise InvalidBundle("Canonical consumers require explicit stable row identities")
            old_by_identity = {tuple(tuple(scalar(r[k])) for k in keys): r for r in old.values()}
            seen, changed = set(), []
            for values in self._db.execute("SELECT * FROM " + _quoted(table)):
                row = dict(zip(names, values))
                prior = old_by_identity.get(tuple(tuple(scalar(row[k])) for k in keys), {})
                row = {**prior, **row}  # preserve source columns unknown to this code
                entry = record_entry(self.source.source, table, row, definitions[table]["columns"])
                seen.add(entry.id)
                if old.get(entry.id) != row:
                    changed.append(row)
            if changed:
                upserts[table] = changed
            missing = set(old) - seen
            if missing:
                deletions[table] = sorted(missing)
        if upserts or deletions:
            self._pending = (self.before, upserts, deletions, self._operation or uuid.uuid4().hex)
            self.retry_pending()
        self._db.commit()

    def retry_pending(self):
        """Only retry the exact retained before/diff/ID after an uncertain reply."""
        with self._lock:
            if self._closed:
                raise InvalidBundle("Canonical consumer session has ended")
            if self._pending is None:
                raise InvalidBundle("No original canonical transaction is pending")
            before, upserts, deletions, operation = self._pending
            try:
                result = self.source.write(
                    before, upserts=upserts, deletions=deletions, operation_id=operation
                )
            except (InvalidBundle, BoxOperationRefused, WriteConflict):
                # A failed validation/CAS may leave unreachable staging files,
                # but must not poison the cached consumer. Unknown I/O outcomes
                # retain the exact pending transaction and still require retry.
                latest = self.source.snapshot()
                if not self.source.bundle.operation_committed(latest, operation):
                    self._pending = None
                raise
            self._pending = None
            return result

    def commit(self):
        if not self._depth:
            raise InvalidBundle("Index writes require an explicit canonical transaction")

    def close(self):
        with self._lock:
            self._closed = True
            self._db.close()

    def __enter__(self):
        self._context = self.transaction(getattr(self, "operation_id", None))
        return self._context.__enter__()

    def __exit__(self, *exc):
        try:
            return self._context.__exit__(*exc)
        finally:
            self.close()


def canonical_methods(cls):
    """One snapshot and atomic commit per public consumer operation, including nesting."""

    def wrap(method):
        @functools.wraps(method)
        def call(self, *args, **kwargs):
            operation_id = kwargs.pop("operation_id", None)
            with self._conn.transaction(operation_id):
                return method(self, *args, **kwargs)

        return call

    for name, method in list(vars(cls).items()):
        if not name.startswith("_") and name != "close" and callable(method):
            setattr(cls, name, wrap(method))
    return cls
