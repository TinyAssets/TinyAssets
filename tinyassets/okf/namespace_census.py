"""Offline reviewed namespace census; unresolved rows never enter owner bundles.

This operator-only preparation API accepts verified snapshots and explicit review
attestations. It cannot infer ownership, activate a store, or delete a source.
The custody directory must be a restricted operator directory outside owner boxes.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from tinyassets.okf.bundle import InvalidBundle
from tinyassets.okf.migration import (
    SnapshotSource,
    _quoted,
    _verify_backup,
    prepare_migration,
    read_snapshot,
    restore_row,
    scalar,
)


@dataclass(frozen=True)
class NamespaceSource:
    path: Path
    source: str
    expected_sha256: str
    namespaces: dict[str, str]  # table -> its declared namespace column
    excluded: dict[str, str]


@dataclass(frozen=True)
class ReviewedBinding:
    source: str
    table: str
    namespace: object
    owner: str
    center: str
    snapshot_sha256: str
    reviewer: str
    review_ref: str
    evidence_sha256: str


def _key(source, table, value):
    return json.dumps([source, table, scalar(value)], ensure_ascii=False, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class NamespaceCensus:
    sources: tuple[NamespaceSource, ...]
    bindings: tuple[ReviewedBinding, ...]
    rows: tuple[tuple[str, object], ...]
    inventory_json: str

    @property
    def report(self):
        return json.loads(self.inventory_json)


def census_sources(sources, bindings, *, verify_authority):
    """Read all namespaces, including NULL and zero rows, before owner projection.

    verify_authority must independently check current center -> owner admission;
    review_ref/evidence_sha256 attest to namespace assignment, not writer billing.
    They are never accepted from an owner-export/model request.
    """
    import sqlite3

    sources, bindings = tuple(sources), tuple(bindings)
    indexed = {source.source: source for source in sources}
    if not sources or len(indexed) != len(sources):
        raise InvalidBundle("Census requires unique named sources")
    assignments = {}
    for binding in bindings:
        source = indexed.get(binding.source)
        if (
            source is None
            or binding.table not in source.namespaces
            or binding.snapshot_sha256 != source.expected_sha256
            or not all(
                isinstance(value, str) and value.strip()
                for value in (binding.owner, binding.center, binding.reviewer, binding.review_ref)
            )
            or len(binding.evidence_sha256) != 64
            or any(c not in "0123456789abcdef" for c in binding.evidence_sha256)
        ):
            raise InvalidBundle("Binding needs complete snapshot-bound review provenance")
        key = _key(binding.source, binding.table, binding.namespace)
        if key in assignments:
            raise InvalidBundle("A namespace has contradictory or duplicate reviewed bindings")
        if verify_authority(binding.owner, binding.center) is not True:
            raise InvalidBundle("Current owner admission rejected the reviewed binding")
        assignments[key] = binding
    rows, inventory = [], []
    unresolved = 0
    for source in sources:
        _verify_backup(source)
        if not source.namespaces or set(source.namespaces) & set(source.excluded):
            raise InvalidBundle("Declare disjoint namespace tables and excluded stores")
        conn = sqlite3.connect(
            Path(source.path).resolve().as_uri() + "?mode=ro&immutable=1", uri=True
        )
        try:
            conn.execute("BEGIN")
            scopes = {}
            for table, column in source.namespaces.items():
                columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(" + _quoted(table) + ")")
                }
                if column not in columns:
                    raise InvalidBundle("Declared namespace column is absent")
                values = [
                    row[0]
                    for row in conn.execute(
                        "SELECT DISTINCT " + _quoted(column) + " FROM " + _quoted(table)
                    )
                ]
                # Empty tables still participate in the complete source census.
                scopes[table] = {column: values or [None]}
        finally:
            conn.close()
        entries, raw = read_snapshot(
            source.path, source=source.source, scopes=scopes, excluded=source.excluded
        )
        _verify_backup(source)
        tables = {}
        for table in source.namespaces:
            table_rows = [e for e in entries if e.metadata["table"] == table]
            groups = {}
            for entry in table_rows:
                value = restore_row(entry)[source.namespaces[table]]
                key = _key(source.source, table, value)
                group = groups.setdefault(
                    key, {"namespace": scalar(value), "rows": 0, "binding": None}
                )
                group["rows"] += 1
                binding = assignments.get(key)
                if binding is None:
                    unresolved += 1
                else:
                    group["binding"] = asdict(binding)
                rows.append((key, entry))
            tables[table] = {
                "column": source.namespaces[table],
                "rows": len(table_rows),
                "namespaces": [groups[key] for key in sorted(groups)],
            }
        inventory.append(
            {
                "source": source.source,
                "backup_sha256": source.expected_sha256,
                "tables": tables,
                "excluded": raw["excluded"],
            }
        )
    report = {
        "format": 1,
        "sources": inventory,
        "unresolved_rows": unresolved,
        "ownership_complete": unresolved == 0,
        "activated": False,
        "source_deletion_permitted": False,
        "bindings": [asdict(b) for b in bindings],
    }
    return NamespaceCensus(sources, bindings, tuple(rows), json.dumps(report, sort_keys=True))


def retain_census(census, custody_root):
    """Durably retain unresolved values in existing private operator custody.

    No owner BoxProvider or export path references this artifact. A failed write
    stays visible for operator inspection; its bytes are never treated as success.
    """
    root = Path(custody_root)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise InvalidBundle("Census custody must be operator-owned and mode 0700")
        for source in census.sources:
            _verify_backup(source)
        assigned = {_key(b.source, b.table, b.namespace) for b in census.bindings}
        doc = {
            "census": census.report,
            "unresolved": [
                base64.b64encode(entry.encode()).decode()
                for key, entry in census.rows
                if key not in assigned
            ],
        }
        data = json.dumps(doc, sort_keys=True).encode()
        digest = hashlib.sha256(data).hexdigest()
        name = "namespace-census-" + digest + ".json"
        out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
        return {
            "path": str(root / name),
            "sha256": digest,
            "unresolved_rows": len(doc["unresolved"]),
            "activated": False,
        }
    finally:
        os.close(fd)


def prepare_reviewed_migration(
    bundle, census, *, operation_id, verify_authority, custody_receipt=None
):
    """Project only this admitted owner's reviewed namespaces; retain sources.

    Re-census immediately: caller mutation, changed backups or newer deletion
    fences cannot silently reuse an earlier plan. Projection is not cutover.
    """
    fresh = census_sources(census.sources, census.bindings, verify_authority=verify_authority)
    if fresh.inventory_json != census.inventory_json:
        raise InvalidBundle("Reviewed namespace census changed")
    if fresh.report["unresolved_rows"]:
        if not isinstance(custody_receipt, dict):
            raise InvalidBundle("Unresolved rows need durable restricted custody before projection")
        path = Path(custody_receipt["path"])
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            raw = stream.read()
        assigned = {_key(b.source, b.table, b.namespace) for b in fresh.bindings}
        expected = {
            "census": fresh.report,
            "unresolved": [
                base64.b64encode(entry.encode()).decode()
                for key, entry in fresh.rows
                if key not in assigned
            ],
        }
        if (
            info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or hashlib.sha256(raw).hexdigest() != custody_receipt.get("sha256")
            or json.loads(raw) != expected
        ):
            raise InvalidBundle("Restricted custody differs from the reviewed census")
    center, owner, _ = bundle.scope
    selected = [b for b in fresh.bindings if (b.owner, b.center) == (owner, center)]
    sources = []
    provenance = {}
    for source in fresh.sources:
        scopes, excluded = {}, dict(source.excluded)
        for table, column in source.namespaces.items():
            matching = [b for b in selected if b.source == source.source and b.table == table]
            if matching:
                scopes[table] = {column: [b.namespace for b in matching]}
                provenance[(source.source, table)] = [asdict(b) for b in matching]
            else:
                excluded[table] = "No reviewed namespace assigned to this owner; retained offline"
        if scopes:
            sources.append(
                SnapshotSource(source.path, source.source, source.expected_sha256, scopes, excluded)
            )
    if not sources:
        raise InvalidBundle("No reviewed namespaces belong to this admitted owner")
    plan = prepare_migration(bundle, sources, operation_id=operation_id)
    reviewed_keys = {_key(b.source, b.table, b.namespace) for b in selected}
    expected = {entry.id: entry.encode() for key, entry in fresh.rows if key in reviewed_keys}
    if {entry.id: entry.encode() for entry in plan.entries} != expected:
        raise InvalidBundle("SQL collation or affinity broadened the reviewed namespace scope")
    inventories = []
    for item in plan.census:
        tables = {
            name: {**definition, "reviewed_bindings": provenance[(item["source"], name)]}
            for name, definition in item["tables"].items()
        }
        inventories.append(
            {
                **item,
                "tables": tables,
                "namespace_census_sha256": _digest(fresh.report),
                "unresolved_rows": fresh.report["unresolved_rows"],
            }
        )
    return replace(plan, census=tuple(inventories))
