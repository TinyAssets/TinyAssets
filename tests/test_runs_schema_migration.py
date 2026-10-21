"""Task #16/#27 — Runs table schema migration + write-site audit.

Guards:
- New columns exist in schema after initialize_runs_db().
- update_run_status() writes provider_used when supplied.
- get_run() returns provider_used / model / token_count fields.
- Migration: pre-existing DB without new columns is upgraded on next init call.
- list_recent_runs() includes provider_used in returned records.
- provider_used=None is preserved (no provider written for mock/no-LLM runs).

Task #27 write-site audit (all update_run_status call-sites):
- _invoke_graph() success path: provider_tracker["last"] → provider_used. POPULATED.
- _invoke_graph() failure paths (compile error, pre-cancel, graph exception): NULL.
  Correct — no nodes ran or exception paths have no provider info.
- _invoke_graph_resume() success path: now has provider_tracker. POPULATED (fix #27).
- _invoke_graph_resume() failure paths: NULL. Correct.
- Background worker crash (execute_branch_async): NULL. Correct — exception path.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

from tinyassets.runs import (
    RUN_STATUS_COMPLETED,
    _connect,
    create_run,
    get_run,
    initialize_runs_db,
    list_recent_runs,
    update_run_status,
)


def _insert_run_raw(base: Path, *, branch_def_id: str = "b1") -> str:
    """Insert a run row using the raw schema (no new columns) — simulates old DB."""
    run_id = uuid.uuid4().hex[:16]
    started_at = time.time()
    with _connect(base) as conn:
        conn.execute(
            """
            INSERT INTO runs
                (run_id, branch_def_id, run_name, thread_id, status,
                 actor, inputs_json, output_json, error, last_node_id, started_at)
            VALUES (?, ?, '', ?, 'queued', 'anon', '{}', '{}', '', '', ?)
            """,
            (run_id, branch_def_id, run_id, started_at),
        )
    return run_id


class TestNewColumnsExist:
    def test_provider_used_column_present(self, tmp_path: Path) -> None:
        initialize_runs_db(tmp_path)
        with _connect(tmp_path) as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        assert "provider_used" in cols

    def test_model_column_present(self, tmp_path: Path) -> None:
        initialize_runs_db(tmp_path)
        with _connect(tmp_path) as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        assert "model" in cols

    def test_token_count_column_present(self, tmp_path: Path) -> None:
        initialize_runs_db(tmp_path)
        with _connect(tmp_path) as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        assert "token_count" in cols

    def test_branch_version_id_column_present(self, tmp_path: Path) -> None:
        """Task #65a — runs.branch_version_id added for Phase A item 6."""
        initialize_runs_db(tmp_path)
        with _connect(tmp_path) as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        assert "branch_version_id" in cols

    def test_branch_version_id_index_present(self, tmp_path: Path) -> None:
        """Task #65a — index on runs.branch_version_id for attribution queries."""
        initialize_runs_db(tmp_path)
        with _connect(tmp_path) as conn:
            idx_names = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                )
            }
        assert "idx_runs_branch_version" in idx_names

    def test_def_based_run_leaves_branch_version_id_null(self, tmp_path: Path) -> None:
        """Task #65a invariant: create_run without branch_version_id leaves it NULL."""
        initialize_runs_db(tmp_path)
        run_id = create_run(
            tmp_path,
            branch_def_id="b1",
            thread_id="t1",
            inputs={},
            run_name="legacy",
            actor="anon",
        )
        with _connect(tmp_path) as conn:
            row = conn.execute(
                "SELECT branch_version_id FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        assert row["branch_version_id"] is None

    def test_create_run_with_branch_version_id_persists(self, tmp_path: Path) -> None:
        """Task #65a: explicit branch_version_id on create_run is stored."""
        initialize_runs_db(tmp_path)
        run_id = create_run(
            tmp_path,
            branch_def_id="b1",
            thread_id="t1",
            inputs={},
            run_name="versioned",
            actor="anon",
            branch_version_id="b1@abc12345",
        )
        with _connect(tmp_path) as conn:
            row = conn.execute(
                "SELECT branch_version_id FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        assert row["branch_version_id"] == "b1@abc12345"

    def test_create_run_resolves_owner_user_id_from_daemon_registry(
        self, tmp_path: Path,
    ) -> None:
        from tinyassets.daemon_registry import create_daemon

        daemon = create_daemon(
            tmp_path,
            display_name="Owner Resolved Runner",
            created_by="owner-user",
            soul_mode="soulless",
        )
        run_id = create_run(
            tmp_path,
            branch_def_id="b1",
            thread_id="t1",
            inputs={},
            run_name="identity",
            actor="actor-user",
            daemon_id=daemon["daemon_id"],
            runtime_instance_id="runtime-1",
            worker_id="worker-1",
        )
        with _connect(tmp_path) as conn:
            row = conn.execute(
                """
                SELECT owner_user_id, daemon_id, runtime_instance_id, worker_id
                FROM runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        assert row["owner_user_id"] == "owner-user"
        assert row["daemon_id"] == daemon["daemon_id"]
        assert row["runtime_instance_id"] == "runtime-1"
        assert row["worker_id"] == "worker-1"


class TestMigrationExistingDb:
    def _old_schema_db(self, base: Path) -> None:
        """Create a DB with the old runs schema (no new columns)."""
        with sqlite3.connect(str(base / "runs.db")) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id         TEXT PRIMARY KEY,
                    branch_def_id  TEXT NOT NULL,
                    run_name       TEXT NOT NULL DEFAULT '',
                    thread_id      TEXT NOT NULL,
                    status         TEXT NOT NULL DEFAULT 'queued',
                    actor          TEXT NOT NULL DEFAULT 'anonymous',
                    inputs_json    TEXT NOT NULL DEFAULT '{}',
                    output_json    TEXT NOT NULL DEFAULT '{}',
                    error          TEXT NOT NULL DEFAULT '',
                    last_node_id   TEXT NOT NULL DEFAULT '',
                    started_at     REAL NOT NULL,
                    finished_at    REAL
                )
            """)

    def test_migration_adds_columns_to_old_db(self, tmp_path: Path) -> None:
        self._old_schema_db(tmp_path)
        # Verify columns are absent before migration
        with sqlite3.connect(str(tmp_path / "runs.db")) as conn:
            conn.row_factory = sqlite3.Row
            cols_before = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        assert "provider_used" not in cols_before

        initialize_runs_db(tmp_path)

        with _connect(tmp_path) as conn:
            cols_after = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        assert "provider_used" in cols_after
        assert "model" in cols_after
        assert "token_count" in cols_after

    def test_migration_idempotent(self, tmp_path: Path) -> None:
        initialize_runs_db(tmp_path)
        initialize_runs_db(tmp_path)  # second call must not raise
        with _connect(tmp_path) as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        assert "provider_used" in cols


class TestUpdateRunStatusProviderFields:
    def test_write_provider_used(self, tmp_path: Path) -> None:
        initialize_runs_db(tmp_path)
        run_id = create_run(
            tmp_path,
            branch_def_id="b1",
            thread_id="t1",
            run_name="r",
            inputs={},
            actor="universe:u-test",
        )
        update_run_status(
            tmp_path, run_id,
            status=RUN_STATUS_COMPLETED,
            finished_at=time.time(),
            provider_used="claude",
        )
        row = get_run(tmp_path, run_id)
        assert row is not None
        assert row["provider_used"] == "claude"

    def test_write_model(self, tmp_path: Path) -> None:
        initialize_runs_db(tmp_path)
        run_id = create_run(
            tmp_path,
            branch_def_id="b1",
            thread_id="t1",
            run_name="r",
            inputs={},
            actor="universe:u-test",
        )
        update_run_status(
            tmp_path, run_id,
            status=RUN_STATUS_COMPLETED,
            finished_at=time.time(),
            model="claude-opus-4-7",
        )
        row = get_run(tmp_path, run_id)
        assert row is not None
        assert row["model"] == "claude-opus-4-7"

    def test_write_token_count(self, tmp_path: Path) -> None:
        initialize_runs_db(tmp_path)
        run_id = create_run(
            tmp_path,
            branch_def_id="b1",
            thread_id="t1",
            run_name="r",
            inputs={},
            actor="universe:u-test",
        )
        update_run_status(
            tmp_path, run_id,
            status=RUN_STATUS_COMPLETED,
            finished_at=time.time(),
            token_count=4200,
        )
        row = get_run(tmp_path, run_id)
        assert row is not None
        assert row["token_count"] == 4200

    def test_provider_used_none_by_default(self, tmp_path: Path) -> None:
        initialize_runs_db(tmp_path)
        run_id = create_run(
            tmp_path,
            branch_def_id="b1",
            thread_id="t1",
            run_name="r",
            inputs={},
            actor="universe:u-test",
        )
        update_run_status(tmp_path, run_id, status=RUN_STATUS_COMPLETED, finished_at=time.time())
        row = get_run(tmp_path, run_id)
        assert row is not None
        assert row["provider_used"] is None


class TestListRecentRunsIncludesProviderFields:
    def test_list_recent_runs_has_provider_used_key(self, tmp_path: Path) -> None:
        initialize_runs_db(tmp_path)
        run_id = create_run(
            tmp_path,
            branch_def_id="b1",
            thread_id="t1",
            run_name="r",
            inputs={},
            actor="universe:u-test",
        )
        update_run_status(
            tmp_path, run_id,
            status=RUN_STATUS_COMPLETED,
            finished_at=time.time(),
            provider_used="codex",
        )
        runs = list_recent_runs(tmp_path, limit=1)
        assert len(runs) == 1
        assert "provider_used" in runs[0]
        assert runs[0]["provider_used"] == "codex"

    def test_list_recent_runs_provider_used_none_when_absent(self, tmp_path: Path) -> None:
        initialize_runs_db(tmp_path)
        run_id = create_run(
            tmp_path,
            branch_def_id="b1",
            thread_id="t1",
            run_name="r",
            inputs={},
            actor="universe:u-test",
        )
        update_run_status(tmp_path, run_id, status=RUN_STATUS_COMPLETED, finished_at=time.time())
        runs = list_recent_runs(tmp_path, limit=1)
        assert len(runs) == 1
        assert runs[0]["provider_used"] is None


# ---------------------------------------------------------------------------
# Task #27: resume path now populates provider_used
# ---------------------------------------------------------------------------


class TestResumePathProviderUsed:
    """The _invoke_graph_resume success path now writes provider_used.

    We can't invoke the full resume path without LangGraph, so we test:
    1. update_run_status accepts provider_used in the resume-completion shape.
    2. get_run returns that value correctly (DB roundtrip).
    3. NULL is still valid for resume paths that see no provider events.
    """


    def test_resume_completion_null_provider_when_no_nodes_ran(self, tmp_path: Path) -> None:
        initialize_runs_db(tmp_path)
        run_id = create_run(
            tmp_path,
            branch_def_id="b1",
            thread_id="t1",
            run_name="r",
            inputs={},
            actor="universe:u-test",
        )
        update_run_status(
            tmp_path, run_id,
            status=RUN_STATUS_COMPLETED,
            finished_at=time.time(),
            provider_used=None,
        )
        row = get_run(tmp_path, run_id)
        assert row is not None
        assert row["provider_used"] is None

    def test_list_recent_runs_reflects_resume_provider(self, tmp_path: Path) -> None:
        initialize_runs_db(tmp_path)
        run_id = create_run(
            tmp_path,
            branch_def_id="b1",
            thread_id="t1",
            run_name="r",
            inputs={},
            actor="universe:u-test",
        )
        update_run_status(
            tmp_path, run_id,
            status=RUN_STATUS_COMPLETED,
            finished_at=time.time(),
            provider_used="codex",
        )
        runs = list_recent_runs(tmp_path, limit=1)
        assert runs[0]["provider_used"] == "codex"
