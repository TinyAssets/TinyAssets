"""Lossless read-only census of scoped SQLite snapshots for the C12 cutover.

This is preparation, never a startup migration. The deploy transaction supplies
verified backup snapshots and an independently resolved owner-to-scope mapping.
No live database is modified or removed. Unclassified tables stop the census.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from tinyassets.okf.bundle import BundleSnapshot, Entry, InvalidBundle, KnowledgeBundle


def scalar(value):
    if value is None:
        return ["null", None]
    if isinstance(value, bytes):
        return ["blob", base64.b64encode(value).decode("ascii")]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        return ["real", value.hex()]
    if isinstance(value, str):
        return ["text", value]
    raise InvalidBundle(f"Unsupported SQLite value: {type(value).__name__}")


def restore_scalar(value):
    kind, body = value
    if kind == "null" and body is None:
        return None
    if kind == "blob":
        return base64.b64decode(body, validate=True)
    if kind == "integer":
        return int(body)
    if kind == "real":
        return float.fromhex(body)
    if kind == "text" and isinstance(body, str):
        return body
    raise InvalidBundle("Invalid encoded SQLite scalar")


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def read_snapshot(
    path: Path, *, source: str, scopes: dict[str, dict[str, list]], excluded: dict[str, str]
) -> tuple[list[Entry], dict]:
    """Read one consistent source view, retaining columns, types, keys and schema.

    Every included table requires explicit scoped column values. Every other
    user table needs a named exclusion reason (derived index or transactional
    state); unknown tables fail. Callers must resolve scope ownership outside
    this trusted, offline conversion function.
    """
    if not source or not scopes or set(scopes) & set(excluded):
        raise InvalidBundle("Name the source and classify disjoint scoped/excluded tables")
    if any(not isinstance(reason, str) or not reason.strip() for reason in excluded.values()):
        raise InvalidBundle("Each excluded table needs a reason")
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise InvalidBundle("Migration requires a regular verified snapshot file")
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    entries, inventory = [], {"source": source, "tables": {}, "excluded": dict(excluded)}
    try:
        conn.execute("BEGIN")
        tables = dict(
            conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' "
                "AND substr(name, 1, 7) != 'sqlite_'"
            )
        )
        if set(tables) != set(scopes) | set(excluded):
            raise InvalidBundle(
                f"Source census mismatch: {sorted(set(tables) ^ (set(scopes) | set(excluded)))}"
            )
        for table, filters in sorted(scopes.items()):
            if not filters or any(
                not isinstance(values, list) or not values for values in filters.values()
            ):
                raise InvalidBundle("Every migrated table needs nonempty explicit scope values")
            columns = [dict(row) for row in conn.execute(f"PRAGMA table_info({_quoted(table)})")]
            names = {column["name"] for column in columns}
            folded_names = {name.casefold() for name in names}
            if not set(filters) <= names:
                raise InvalidBundle("A scope column is absent from the source table")
            pk = [
                column["name"] for column in sorted(columns, key=lambda c: c["pk"]) if column["pk"]
            ]
            rowid = next(
                (alias for alias in ("rowid", "_rowid_", "oid") if alias not in folded_names), None
            )
            if not pk and rowid is None:
                raise InvalidBundle("A table without a primary key needs an accessible rowid")
            predicates, parameters = [], []
            for key, values in filters.items():
                nonnull = [value for value in values if value is not None]
                alternatives = []
                if nonnull:
                    alternatives.append(f"{_quoted(key)} IN ({','.join('?' for _ in nonnull)})")
                    parameters.extend(nonnull)
                if None in values:
                    alternatives.append(f"{_quoted(key)} IS NULL")
                predicates.append("(" + " OR ".join(alternatives) + ")")
            predicate = " AND ".join(predicates)
            prefix = "" if pk else f"{_quoted(rowid)} AS __migration_rowid__, "
            if "__migration_rowid__" in folded_names:
                raise InvalidBundle("Reserved migration rowid column in source")
            rows = [
                dict(row)
                for row in conn.execute(
                    f"SELECT {prefix}* FROM {_quoted(table)} WHERE {predicate}", parameters
                )
            ]
            table_entries = []
            for row in rows:
                identity = (
                    [scalar(row[key]) for key in pk] if pk else [scalar(row["__migration_rowid__"])]
                )
                encoded = {name: scalar(value) for name, value in row.items()}
                key = json.dumps(
                    [source, table, identity], ensure_ascii=False, separators=(",", ":")
                )
                entry_id = hashlib.sha256(key.encode()).hexdigest()
                body_field = next(
                    (
                        field
                        for field in ("content", "summary", "value", "reflection")
                        if isinstance(row.get(field), str)
                    ),
                    None,
                )
                body = row[body_field] if body_field else ""
                if body_field:
                    del encoded[body_field]
                table_entries.append(
                    Entry(
                        entry_id,
                        "sqlite-record",
                        body,
                        {
                            "source": source,
                            "table": table,
                            "identity": identity,
                            "body_field": body_field,
                            "fields": encoded,
                        },
                    )
                )
            table_entries.sort(key=lambda entry: entry.id)
            digest = hashlib.sha256(b"".join(entry.encode() for entry in table_entries)).hexdigest()
            inventory["tables"][table] = {
                "columns": columns,
                "sql": tables[table],
                "scope": filters,
                "count": len(rows),
                "sha256": digest,
            }
            entries.extend(table_entries)
        # Round-trip every value before any bundle write is offered to a caller.
        if len({entry.id for entry in entries}) != len(entries):
            raise InvalidBundle("Duplicate migration entry identities")
        for entry in entries:
            restored = restore_row(Entry.decode(entry.encode()))
            if {name: scalar(value) for name, value in restored.items()} != {
                **entry.metadata["fields"],
                **(
                    {entry.metadata["body_field"]: scalar(entry.body)}
                    if entry.metadata["body_field"]
                    else {}
                ),
            }:
                raise InvalidBundle("SQLite row did not round-trip losslessly")
        return entries, inventory
    finally:
        conn.close()


def restore_row(entry: Entry):
    """Decode a source row; the editable Markdown body wins over the old prose."""
    if entry.type != "sqlite-record":
        raise InvalidBundle("Expected a migrated SQLite record")
    row = {name: restore_scalar(value) for name, value in entry.metadata["fields"].items()}
    field = entry.metadata["body_field"]
    if field is not None:
        if field in row:
            raise InvalidBundle("Body field has two canonical values")
        row[field] = entry.body
    return row


@dataclass(frozen=True)
class SnapshotSource:
    path: Path
    source: str
    expected_sha256: str
    scopes: dict[str, dict[str, list]]
    excluded: dict[str, str]


@dataclass(frozen=True)
class MigrationPlan:
    before: BundleSnapshot
    sources: tuple[SnapshotSource, ...]
    entries: tuple[Entry, ...]
    census: tuple[dict, ...]
    operation_id: str


def _verify_backup(source: SnapshotSource):
    path = Path(source.path)
    if path.is_symlink() or not path.is_file():
        raise InvalidBundle("Migration requires a regular backup snapshot")
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")):
        raise InvalidBundle("Migration needs a standalone, checkpointed backup")
    with path.open("rb") as backup:
        digest = hashlib.file_digest(backup, "sha256").hexdigest()
    if digest != source.expected_sha256:
        raise InvalidBundle("Backup checksum differs from the verified source")


def prepare_migration(
    bundle: KnowledgeBundle, sources: list[SnapshotSource], *, operation_id: str
) -> MigrationPlan:
    """Census every verified source before staging a single byte.

    The deploy operator still owns a cross-store-consistent backup and paused
    legacy writers. No function here activates the new path or retires a DB.
    """
    if not sources or not operation_id or len({s.source for s in sources}) != len(sources):
        raise InvalidBundle("Migration needs unique sources and a stable operation id")
    before = bundle.snapshot()
    if before.entries or "revision" in before.manifest:
        raise InvalidBundle("Initial migration requires an uninitialized knowledge store")
    entries, census = [], []
    for source in sources:
        _verify_backup(source)
        records, inventory = read_snapshot(
            source.path, source=source.source, scopes=source.scopes, excluded=source.excluded
        )
        _verify_backup(source)
        inventory["backup_sha256"] = source.expected_sha256
        entries.extend(records)
        census.append(inventory)
    if len({entry.id for entry in entries}) != len(entries):
        raise InvalidBundle("Migration sources contain duplicate entry identities")
    return MigrationPlan(before, tuple(sources), tuple(entries), tuple(census), operation_id)


def commit_migration(bundle: KnowledgeBundle, plan: MigrationPlan) -> dict:
    """Commit and verify the planned revision; keep every legacy backup untouched.

    On a failed/uncertain outcome retain this exact plan for retry. A successful
    receipt proves projection only, never a completed live cutover.
    """
    for source in plan.sources:
        _verify_backup(source)
    before = replace(plan.before, manifest={**plan.before.manifest, "migration": list(plan.census)})
    bundle.commit(
        before, {entry.id: entry for entry in plan.entries}, operation_id=plan.operation_id
    )
    projected = bundle.snapshot()
    if (
        projected.manifest.get("operation_id") != plan.operation_id
        or (
            {key: entry.encode() for key, entry in projected.entries.items()}
            != {entry.id: entry.encode() for entry in plan.entries}
        )
        or projected.manifest.get("migration") != list(plan.census)
    ):
        raise InvalidBundle("Committed projection differs from plan; inspect before cutover")
    return {
        "operation_id": plan.operation_id,
        "entries": len(plan.entries),
        "revision": projected.manifest["revision"],
        "census": list(plan.census),
    }
