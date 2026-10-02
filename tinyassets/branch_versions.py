"""Content-addressed branch snapshot storage.

Spec: docs/vetted-specs.md §publish_version.
Rollback design: docs/design-notes/2026-04-25-surgical-rollback-proposal.md.

A published version is a write-once snapshot of a BranchDefinition's full
topology (node_defs, edges, conditional_edges, state_schema, entry_point)
at the moment of publish. It is immutable after creation.

``publish_version`` mints a new ``branch_version_id`` of the form
``<branch_def_id>@<sha256_prefix8>`` and stores the canonical JSON in
the ``branch_versions`` SQLite table inside the runs database.

Surgical-rollback columns (Task #22 Phase A):
- ``status`` — `'active'` | `'rolled_back'` | `'superseded'` (last reserved).
- ``rolled_back_at`` / ``rolled_back_by`` / ``rolled_back_reason`` — populated
  by the rollback engine (`tinyassets/rollback.py`) when status flips to
  `'rolled_back'`. Versions stay in the table; only the status flips
  (immutable invariant preserved per design §2.3).
- ``watch_window_seconds`` — per-version eligibility window for being
  flagged by canary RED → `caused_regression` event. Defaults to 24h;
  publish-time override via the same-name arg or
  ``branch_dict["_publish_metadata"]["watch_window_seconds"]``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinyassets.sqlite_connection import ClosingConnection

# ── Watch-window defaults (Task #22 Phase A) ─────────────────────────────────

DEFAULT_WATCH_WINDOW_SECONDS = 86400  # 24h

# ── DDL ──────────────────────────────────────────────────────────────────────
#
# Schema is split into two parts:
#   1. CREATE TABLE for fresh DBs.
#   2. ALTER TABLE migration applied idempotently by
#      `initialize_branch_versions_db` so pre-Task-#22 DBs grow the new
#      columns without losing data. Existing rows backfill to
#      `status='active'` + 24h watch window per design §2.3 (defaults match
#      "no rollback ever applied").

BRANCH_VERSIONS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS branch_versions (
        branch_version_id     TEXT PRIMARY KEY,
        branch_def_id         TEXT NOT NULL,
        content_hash          TEXT NOT NULL,
        snapshot_json         TEXT NOT NULL,
        notes                 TEXT NOT NULL DEFAULT '',
        publisher             TEXT NOT NULL,
        published_at          TEXT NOT NULL,
        parent_version_id     TEXT,
        status                TEXT NOT NULL DEFAULT 'active',
        rolled_back_at        TEXT,
        rolled_back_by        TEXT,
        rolled_back_reason    TEXT,
        watch_window_seconds  INTEGER NOT NULL DEFAULT 86400,
        public                INTEGER NOT NULL DEFAULT 0
    );

    CREATE INDEX IF NOT EXISTS idx_bv_branch_def
        ON branch_versions(branch_def_id, published_at);
    CREATE INDEX IF NOT EXISTS idx_bv_hash
        ON branch_versions(content_hash);
    CREATE INDEX IF NOT EXISTS idx_bv_status
        ON branch_versions(status);
    CREATE INDEX IF NOT EXISTS idx_bv_published_at
        ON branch_versions(published_at);
"""

# Columns added by Task #22 Phase A. Order matters — we ALTER TABLE for
# any missing column on existing DBs. Each tuple is (column_name, ddl).
_ROLLBACK_COLUMNS: tuple[tuple[str, str], ...] = (
    ("status", "TEXT NOT NULL DEFAULT 'active'"),
    ("rolled_back_at", "TEXT"),
    ("rolled_back_by", "TEXT"),
    ("rolled_back_reason", "TEXT"),
    ("watch_window_seconds", "INTEGER NOT NULL DEFAULT 86400"),
)


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class BranchVersion:
    branch_version_id: str
    branch_def_id: str
    content_hash: str
    snapshot: dict[str, Any]
    notes: str
    publisher: str
    published_at: str
    parent_version_id: str | None = None
    # Surgical-rollback fields (Task #22 Phase A). Defaults match
    # "no rollback ever applied" so existing callers + pre-migration
    # rows behave identically to pre-Task-#22.
    status: str = "active"
    rolled_back_at: str | None = None
    rolled_back_by: str | None = None
    rolled_back_reason: str | None = None
    watch_window_seconds: int = DEFAULT_WATCH_WINDOW_SECONDS
    #: The publication mark: its owner published THIS version. Anyone but the
    #: author reads a version only when this is set AND its branch is readable.
    public: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch_version_id": self.branch_version_id,
            "branch_def_id": self.branch_def_id,
            "content_hash": self.content_hash,
            "snapshot": self.snapshot,
            "notes": self.notes,
            "publisher": self.publisher,
            "published_at": self.published_at,
            "parent_version_id": self.parent_version_id,
            "status": self.status,
            "rolled_back_at": self.rolled_back_at,
            "rolled_back_by": self.rolled_back_by,
            "rolled_back_reason": self.rolled_back_reason,
            "watch_window_seconds": self.watch_window_seconds,
            "public": self.public,
        }


# ── Storage helpers ───────────────────────────────────────────────────────────

def _connect(base_path: str | Path) -> sqlite3.Connection:
    from tinyassets.runs import runs_db_path
    path = runs_db_path(base_path)
    conn = sqlite3.connect(str(path), timeout=30.0, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def initialize_branch_versions_db(base_path: str | Path) -> None:
    """Create branch_versions table + apply Task #22 Phase A column adds.

    Idempotent. Pre-Task-#22 DBs grow the new rollback columns via ALTER
    TABLE; existing rows backfill via the column defaults
    (`status='active'`, `watch_window_seconds=86400`, NULLs for the
    rolled_back_* trio). Post-migration the column set is identical
    whether the DB was fresh-created or migrated.

    Sequence matters: CREATE TABLE IF NOT EXISTS first (no-op for
    pre-existing tables), then ALTER TABLE for missing columns, then
    CREATE INDEX IF NOT EXISTS — the new indexes reference columns the
    ALTER step adds, so they MUST run after migration.
    """
    from tinyassets.runs import runs_db_path
    runs_db_path(base_path)  # ensure parent runs DB exists
    with _connect(base_path) as conn:
        # Step 1: ensure the table exists. For fresh DBs this creates the
        # full new column set; for pre-Task-#22 DBs it's a no-op (table
        # already exists with old columns).
        conn.execute("""
            CREATE TABLE IF NOT EXISTS branch_versions (
                branch_version_id     TEXT PRIMARY KEY,
                branch_def_id         TEXT NOT NULL,
                content_hash          TEXT NOT NULL,
                snapshot_json         TEXT NOT NULL,
                notes                 TEXT NOT NULL DEFAULT '',
                publisher             TEXT NOT NULL,
                published_at          TEXT NOT NULL,
                parent_version_id     TEXT,
                status                TEXT NOT NULL DEFAULT 'active',
                rolled_back_at        TEXT,
                rolled_back_by        TEXT,
                rolled_back_reason    TEXT,
                watch_window_seconds  INTEGER NOT NULL DEFAULT 86400,
                public                INTEGER NOT NULL DEFAULT 0
            )
        """)
        # Step 2: ALTER TABLE for any pre-Task-#22 DBs missing the new
        # columns. Each ALTER is O(1) in SQLite.
        existing_cols = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(branch_versions)"
            ).fetchall()
        }
        for col_name, col_ddl in _ROLLBACK_COLUMNS:
            if col_name not in existing_cols:
                conn.execute(
                    f"ALTER TABLE branch_versions ADD COLUMN {col_name} {col_ddl}"
                )
        if "public" not in existing_cols:
            # The publication mark (in-platform-agent-systems, founder
            # 2026-09-30): a version is readable by anyone but its author
            # only when its owner published THAT version. Existing rows start
            # unmarked; _backfill_publication_marks below marks exactly the
            # ones the previous read rule already exposed, once.
            conn.execute(
                "ALTER TABLE branch_versions ADD COLUMN public INTEGER NOT NULL DEFAULT 0"
            )
        # Step 3: indexes — including the new idx_bv_status / idx_bv_published_at
        # which reference columns the ALTER step just added on migrated DBs.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bv_branch_def "
            "ON branch_versions(branch_def_id, published_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bv_hash "
            "ON branch_versions(content_hash)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bv_status "
            "ON branch_versions(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bv_published_at "
            "ON branch_versions(published_at)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS branch_versions_migrations ("
            " name TEXT PRIMARY KEY, applied_at TEXT NOT NULL, detail TEXT NOT NULL)"
        )
        conn.commit()
        _backfill_publication_marks(conn, base_path)


#: One-time data migration marker (a row in ``branch_versions_migrations``).
PUBLICATION_MARK_BACKFILL = "2026-09-30-publication-mark-backfill"


def _public_branch_ids(base_path: str | Path) -> set[str] | None:
    """Branches the PREVIOUS read rule exposed to every caller, or None when the
    branch-definition store cannot be read yet.

    That rule (``_resolve_readable_branch`` before the publication mark) let a
    non-author read a version exactly when its branch row exists and
    ``(visibility or "private") == "public"``: a missing row, a NULL or empty
    visibility, and every other value were unreadable. Opened read-only, so
    the runs-side migration never creates the branch store.
    """
    from tinyassets.storage import db_path

    path = db_path(base_path)
    if not path.exists():
        return None
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30.0)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "branch_definitions" not in tables:
            return None
        columns = {r[1] for r in conn.execute("PRAGMA table_info(branch_definitions)")}
        if "visibility" not in columns:
            return set()
        return {str(r[0]) for r in conn.execute(
            "SELECT branch_def_id FROM branch_definitions WHERE visibility = 'public'")}
    finally:
        conn.close()


def _backfill_publication_marks(conn: sqlite3.Connection, base_path: str | Path) -> None:
    """Mark, once, every version the previous read rule already made public.

    Before the publication mark, anyone could read any version of a branch
    whose CURRENT visibility is public (its history included), and no
    version of any other branch. Exactly those versions get the mark, so a
    public shape that was readable stays readable; nothing the old rule hid
    becomes visible. After the marker row lands, the mark is the only rule
    and this never runs again -- versions minted later are marked only by an
    explicit publish.
    """
    if conn.execute(
        "SELECT 1 FROM branch_versions_migrations WHERE name = ?",
        (PUBLICATION_MARK_BACKFILL,),
    ).fetchone():
        return
    public_ids = _public_branch_ids(base_path)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute(
            "SELECT 1 FROM branch_versions_migrations WHERE name = ?",
            (PUBLICATION_MARK_BACKFILL,),
        ).fetchone():
            conn.rollback()
            return
        if public_ids is None:
            if conn.execute("SELECT 1 FROM branch_versions LIMIT 1").fetchone():
                # Versions exist but their branches cannot be read: deciding
                # now would guess. Leave the marker unset; the next open retries.
                conn.rollback()
                return
            public_ids = set()
        marked = 0
        for branch_def_id in sorted(public_ids):
            marked += conn.execute(
                "UPDATE branch_versions SET public = 1 "
                "WHERE branch_def_id = ? AND public = 0",
                (branch_def_id,),
            ).rowcount
        conn.execute(
            "INSERT INTO branch_versions_migrations (name, applied_at, detail) "
            "VALUES (?, ?, ?)",
            (PUBLICATION_MARK_BACKFILL, datetime.now(timezone.utc).isoformat(),
             json.dumps({"public_branches": len(public_ids), "versions_marked": marked})),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _canonical_snapshot(branch_dict: dict[str, Any]) -> dict[str, Any]:
    """Extract immutable behavior and access-authority fields."""
    from tinyassets.branches import BranchDefinition

    normalized = BranchDefinition.from_dict(branch_dict).to_dict()
    return {
        "branch_def_id": normalized.get("branch_def_id", ""),
        "author": normalized.get("author", ""),
        "visibility": normalized.get("visibility", "public"),
        "skills": normalized.get("skills", []),
        "entry_point": normalized.get("entry_point", ""),
        "graph_nodes": normalized.get("graph_nodes", []),
        "edges": normalized.get("edges", []),
        "conditional_edges": normalized.get("conditional_edges", []),
        "node_defs": normalized.get("node_defs", []),
        "state_schema": normalized.get("state_schema", []),
        **({"io_manifest": normalized["io_manifest"]} if "io_manifest" in normalized else {}),
        # Execution choices the owner authored on the branch. Conditional on
        # ``is not None`` so a branch that sets neither keeps the exact
        # absent-key snapshot form -- and therefore the exact content_hash and
        # branch_version_id -- for a branch whose choices are unset. Existing
        # rows are never rewritten; publishing a newly preserved choice must
        # produce a distinct version instead of silently returning the old one.
        **(
            {"default_llm_policy": normalized["default_llm_policy"]}
            if normalized.get("default_llm_policy") is not None
            else {}
        ),
        **(
            {"concurrency_budget": normalized["concurrency_budget"]}
            if normalized.get("concurrency_budget") is not None
            else {}
        ),
    }


def compute_content_hash(snapshot: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON serialization of the snapshot."""
    canonical = json.dumps(snapshot, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def publish_branch_version(
    base_path: str | Path,
    branch_dict: dict[str, Any],
    *,
    publisher: str = "",
    notes: str = "",
    parent_version_id: str | None = None,
    watch_window_seconds: int | None = None,
    public: bool = False,
) -> BranchVersion:
    """Mint an immutable snapshot of branch_dict.

    ``public`` marks THIS version as published by its owner -- the only way a
    version becomes readable to anyone but its author (see
    :func:`mark_versions_public`). An identical snapshot that already exists is
    returned, and marked when ``public`` is set.

    Returns the BranchVersion. If an identical content_hash already exists
    for this branch_def_id, returns the existing record (deterministic).

    ``watch_window_seconds`` controls the post-publish window during which
    a canary RED can attribute a `caused_regression` event to this version
    (see surgical-rollback design §3). Resolution order:

    1. Explicit ``watch_window_seconds`` arg (highest precedence).
    2. ``branch_dict["_publish_metadata"]["watch_window_seconds"]``
       (frontmatter override; per-branch operator setting).
    3. ``DEFAULT_WATCH_WINDOW_SECONDS`` (24h).
    """
    initialize_branch_versions_db(base_path)

    branch_def_id = branch_dict.get("branch_def_id", "")
    if not branch_def_id:
        raise ValueError("branch_dict must contain 'branch_def_id'.")

    snapshot = _canonical_snapshot(branch_dict)
    content_hash = compute_content_hash(snapshot)
    resolved_watch_window = _resolve_watch_window(branch_dict, watch_window_seconds)

    # A published version is charged to its PUBLISHER's account storage
    # (account-storage-quota D7). Refused at the quota before anything is
    # written; a re-publish of identical content is an over-count the next
    # measurement clears.
    from tinyassets import storage_accounting

    storage_accounting.charge_now(
        base_path,
        account_id=storage_accounting.account_for_actor(base_path, publisher),
        store="branches",
        nbytes=len(str(snapshot).encode("utf-8")) + len(str(notes or "").encode("utf-8")),
    )

    with _connect(base_path) as conn:
        # Deterministic: same content_hash for same branch_def_id returns existing.
        existing = conn.execute(
            "SELECT * FROM branch_versions "
            "WHERE branch_def_id = ? AND content_hash = ?",
            (branch_def_id, content_hash),
        ).fetchone()
        if existing is not None:
            if public and not existing["public"]:
                conn.execute(
                    "UPDATE branch_versions SET public = 1 WHERE branch_version_id = ?",
                    (existing["branch_version_id"],),
                )
                existing = conn.execute(
                    "SELECT * FROM branch_versions WHERE branch_version_id = ?",
                    (existing["branch_version_id"],),
                ).fetchone()
            return _row_to_version(existing)

        branch_version_id = f"{branch_def_id}@{content_hash[:8]}"
        # Handle the (rare) case where hash prefix collides with a different hash.
        collision = conn.execute(
            "SELECT content_hash FROM branch_versions WHERE branch_version_id = ?",
            (branch_version_id,),
        ).fetchone()
        if collision and collision["content_hash"] != content_hash:
            branch_version_id = f"{branch_def_id}@{content_hash[:16]}"

        if parent_version_id:
            _validate_version_exists(conn, parent_version_id)

        published_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT OR IGNORE INTO branch_versions
                (branch_version_id, branch_def_id, content_hash,
                 snapshot_json, notes, publisher, published_at, parent_version_id,
                 status, watch_window_seconds, public)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                branch_version_id,
                branch_def_id,
                content_hash,
                json.dumps(snapshot, default=str),
                notes,
                publisher,
                published_at,
                parent_version_id,
                resolved_watch_window,
                1 if public else 0,
            ),
        )
        # Re-fetch to get exact stored row (handles INSERT OR IGNORE race).
        row = conn.execute(
            "SELECT * FROM branch_versions WHERE branch_version_id = ?",
            (branch_version_id,),
        ).fetchone()
        return _row_to_version(row)


def _resolve_watch_window(
    branch_dict: dict[str, Any],
    explicit: int | None,
) -> int:
    """Resolve effective watch_window_seconds per the publish-time precedence
    order documented on `publish_branch_version`. Coerces to int; rejects
    non-positive values to avoid an instantly-expired window.
    """
    if explicit is not None:
        candidate: Any = explicit
    else:
        meta = branch_dict.get("_publish_metadata") or {}
        candidate = meta.get("watch_window_seconds", DEFAULT_WATCH_WINDOW_SECONDS)
    try:
        value = int(candidate)
    except (TypeError, ValueError):
        return DEFAULT_WATCH_WINDOW_SECONDS
    return value if value > 0 else DEFAULT_WATCH_WINDOW_SECONDS


def is_within_watch_window(
    version: BranchVersion,
    *,
    now: datetime | None = None,
) -> bool:
    """True iff `now` falls within the version's [published_at,
    published_at + watch_window_seconds] window.

    Convenience helper consumed by Phase C bisect (suspect-set filter:
    "which versions can still emit caused_regression?"). Folded into
    Phase A per lead's bonus ask so Phase C consumes a stable contract.

    Returns False if `published_at` cannot be parsed (defensive — better
    to drop a version from the suspect set than to crash bisect).
    """
    if version.status != "active":
        return False
    try:
        published_at = datetime.fromisoformat(version.published_at)
    except (TypeError, ValueError):
        return False
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    elapsed = (now - published_at).total_seconds()
    return 0 <= elapsed <= version.watch_window_seconds


def get_branch_version(
    base_path: str | Path,
    branch_version_id: str,
) -> BranchVersion | None:
    """Fetch a published version by ID. Returns None if not found."""
    initialize_branch_versions_db(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM branch_versions WHERE branch_version_id = ?",
            (branch_version_id,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_version(row)


def branch_version_def_id(base_path: str | Path, branch_version_id: str) -> str:
    """Metadata-only lookup: the ``branch_def_id`` a version belongs to, WITHOUT
    loading its (potentially large / private) snapshot. Used by the invoke_branch
    authorization gate to authorize-before-snapshot-load. Empty string if absent."""
    initialize_branch_versions_db(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT branch_def_id FROM branch_versions WHERE branch_version_id = ?",
            (branch_version_id,),
        ).fetchone()
    if row is None:
        return ""
    return (row["branch_def_id"] or "").strip()


def branch_version_is_public(base_path: str | Path, branch_version_id: str) -> bool:
    """Read only the publication mark, without loading private snapshot content."""
    initialize_branch_versions_db(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT public FROM branch_versions WHERE branch_version_id = ?",
            (branch_version_id,),
        ).fetchone()
    return bool(row and row["public"])


def list_branch_versions(
    base_path: str | Path,
    branch_def_id: str,
    *,
    limit: int = 50,
) -> list[BranchVersion]:
    """List published versions for a branch, newest first."""
    initialize_branch_versions_db(base_path)
    limit = min(max(1, limit), 500)
    with _connect(base_path) as conn:
        rows = conn.execute(
            "SELECT * FROM branch_versions WHERE branch_def_id = ? "
            "ORDER BY published_at DESC LIMIT ?",
            (branch_def_id, limit),
        ).fetchall()
    return [_row_to_version(r) for r in rows]


def list_version_ids(base_path: str | Path, branch_def_id: str) -> set[str]:
    """EVERY version id of a branch, uncapped. `list_branch_versions` caps at
    500, which is fine for a listing and wrong for a dependency check."""
    initialize_branch_versions_db(base_path)
    conn = _connect(base_path)
    try:
        rows = conn.execute(
            "SELECT branch_version_id FROM branch_versions WHERE branch_def_id = ?",
            (branch_def_id,),
        ).fetchall()
    finally:
        conn.close()
    return {str(r[0]) for r in rows}


def versions_invoking(
    base_path: str | Path,
    *,
    branch_def_id: str,
    version_ids: set[str],
    exclude_branch_def_id: str = "",
    author: str = "",
) -> set[str]:
    """Branch ids whose PUBLISHED SNAPSHOTS invoke `branch_def_id` (by id) or one
    of `version_ids` (by version). A snapshot is executable on its own -- a
    canonical binding or `invoke_branch_version` runs it -- and its invoke node
    reloads the child live, so a child a snapshot names is a dependency even
    when the parent's CURRENT definition no longer names it.

    With `author`, only that author's snapshots count: a FOREIGN snapshot that
    invoked the branch while it was public was already cut off when the branch
    went private (a private child runs only for its author), and the owner
    cannot edit a foreign branch to remediate, so it must not block forever."""
    initialize_branch_versions_db(base_path)
    conn = _connect(base_path)
    try:
        rows = conn.execute(
            "SELECT branch_def_id, snapshot_json FROM branch_versions "
            "WHERE branch_def_id != ? AND status = 'active'",
            (exclude_branch_def_id or branch_def_id,),
        ).fetchall()
    finally:
        conn.close()
    found: set[str] = set()
    for row in rows:
        try:
            snapshot = json.loads(row["snapshot_json"])
        except (TypeError, ValueError):
            continue
        if author and (snapshot.get("author") or "") != author:
            continue
        for node in snapshot.get("node_defs") or []:
            if not isinstance(node, dict):
                continue
            by_id = (node.get("invoke_branch_spec") or {}).get("branch_def_id")
            by_version = (node.get("invoke_branch_version_spec") or {}).get("branch_version_id")
            if by_id == branch_def_id or (by_version and by_version in version_ids):
                found.add(str(row["branch_def_id"]))
                break
    return found


def _validate_version_exists(conn: sqlite3.Connection, version_id: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM branch_versions WHERE branch_version_id = ?",
        (version_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"parent_version_id '{version_id}' not found.")


def branch_readable_by(actor: str | None, *, author: Any, visibility: Any) -> bool:
    """THE branch read rule: public, or the caller wrote it.

    A missing, NULL or blank visibility is PRIVATE (private by default, founder
    2026-09-26): a legacy row without the field is never readable by everyone
    because the field is absent. Its author still reads it.
    """
    if (visibility or "private") == "public":
        return True
    return actor is not None and (author or "") == actor


def version_readable_by(
    actor: str | None, *, author: Any, visibility: Any, public: Any,
) -> bool:
    """THE version read rule: its branch's author reads every version, history
    included; anyone else needs a publicly readable branch AND the version's
    publication mark (founder 2026-09-30)."""
    if actor is not None and (author or "") == actor:
        return True
    return branch_readable_by(None, author=author, visibility=visibility) and bool(public)


def mark_versions_public(
    base_path: str | Path, version_ids: list[str], *, public: bool = True,
) -> None:
    """Set (or clear) the publication mark on exactly these versions."""
    initialize_branch_versions_db(base_path)
    with _connect(base_path) as conn:
        for version_id in version_ids:
            conn.execute(
                "UPDATE branch_versions SET public = ? WHERE branch_version_id = ?",
                (1 if public else 0, version_id),
            )


def _row_to_version(row: sqlite3.Row) -> BranchVersion:
    try:
        snapshot = json.loads(row["snapshot_json"])
    except (json.JSONDecodeError, TypeError):
        snapshot = {}
    # Defensive .keys() check on the rollback fields: lets pre-migration
    # row objects (e.g. from a test that stubs sqlite3.Row) round-trip
    # without KeyError. Production rows always have all fields after
    # `initialize_branch_versions_db` runs.
    row_keys = set(row.keys())
    return BranchVersion(
        branch_version_id=row["branch_version_id"],
        branch_def_id=row["branch_def_id"],
        content_hash=row["content_hash"],
        snapshot=snapshot,
        notes=row["notes"] or "",
        publisher=row["publisher"],
        published_at=row["published_at"],
        parent_version_id=row["parent_version_id"],
        status=row["status"] if "status" in row_keys else "active",
        rolled_back_at=(
            row["rolled_back_at"] if "rolled_back_at" in row_keys else None
        ),
        rolled_back_by=(
            row["rolled_back_by"] if "rolled_back_by" in row_keys else None
        ),
        rolled_back_reason=(
            row["rolled_back_reason"] if "rolled_back_reason" in row_keys else None
        ),
        watch_window_seconds=(
            int(row["watch_window_seconds"])
            if "watch_window_seconds" in row_keys
            and row["watch_window_seconds"] is not None
            else DEFAULT_WATCH_WINDOW_SECONDS
        ),
        public=bool(row["public"]) if "public" in row_keys else False,
    )


__all__ = [
    "BranchVersion",
    "mark_versions_public",
    "BRANCH_VERSIONS_SCHEMA",
    "DEFAULT_WATCH_WINDOW_SECONDS",
    "compute_content_hash",
    "get_branch_version",
    "initialize_branch_versions_db",
    "is_within_watch_window",
    "list_branch_versions",
    "publish_branch_version",
]
