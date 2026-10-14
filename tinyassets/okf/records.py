"""Owner-box source adapters for migrated records; no legacy database fallback.

A source may span related tables. One original bundle snapshot and operation ID
commit all of them together. Consumers use row values, while editable Markdown
and unknown metadata remain canonical in the existing OKF revision.
"""

from __future__ import annotations

import hashlib
import json

from tinyassets.okf.bundle import Entry, InvalidBundle, KnowledgeBundle
from tinyassets.okf.migration import restore_row, scalar

BODY_FIELDS = (
    "content",
    "summary",
    "text",
    "value",
    "reflection",
    "observation",
    "public_description",
    "prose",
    "description",
)


def source_census(snapshot, source):
    matches = [
        item for item in snapshot.manifest.get("migration", []) if item.get("source") == source
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("tables"), dict):
        raise InvalidBundle("Canonical source has not completed migration")
    return matches[0]


def record_entry(source, table, row, columns, *, template=None):
    """The same typed identity and sole body representation as snapshot migration."""
    names = {column["name"] for column in columns}
    keys = [
        column["name"] for column in sorted(columns, key=lambda item: item["pk"]) if column["pk"]
    ]
    if not keys:
        names.add("__migration_rowid__")
        keys = ["__migration_rowid__"]
    if not isinstance(row, dict) or set(row) != names:
        raise InvalidBundle("A record must retain every source column")
    if any(
        type(value) not in (type(None), bytes, int, float, str)
        or isinstance(value, float)
        and value != value
        for value in row.values()
    ):
        raise InvalidBundle("Record values must be exact portable SQLite scalars")
    identity = [scalar(row[key]) for key in keys]
    encoded = json.dumps([source, table, identity], ensure_ascii=False, separators=(",", ":"))
    key = hashlib.sha256(encoded.encode()).hexdigest()
    if template is not None and template.id != key:
        raise InvalidBundle("An update cannot change its canonical identity")
    body_field = template.metadata.get("body_field") if template is not None else None
    if not isinstance(row.get(body_field), str):
        body_field = next((field for field in BODY_FIELDS if isinstance(row.get(field), str)), None)
    metadata = dict(template.metadata) if template else {}
    metadata.update(
        source=source,
        table=table,
        identity=identity,
        body_field=body_field,
        fields={name: scalar(value) for name, value in row.items() if name != body_field},
    )
    return Entry(key, "sqlite-record", row[body_field] if body_field else "", metadata)


def validate_record(entry, source, table, definition):
    if entry.type != "sqlite-record":
        raise InvalidBundle("Expected a canonical source record")
    row = restore_row(entry)
    expected = record_entry(source, table, row, definition["columns"], template=entry)
    if (
        entry.metadata.get("source") != source
        or entry.metadata.get("table") != table
        or expected.metadata["identity"] != entry.metadata.get("identity")
    ):
        raise InvalidBundle("Canonical record identity differs from its row")
    for column, values in definition["scope"].items():
        if scalar(row[column]) not in [scalar(value) for value in values]:
            raise InvalidBundle("Canonical record is outside its declared source scope")
    return row


class SourceRecords:
    def __init__(self, bundle: KnowledgeBundle, source: str):
        if not isinstance(source, str) or not source:
            raise InvalidBundle("Name the canonical source")
        self.bundle, self.source = bundle, source

    @classmethod
    def bind(cls, *, owner: str, center: str, source: str):
        from tinyassets.agent_loop.served_chat import configured_box_provider

        provider, _ = configured_box_provider()
        if provider is None:
            raise InvalidBundle("Canonical owner-box provider is unavailable")
        return cls(KnowledgeBundle(provider, provider.bind(center, account_id=owner)), source)

    def snapshot(self):
        snapshot = self.bundle.snapshot()
        self.rows(snapshot)  # validate before any consumer sees rows
        return snapshot

    def rows(self, snapshot):
        if self.bundle.access_check is not None:
            self.bundle.access_check()
        if snapshot.scope != self.bundle.scope:
            raise InvalidBundle("Source snapshot belongs to another owner or placement")
        census = source_census(snapshot, self.source)
        rows = {table: {} for table in census["tables"]}
        for key, entry in snapshot.entries.items():
            if entry.metadata.get("source") != self.source:
                continue
            table = entry.metadata.get("table")
            if table not in rows or key != entry.id:
                raise InvalidBundle("Source record belongs to an unclassified table")
            rows[table][key] = validate_record(entry, self.source, table, census["tables"][table])
        return rows

    def write(self, before, *, upserts=None, deletions=None, operation_id):
        existing = self.rows(before)
        census = source_census(before, self.source)
        changes = {}
        for table, keys in (deletions or {}).items():
            if table not in existing or len(set(keys)) != len(keys):
                raise InvalidBundle("Invalid source deletion set")
            for key in keys:
                if key not in existing[table]:
                    raise InvalidBundle("Cannot delete a missing or foreign source record")
                changes[key] = None
        for table, rows in (upserts or {}).items():
            if table not in existing:
                raise InvalidBundle("Cannot write an unclassified source table")
            for row in rows:
                entry = record_entry(self.source, table, row, census["tables"][table]["columns"])
                if entry.id in changes:
                    raise InvalidBundle("A record occurs twice in one transaction")
                original = before.entries.get(entry.id)
                if original is not None:
                    entry = record_entry(
                        self.source,
                        table,
                        row,
                        census["tables"][table]["columns"],
                        template=original,
                    )
                validate_record(entry, self.source, table, census["tables"][table])
                changes[entry.id] = entry
        if not changes:
            raise InvalidBundle("A source write needs a concrete change")
        return self.bundle.commit(before, changes, operation_id=operation_id)
