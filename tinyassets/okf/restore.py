"""Offline restore proof into a new SQLite copy, never an activation or live writer."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter
from pathlib import Path

from tinyassets.okf.bundle import InvalidBundle
from tinyassets.okf.migration import _quoted, _verify_backup, read_snapshot, restore_row, scalar
from tinyassets.okf.records import source_census, validate_record


def _predicate(filters):
    clauses, params = [], []
    for column, values in filters.items():
        terms = []
        nonnull = [value for value in values if value is not None]
        if nonnull:
            terms.append(_quoted(column) + " IN (" + ",".join("?" for _ in nonnull) + ")")
            params.extend(nonnull)
        if None in values:
            terms.append(_quoted(column) + " IS NULL")
        if not terms:
            raise InvalidBundle("Restore scope cannot be empty")
        clauses.append("(" + " OR ".join(terms) + ")")
    if not clauses:
        raise InvalidBundle("Restore requires an explicit scope")
    return " AND ".join(clauses), params


def _preserved_rows(conn, census):
    """Compare every excluded/peer row, including NULLs and duplicate rows."""
    result = {}
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND substr(name, 1, 7) != 'sqlite_'"
    ).fetchall()
    for (table,) in tables:
        query, params = "SELECT * FROM " + _quoted(table), []
        if table in census["tables"]:
            predicate, params = _predicate(census["tables"][table]["scope"])
            query += " WHERE (" + predicate + ") IS NOT TRUE"
        result[table] = Counter(
            json.dumps([scalar(value) for value in row], sort_keys=True)
            for row in conn.execute(query, params)
        )
    return result


def restore_copy(snapshot, source, destination):
    """Project canonical rows into a new verified backup copy, preserving peer rows.

    The caller supplies the same independently verified offline backup and scope
    as migration. No SQL from owner-editable frontmatter is executed. A receipt
    proves canonical data restoration, not that derived indexes or a live app
    are ready. Failed destinations are retained as failed evidence, not reused.
    """
    _verify_backup(source)
    census = source_census(snapshot, source.source)
    _, original = read_snapshot(
        source.path, source=source.source, scopes=source.scopes, excluded=source.excluded
    )
    original["backup_sha256"] = source.expected_sha256
    if census != original:
        raise InvalidBundle("Restore needs the original verified migration scope and census")
    expected = {}
    for key, entry in snapshot.entries.items():
        if entry.metadata.get("source") != source.source:
            continue
        table = entry.metadata.get("table")
        if table not in census["tables"] or key != entry.id:
            raise InvalidBundle("Restore contains an unclassified canonical record")
        row = validate_record(entry, source.source, table, census["tables"][table])
        expected[key] = (table, row)
    # A clone of the verified backup preserves schema, indexes, peer rows and
    # excluded transactional data without evaluating frontmatter as SQLite DDL.
    target = Path(destination)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    source_db = sqlite3.connect(
        Path(source.path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True
    )
    dest = sqlite3.connect(target.resolve().as_uri() + "?mode=rw", uri=True)
    try:
        source_db.backup(dest)
        dest.execute("PRAGMA journal_mode=DELETE")
        # Foreign-key relationships are restored together; checks run before
        # success and all scoped inserts occur in one destination transaction.
        dest.execute("PRAGMA foreign_keys=OFF")
        with dest:
            dest.execute("BEGIN IMMEDIATE")
            preserved = _preserved_rows(dest, census)
            # Trigger side effects must not mutate peer or transactional rows.
            # Preserve their verified backup definitions, reinstalling them only
            # after all canonical rows are restored.
            triggers = dest.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
            for name, _ in triggers:
                dest.execute("DROP TRIGGER " + _quoted(name))
            for table, definition in census["tables"].items():
                predicate, params = _predicate(definition["scope"])
                dest.execute("DELETE FROM " + _quoted(table) + " WHERE " + predicate, params)
            for table, row in expected.values():
                values = dict(row)
                if "__migration_rowid__" in values:
                    names = {
                        column["name"].casefold() for column in census["tables"][table]["columns"]
                    }
                    alias = next(name for name in ("rowid", "_rowid_", "oid") if name not in names)
                    values[alias] = values.pop("__migration_rowid__")
                dest.execute(
                    "INSERT OR ABORT INTO "
                    + _quoted(table)
                    + " ("
                    + ",".join(_quoted(name) for name in values)
                    + ") VALUES ("
                    + ",".join("?" for _ in values)
                    + ")",
                    list(values.values()),
                )
            for _, sql in triggers:
                dest.execute(sql)
            if dest.execute("PRAGMA foreign_key_check").fetchone():
                raise InvalidBundle("Restored source violates foreign keys")
            if _preserved_rows(dest, census) != preserved:
                raise InvalidBundle("Restore changed peer or excluded rows")
        if dest.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise InvalidBundle("Restored source failed SQLite integrity checking")
    finally:
        source_db.close()
        dest.close()
    _verify_backup(source)
    rows, _ = read_snapshot(
        target, source=source.source, scopes=source.scopes, excluded=source.excluded
    )

    def typed(row):
        return {key: scalar(value) for key, value in row.items()}

    restored = {entry.id: (entry.metadata["table"], typed(restore_row(entry))) for entry in rows}
    if restored != {key: (table, typed(row)) for key, (table, row) in expected.items()}:
        raise InvalidBundle("Restored canonical values differ from the owner snapshot")
    with target.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {
        "source": source.source,
        "rows": len(rows),
        "sha256": digest,
        "revision": snapshot.manifest["revision"],
        "activated": False,
        "excluded_stores_require_review": dict(source.excluded),
    }
