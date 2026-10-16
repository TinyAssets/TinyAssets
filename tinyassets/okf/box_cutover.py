"""Knowledge projection inside the gated, one-shot owner-box layout move.

The C6 move has stopped writers, verified the paired full backup, and proved
each source tree's owner before calling this module. No startup or second
migration entry point exists. The original databases stay in the copied box
and in the paired backup for restoration.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import uuid
from dataclasses import dataclass, replace
from pathlib import Path

from tinyassets.okf.bundle import Entry, InvalidBundle, KnowledgeBundle
from tinyassets.okf.migration import commit_migration
from tinyassets.okf.namespace_census import (
    NamespaceSource,
    ReviewedBinding,
    census_sources,
    prepare_reviewed_migration,
)

# One name per canonical consumer. Unknown tables and duplicate source files
# fail the census rather than being guessed into an owner's records.
_FILES = {
    "daemon": "daemon_brain.db",
    "project": ".project_memory.db",
    "episodic": "episodic.db",
    "knowledge": "knowledge.db",
    "promises": "promises.db",
    "versions": "versions.db",
    "temporal": "temporal.db",
}
_COLUMNS = {
    "daemon": {table: "daemon_id" for table in (
        "daemon_brain_entries", "daemon_memory_promotions", "daemon_memory_events"
    )},
    "project": {table: "project_id" for table in (
        "project_memory", "project_memory_history"
    )},
    "episodic": {table: "universe_id" for table in (
        "scene_summaries", "episodic_facts", "style_observations", "reflections"
    )},
    "knowledge": {table: "universe_id" for table in (
        "entities", "edges", "facts", "communities"
    )},
    "promises": {"series_promises": "universe_id"},
    "versions": {"draft_versions": "universe_id"},
    "temporal": {"temporal_facts": "branch_id"},
}
_VECTOR_DIRS = ("lancedb", ".lance", "lance")


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise InvalidBundle(f"linked vector source: {path}")
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big") + relative)
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            digest.update(b"d")
        elif stat.S_ISREG(info.st_mode):
            digest.update(b"f")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            raise InvalidBundle(f"non-regular vector source: {path}")
    return digest.hexdigest()


def _portable(value):
    """Retain Arrow scalar types and exact floating bits in portable metadata."""
    if value is None:
        return ["null", None]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        return ["real", value.hex()]
    if isinstance(value, str):
        return ["text", value]
    if isinstance(value, bytes):
        return ["blob", base64.b64encode(value).decode("ascii")]
    if isinstance(value, list):
        return ["list", [_portable(item) for item in value]]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return ["object", {key: _portable(item) for key, item in value.items()}]
    raise InvalidBundle(f"Unsupported vector value: {type(value).__name__}")


def _vector_snapshot(source: Path, snapshots: Path, center: str,
                     admitted_universes: set[str]):
    import lancedb

    entries, inventories, copies = [], [], []
    candidates = [
        path for parent in (source, *source.iterdir()) if parent.is_dir()
        and not parent.is_symlink() for name in _VECTOR_DIRS
        if (path := parent / name).exists() or path.is_symlink()
    ]
    for index, path in enumerate(candidates):
        if path.is_symlink() or not path.is_dir():
            raise InvalidBundle(f"{center}: invalid vector directory {path}")
        digest = _tree_digest(path)
        copy = snapshots / f"{center}-vector-{index}"
        shutil.copytree(path, copy, symlinks=False)
        if _tree_digest(copy) != digest or _tree_digest(path) != digest:
            raise InvalidBundle(f"{center}: vector snapshot changed")
        db = lancedb.connect(str(copy))
        tables = db.list_tables()
        table_names = tables.tables if hasattr(tables, "tables") else tables
        counts = {}
        for name in sorted(table_names):
            table = db.open_table(name)
            ordinal = 0
            expected_rows = table.count_rows()
            for batch in table.search().limit(None).to_batches(batch_size=512):
                for row in batch.to_pylist():
                    entries.extend(_vector_row(
                        row, source=source, path=path, name=name, ordinal=ordinal,
                        digest=digest, center=center,
                        admitted_universes=admitted_universes,
                    ))
                    ordinal += 1
            counts[name] = ordinal
            if ordinal != expected_rows:
                raise InvalidBundle(f"{center}: vector table scan missed rows")
        inventories.append({
            "source": "vector", "path": path.relative_to(source).as_posix(),
            "backup_sha256": digest, "tables": counts,
        })
        copies.append((copy, digest))
    return tuple(entries), tuple(inventories), tuple(copies)


def _vector_row(row, *, source, path, name, ordinal, digest, center,
                admitted_universes):
    if row.get("chunk_id") == "__seed__":
        return ()
    universe = row.get("universe_id")
    if universe not in (None, "") and universe not in admitted_universes:
        raise InvalidBundle(f"{center}: vector row has a foreign universe scope")
    if path.parent != source and universe not in (None, "", path.parent.name):
        raise InvalidBundle(f"{center}: vector row differs from its universe root")
    body = row.get("text", "")
    if not isinstance(body, str):
        raise InvalidBundle(f"{center}: vector text is not UTF-8 prose")
    identity = [path.relative_to(source).as_posix(), name, ordinal]
    key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
    metadata = {
        "source": "vector", "table": name, "identity": identity,
        "fields": {field: _portable(value) for field, value in row.items()
                   if field != "text"},
        "snapshot_sha256": digest,
    }
    return (Entry("vector-" + key, "vector-record", body, metadata),)


def _seed(name: str, conn: sqlite3.Connection) -> None:
    if name == "daemon":
        from tinyassets.daemon_brain import initialize_daemon_brain

        initialize_daemon_brain(conn)
    elif name == "project":
        from tinyassets.memory.project import PROJECT_SCHEMA

        conn.executescript(PROJECT_SCHEMA)
    elif name == "episodic":
        from tinyassets.memory.episodic import initialize_episodic_index

        initialize_episodic_index(conn)
    elif name == "knowledge":
        from tinyassets.knowledge.knowledge_graph import initialize_knowledge_index

        initialize_knowledge_index(conn)
    elif name == "promises":
        from tinyassets.memory.promises import _SCHEMA

        conn.executescript(_SCHEMA)
    elif name == "versions":
        from tinyassets.memory.versioning import _SCHEMA

        conn.executescript(_SCHEMA)
    elif name == "temporal":
        from tinyassets.memory.temporal import _TEMPORAL_SCHEMA

        conn.executescript(_TEMPORAL_SCHEMA)


def _source_path(center: Path, name: str) -> Path | None:
    filename = _FILES[name]
    # Known legacy stores may be at the center root or inside one universe.
    # Dot-directories are agent/platform work areas, never universe roots.
    # A second match requires an explicit consolidation, never a first match.
    matches = [path for path in (center / filename, *center.glob("*/" + filename))
               if not (path.parent != center and path.parent.name.startswith("."))
               and (path.exists() or path.is_symlink())]
    if len(matches) > 1:
        raise InvalidBundle(f"{center.name}: multiple {name} databases")
    if not matches:
        return None
    path = matches[0]
    if any(parent.is_symlink() for parent in (path, path.parent)):
        raise InvalidBundle(f"{center.name}: linked {name} database")
    if not stat.S_ISREG(path.lstat().st_mode):
        raise InvalidBundle(f"{center.name}: non-regular {name} database")
    return path


def preflight_center(source: Path) -> None:
    """Reject knowledge source errors that can be established without writes."""
    from tinyassets.sqlite_readonly import readonly_db

    center = source.name
    admitted_universes = {center}
    registry = source / ".tinyassets.db"
    if registry.exists() or registry.is_symlink():
        if registry.is_symlink() or not stat.S_ISREG(registry.lstat().st_mode):
            raise InvalidBundle(f"{center}: invalid daemon registry")
        with readonly_db(registry) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='universes'").fetchone():
                for (universe,) in db.execute("SELECT universe_id FROM universes"):
                    root = source / universe
                    if not root.is_dir() or root.is_symlink() or root.parent != source:
                        raise InvalidBundle(f"{center}: invalid registered universe")
                    admitted_universes.add(universe)
    for name in _FILES:
        path = _source_path(source, name)
        if path is None:
            continue
        with readonly_db(path) as db:
            tables = {
                row[1]: row[2] for row in db.execute("PRAGMA table_list")
                if row[0] == "main" and not row[1].startswith("sqlite_")
            }
            expected = set(_COLUMNS[name])
            normal = {table for table, kind in tables.items() if kind == "table"}
            if normal != expected:
                raise InvalidBundle(
                    f"{center}: {name} tables differ: {sorted(normal ^ expected)}"
                )
            if any(kind not in ("table", "virtual", "shadow") for kind in tables.values()):
                raise InvalidBundle(f"{center}: unclassified {name} table")
            for table, column in _COLUMNS[name].items():
                quoted_table = '"' + table.replace('"', '""') + '"'
                quoted_column = '"' + column.replace('"', '""') + '"'
                for (value,) in db.execute(
                    f"SELECT DISTINCT {quoted_column} FROM {quoted_table}"
                ):
                    if name in ("episodic", "knowledge", "promises", "versions"):
                        if value is None and name == "knowledge":
                            continue
                        if value not in admitted_universes:
                            raise InvalidBundle(
                                f"{center}: {name}.{table} has a foreign universe scope"
                            )
                        if path.parent != source and value != path.parent.name:
                            raise InvalidBundle(
                                f"{center}: {name}.{table} differs from its universe root"
                            )
    for parent in (source, *source.iterdir()):
        if not parent.is_dir() or parent.is_symlink():
            continue
        for name in _VECTOR_DIRS:
            path = parent / name
            if path.exists() or path.is_symlink():
                if path.is_symlink() or not path.is_dir():
                    raise InvalidBundle(f"{center}: invalid vector directory {path}")
                _tree_digest(path)


@dataclass(frozen=True)
class CenterKnowledge:
    owner: str
    center: str
    sources: tuple[NamespaceSource, ...]
    bindings: tuple[ReviewedBinding, ...]
    source_rows: int
    vector_entries: tuple[Entry, ...]
    vector_inventory: tuple[dict, ...]
    vector_copies: tuple[tuple[Path, str], ...]


def prepare_center(source: Path, snapshots: Path, *, owner: str) -> CenterKnowledge:
    """Census one admitted center from stopped, physically isolated source files."""
    center = source.name
    admitted_universes = {center}
    registered_daemons = set()
    registry = source / ".tinyassets.db"
    if registry.exists():
        if registry.is_symlink() or not stat.S_ISREG(registry.lstat().st_mode):
            raise InvalidBundle(f"{center}: invalid daemon registry")
        with sqlite3.connect(registry.resolve().as_uri() + "?mode=ro", uri=True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='universes'").fetchone():
                for (universe,) in db.execute("SELECT universe_id FROM universes"):
                    root = source / universe
                    if not root.is_dir() or root.is_symlink() or root.parent != source:
                        raise InvalidBundle(f"{center}: invalid registered universe")
                    admitted_universes.add(universe)
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='author_definitions'").fetchone():
                registered_daemons = {
                    row[0] for row in db.execute("SELECT author_id FROM author_definitions")
                }
    definitions, bindings = [], []
    total = 0
    for name in _FILES:
        original = _source_path(source, name)
        backup = snapshots / (center + "-" + name + ".db")
        if original is None:
            with sqlite3.connect(backup) as target:
                target.row_factory = sqlite3.Row
                _seed(name, target)
        else:
            # SQLite's backup API makes a standalone, checkpointed view even
            # when the stopped source was in WAL mode. No live DB is opened RW.
            with sqlite3.connect(original.resolve().as_uri() + "?mode=ro", uri=True) as live:
                with sqlite3.connect(backup) as target:
                    live.backup(target)
        digest = hashlib.sha256(backup.read_bytes()).hexdigest()
        with sqlite3.connect(backup) as db:
            tables = {
                row[1]: row[2] for row in db.execute("PRAGMA table_list")
                if row[0] == "main" and not row[1].startswith("sqlite_")
            }
            expected = set(_COLUMNS[name])
            normal = {table for table, kind in tables.items() if kind == "table"}
            if normal != expected:
                raise InvalidBundle(f"{center}: {name} tables differ: {sorted(normal ^ expected)}")
            excluded = {
                table: "Disposable derived full-text index"
                for table, kind in tables.items() if kind in ("virtual", "shadow")
            }
            if set(tables) != normal | set(excluded):
                raise InvalidBundle(f"{center}: unclassified {name} table")
            definitions.append(NamespaceSource(
                backup, name, digest, _COLUMNS[name], excluded
            ))
            for table, column in _COLUMNS[name].items():
                quoted_table = '"' + table.replace('"', '""') + '"'
                quoted_column = '"' + column.replace('"', '""') + '"'
                values = [row[0] for row in db.execute(
                    f"SELECT DISTINCT {quoted_column} FROM {quoted_table}"
                )]
                if name in ("episodic", "knowledge", "promises", "versions"):
                    for value in values:
                        if value is None and name == "knowledge":
                            continue  # Legacy broad knowledge is still in this owner's tree.
                        if value not in admitted_universes:
                            raise InvalidBundle(
                                f"{center}: {name}.{table} has a foreign universe scope"
                            )
                        if original is not None and original.parent != source \
                                and value != original.parent.name:
                            raise InvalidBundle(
                                f"{center}: {name}.{table} differs from its universe root"
                            )
                total += db.execute(f"SELECT count(*) FROM {quoted_table}").fetchone()[0]
                # Register already admitted namespaces even if this table has
                # no rows yet. The source tree and registry, not its last
                # writer, attest their ownership.
                allowed = set(values)
                if name in ("episodic", "knowledge", "promises", "versions"):
                    allowed.update(admitted_universes)
                    if name == "knowledge":
                        allowed.add(None)
                elif name == "daemon":
                    allowed.update(registered_daemons)
                for value in sorted(allowed, key=lambda item: (item is not None, str(item))) \
                        or [center]:
                    evidence = hashlib.sha256(
                        f"{owner}\0{center}\0{name}\0{digest}".encode()
                    ).hexdigest()
                    bindings.append(ReviewedBinding(
                        name, table, value, owner, center, digest,
                        "owner-box-source-tree", str(original or source), evidence,
                    ))
    census = census_sources(
        definitions, bindings,
        verify_authority=lambda principal, home: (principal, home) == (owner, center),
    )
    if census.report["unresolved_rows"]:
        raise InvalidBundle(f"{center}: {census.report['unresolved_rows']} unassigned rows")
    vectors, inventory, copies = _vector_snapshot(
        source, snapshots, center, admitted_universes
    )
    return CenterKnowledge(owner, center, tuple(definitions), tuple(bindings), total,
                           vectors, inventory, copies)


def initialize_empty_center(*, owner: str, center: str, source: Path) -> None:
    """Give a newly admitted center the same empty census as the cutover."""
    from tinyassets.agent_loop.served_chat import configured_box_provider

    provider, _ = configured_box_provider()
    if provider is None:
        raise InvalidBundle("Canonical owner-box provider is unavailable")
    bundle = KnowledgeBundle(provider, provider.bind(center, account_id=owner))
    before = bundle.snapshot()
    if "revision" in before.manifest:
        from tinyassets.okf.records import source_census

        for name in _FILES:
            source_census(before, name)
        return
    with tempfile.TemporaryDirectory(prefix="owner-box-empty-") as scratch:
        operation_id = "owner-box-empty-" + uuid.uuid4().hex
        plan = prepare_center(source, Path(scratch), owner=owner)
        if plan.source_rows or plan.vector_entries:
            raise InvalidBundle("New center contains legacy knowledge sources")
        census = census_sources(
            plan.sources, plan.bindings,
            verify_authority=lambda principal, home: (principal, home) == (owner, center),
        )
        migration = prepare_reviewed_migration(
            bundle, census, operation_id=operation_id,
            verify_authority=lambda principal, home: (principal, home) == (owner, center),
        )
        commit_migration(bundle, migration)


def commit_center(plan: CenterKnowledge, tree: Path, state: Path) -> dict:
    """Write one center's complete canonical revision to its copied owner box."""
    from tinyassets.boxes.local import LocalBoxProvider

    provider = LocalBoxProvider(
        boxes_root=tree, state_dir=state,
        owner_of=lambda center: plan.owner if center == plan.center else None,
        allow_unisolated=True,
    )
    try:
        bundle = KnowledgeBundle(provider, provider.bind(plan.center, account_id=plan.owner))
        before = bundle.snapshot()
        operation_id = "owner-box-knowledge-" + hashlib.sha256(
            (plan.owner + "\0" + plan.center).encode()
        ).hexdigest()
        census = census_sources(
            plan.sources, plan.bindings,
            verify_authority=lambda owner, center: (owner, center) == (
                plan.owner, plan.center
            ),
        )
        if before.manifest.get("operation_id") == operation_id:
            from tinyassets.okf.records import source_census

            expected = {entry.id: entry.encode() for _, entry in census.rows}
            expected.update({entry.id: entry.encode() for entry in plan.vector_entries})
            if {key: entry.encode() for key, entry in before.entries.items()} != expected:
                raise InvalidBundle("Prior center conversion differs from stopped sources")
            for source in plan.sources:
                if source_census(before, source.source)["backup_sha256"] != source.expected_sha256:
                    raise InvalidBundle("Prior center conversion used another backup")
            return {"center": plan.center, "owner": plan.owner, "rows": plan.source_rows,
                    "status": "already-converted"}
        migration = prepare_reviewed_migration(
            bundle, census, operation_id=operation_id,
            verify_authority=lambda owner, center: (owner, center) == (
                plan.owner, plan.center
            ),
        )
        for copy, digest in plan.vector_copies:
            if _tree_digest(copy) != digest:
                raise InvalidBundle("Vector snapshot changed before commit")
        migration = replace(
            migration,
            entries=(*migration.entries, *plan.vector_entries),
            census=(*migration.census, *plan.vector_inventory),
        )
        receipt = commit_migration(bundle, migration)
        return {"center": plan.center, "owner": plan.owner,
                "rows": plan.source_rows, "vector_rows": len(plan.vector_entries),
                "status": "converted",
                "revision": receipt["revision"]}
    finally:
        provider.close()


def chown_projection(destination: Path, uid: int) -> None:
    """Offline provider writes as migration root; hand every new file to its owner."""
    root = destination / "brain" / "knowledge"
    if not root.exists():
        raise InvalidBundle("Canonical projection has no knowledge root")
    os.chown(root.parent, uid, uid)
    for directory, dirs, files in os.walk(root, followlinks=False):
        for path in (Path(directory), *(Path(directory) / name for name in dirs + files)):
            if path.is_symlink():
                raise InvalidBundle("Canonical projection contains a link")
            os.chown(path, uid, uid)
