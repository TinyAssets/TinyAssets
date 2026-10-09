"""Tests for attribution chain MCP actions in extensions().

Covers: record_remix, get_provenance.
"""

from __future__ import annotations

import json

import pytest

from tinyassets.runs import initialize_runs_db


@pytest.fixture(autouse=True)
def _set_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    initialize_runs_db(tmp_path)


# ── record_remix ───────────────────────────────────────────────────────────────

class TestRecordRemix:
    def test_record_credit_persists_identity_tuple(self, tmp_path):
        from tinyassets.api.market import _action_record_remix

        result = json.loads(_action_record_remix({
            "parent_branch_def_id": "branch-A",
            "child_branch_def_id": "branch-B",
            "actor_id": "actor-daemon",
            "owner_user_id": "owner-user",
            "daemon_id": "daemon::actor",
            "runtime_instance_id": "runtime-1",
            "worker_id": "worker-1",
            "credit_share": 0.3,
        }))
        assert result["status"] == "recorded"

        from tinyassets.runs import _connect

        with _connect(tmp_path) as conn:
            row = conn.execute(
                """
                SELECT actor_id, owner_user_id, daemon_id,
                       runtime_instance_id, worker_id, credit_share
                FROM attribution_credit
                WHERE artifact_id = ? AND artifact_kind = 'branch'
                """,
                ("branch-B",),
            ).fetchone()
        assert row["actor_id"] == "actor-daemon"
        assert row["owner_user_id"] == "owner-user"
        assert row["daemon_id"] == "daemon::actor"
        assert row["runtime_instance_id"] == "runtime-1"
        assert row["worker_id"] == "worker-1"
        assert row["credit_share"] == pytest.approx(0.3)


# ── get_provenance ─────────────────────────────────────────────────────────────


# ── Cross-universe isolation ───────────────────────────────────────────────────


# ── available_actions listing ──────────────────────────────────────────────────
