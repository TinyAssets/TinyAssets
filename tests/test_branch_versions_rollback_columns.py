"""Phase A surgical-rollback schema migration tests (Task #22).

Spec: 2026-04-25-surgical-rollback-proposal.md (archived in git) §2.3.

Covers:
- Fresh DB: CREATE TABLE includes the 5 new columns + 2 new indexes.
- Pre-Task-#22 DB: ALTER TABLE adds missing columns idempotently.
- Existing rows backfill correctly (status='active', watch=86400, NULLs).
- BranchVersion dataclass roundtrips the new fields.
- watch_window_seconds resolution: explicit > frontmatter > default.
- is_within_watch_window helper: in-window / past-window /
  rolled-back-status / unparseable-published_at.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tinyassets.branch_versions import (
    DEFAULT_WATCH_WINDOW_SECONDS,
    BranchVersion,
    _resolve_watch_window,
    get_branch_version,
    initialize_branch_versions_db,
    is_within_watch_window,
    publish_branch_version,
)
from tinyassets.runs import runs_db_path


def _seed_branch_dict(branch_def_id="b1"):
    """Minimal-valid branch_dict for publish_branch_version."""
    return {
        "branch_def_id": branch_def_id,
        "entry_point": "n1",
        "graph_nodes": [{"id": "n1", "node_def_id": "n1"}],
        "edges": [{"from_node": "n1", "to_node": "END"}],
        "node_defs": [
            {"node_id": "n1", "display_name": "N1", "prompt_template": "echo"},
        ],
        "state_schema": [],
        "conditional_edges": [],
    }


def _live_columns(base_path: Path) -> set[str]:
    """Read live column set via PRAGMA — independent of the dataclass."""
    db = runs_db_path(base_path)
    with sqlite3.connect(str(db)) as conn:
        rows = conn.execute("PRAGMA table_info(branch_versions)").fetchall()
    return {r[1] for r in rows}


# ─── fresh-DB schema ─────────────────────────────────────────────────────


class TestFreshDBSchema:
    def test_new_columns_present(self, tmp_path):
        initialize_branch_versions_db(tmp_path)
        cols = _live_columns(tmp_path)
        for new_col in (
            "status",
            "rolled_back_at",
            "rolled_back_by",
            "rolled_back_reason",
            "watch_window_seconds",
        ):
            assert new_col in cols, f"missing column on fresh DB: {new_col}"

    def test_new_indexes_present(self, tmp_path):
        initialize_branch_versions_db(tmp_path)
        db = runs_db_path(tmp_path)
        with sqlite3.connect(str(db)) as conn:
            idx = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='branch_versions'"
                ).fetchall()
            }
        assert "idx_bv_status" in idx
        assert "idx_bv_published_at" in idx


# ─── pre-Task-#22 migration ──────────────────────────────────────────────


class TestPreTask22Migration:
    """Simulate a DB created before Phase A landed and confirm the
    initialize() helper grows the new columns idempotently.
    """

    def _seed_pre_task22_db(self, base_path: Path) -> None:
        """Create a branch_versions table with the OLD column set only."""
        # initialize_branch_versions_db calls runs_db_path which creates
        # the parent dir. We reach in to bootstrap the DB then drop the
        # already-created table and recreate it with the old shape.
        initialize_branch_versions_db(base_path)
        db = runs_db_path(base_path)
        with sqlite3.connect(str(db)) as conn:
            conn.execute("DROP TABLE branch_versions")
            conn.execute(
                """
                CREATE TABLE branch_versions (
                    branch_version_id   TEXT PRIMARY KEY,
                    branch_def_id       TEXT NOT NULL,
                    content_hash        TEXT NOT NULL,
                    snapshot_json       TEXT NOT NULL,
                    notes               TEXT NOT NULL DEFAULT '',
                    publisher           TEXT NOT NULL,
                    published_at        TEXT NOT NULL,
                    parent_version_id   TEXT
                )
                """
            )
            # Seed an existing row so backfill semantics are tested.
            conn.execute(
                """
                INSERT INTO branch_versions
                    (branch_version_id, branch_def_id, content_hash,
                     snapshot_json, notes, publisher, published_at)
                VALUES ('legacy@abc12345', 'legacy', 'abc12345...',
                        '{}', '', 'legacy-actor', '2026-01-01T00:00:00+00:00')
                """
            )

    def test_migration_adds_columns(self, tmp_path):
        self._seed_pre_task22_db(tmp_path)
        # Confirm pre-state really lacks the new columns.
        cols_before = _live_columns(tmp_path)
        assert "status" not in cols_before
        # Run the migration.
        initialize_branch_versions_db(tmp_path)
        cols_after = _live_columns(tmp_path)
        assert {"status", "rolled_back_at", "rolled_back_by",
                "rolled_back_reason", "watch_window_seconds"} <= cols_after

    def test_migration_is_idempotent(self, tmp_path):
        self._seed_pre_task22_db(tmp_path)
        # Run migration twice — second call must NOT raise (no duplicate
        # column ALTER) and the column set should remain stable.
        initialize_branch_versions_db(tmp_path)
        initialize_branch_versions_db(tmp_path)
        cols = _live_columns(tmp_path)
        assert "status" in cols

    def test_existing_row_backfills_to_active_and_default_window(self, tmp_path):
        self._seed_pre_task22_db(tmp_path)
        initialize_branch_versions_db(tmp_path)
        version = get_branch_version(tmp_path, "legacy@abc12345")
        assert version is not None
        assert version.status == "active"
        assert version.watch_window_seconds == DEFAULT_WATCH_WINDOW_SECONDS
        assert version.rolled_back_at is None
        assert version.rolled_back_by is None
        assert version.rolled_back_reason is None


# ─── BranchVersion dataclass roundtrip ───────────────────────────────────


class TestDataclassRoundtrip:
    def test_to_dict_includes_new_fields(self):
        v = BranchVersion(
            branch_version_id="b@123",
            branch_def_id="b",
            content_hash="123",
            snapshot={},
            notes="",
            publisher="alice",
            published_at="2026-01-01T00:00:00+00:00",
            status="rolled_back",
            rolled_back_at="2026-01-02T00:00:00+00:00",
            rolled_back_by="alice",
            rolled_back_reason="canary regression",
            watch_window_seconds=3600,
        )
        d = v.to_dict()
        assert d["status"] == "rolled_back"
        assert d["rolled_back_at"] == "2026-01-02T00:00:00+00:00"
        assert d["rolled_back_by"] == "alice"
        assert d["rolled_back_reason"] == "canary regression"
        assert d["watch_window_seconds"] == 3600

    def test_default_field_values(self):
        v = BranchVersion(
            branch_version_id="b@123",
            branch_def_id="b",
            content_hash="123",
            snapshot={},
            notes="",
            publisher="alice",
            published_at="2026-01-01T00:00:00+00:00",
        )
        assert v.status == "active"
        assert v.rolled_back_at is None
        assert v.watch_window_seconds == DEFAULT_WATCH_WINDOW_SECONDS


# ─── publish-time watch_window precedence ────────────────────────────────


class TestPublishWatchWindow:
    def test_default_watch_window_is_24h(self, tmp_path):
        v = publish_branch_version(tmp_path, _seed_branch_dict("default-branch"))
        assert v.watch_window_seconds == DEFAULT_WATCH_WINDOW_SECONDS
        assert v.status == "active"

    def test_explicit_arg_wins_over_frontmatter(self, tmp_path):
        branch = _seed_branch_dict("override-branch")
        branch["_publish_metadata"] = {"watch_window_seconds": 3600}
        v = publish_branch_version(
            tmp_path, branch, watch_window_seconds=604800,
        )
        assert v.watch_window_seconds == 604800

    def test_frontmatter_wins_over_default(self, tmp_path):
        branch = _seed_branch_dict("frontmatter-branch")
        branch["_publish_metadata"] = {"watch_window_seconds": 7200}
        v = publish_branch_version(tmp_path, branch)
        assert v.watch_window_seconds == 7200

    def test_invalid_watch_window_falls_back_to_default(self, tmp_path):
        branch = _seed_branch_dict("invalid-branch")
        branch["_publish_metadata"] = {"watch_window_seconds": "not-a-number"}
        v = publish_branch_version(tmp_path, branch)
        assert v.watch_window_seconds == DEFAULT_WATCH_WINDOW_SECONDS

    def test_zero_watch_window_falls_back_to_default(self, tmp_path):
        # Zero would mean "instantly expired" — treat as misconfiguration.
        branch = _seed_branch_dict("zero-branch")
        branch["_publish_metadata"] = {"watch_window_seconds": 0}
        v = publish_branch_version(tmp_path, branch)
        assert v.watch_window_seconds == DEFAULT_WATCH_WINDOW_SECONDS


# ─── _resolve_watch_window unit ──────────────────────────────────────────


class TestResolveWatchWindow:
    @pytest.mark.parametrize(
        "branch_dict,explicit,expected",
        [
            ({}, None, DEFAULT_WATCH_WINDOW_SECONDS),
            ({"_publish_metadata": {"watch_window_seconds": 3600}}, None, 3600),
            ({"_publish_metadata": {"watch_window_seconds": 3600}}, 7200, 7200),
            ({"_publish_metadata": None}, None, DEFAULT_WATCH_WINDOW_SECONDS),
            ({"_publish_metadata": {}}, None, DEFAULT_WATCH_WINDOW_SECONDS),
        ],
    )
    def test_resolution(self, branch_dict, explicit, expected):
        assert _resolve_watch_window(branch_dict, explicit) == expected


# ─── is_within_watch_window helper ───────────────────────────────────────


class TestIsWithinWatchWindow:
    def _version(
        self,
        *,
        published_at: datetime,
        watch_window_seconds: int = 3600,
        status: str = "active",
    ) -> BranchVersion:
        return BranchVersion(
            branch_version_id="b@xx",
            branch_def_id="b",
            content_hash="xx",
            snapshot={},
            notes="",
            publisher="alice",
            published_at=published_at.isoformat(),
            watch_window_seconds=watch_window_seconds,
            status=status,
        )

    def test_just_published_is_within(self):
        now = datetime.now(timezone.utc)
        v = self._version(published_at=now - timedelta(minutes=1))
        assert is_within_watch_window(v, now=now) is True

    def test_past_window_returns_false(self):
        now = datetime.now(timezone.utc)
        v = self._version(
            published_at=now - timedelta(hours=2),
            watch_window_seconds=3600,
        )
        assert is_within_watch_window(v, now=now) is False

    def test_rolled_back_status_returns_false(self):
        now = datetime.now(timezone.utc)
        v = self._version(
            published_at=now - timedelta(minutes=1),
            status="rolled_back",
        )
        assert is_within_watch_window(v, now=now) is False

    def test_unparseable_published_at_returns_false(self):
        v = BranchVersion(
            branch_version_id="b@xx",
            branch_def_id="b",
            content_hash="xx",
            snapshot={},
            notes="",
            publisher="alice",
            published_at="garbage",
            watch_window_seconds=3600,
        )
        assert is_within_watch_window(v) is False

    def test_naive_datetime_treated_as_utc(self):
        # If the caller passes a naive datetime, the helper attaches UTC
        # rather than crashing with a tz-comparison TypeError.
        now_aware = datetime.now(timezone.utc)
        v = self._version(published_at=now_aware - timedelta(minutes=1))
        now_naive = now_aware.replace(tzinfo=None)
        assert is_within_watch_window(v, now=now_naive) is True
