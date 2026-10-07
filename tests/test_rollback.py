"""Phase B surgical-rollback engine tests (Task #22).

Spec: docs/design-notes/2026-04-25-surgical-rollback-proposal.md.
Implementation: tinyassets/rollback.py.

Covers:
- compute_rollback_set: closure walk over parent_version_id + fork_from.
- execute_rollback_set: atomic status flip + caused_regression event emit
  (single runs-DB transaction); abort-all on validation failure;
  host-only authority enforced at the MCP-action layer (separate test).
- get_rollback_history: read-only history query.
- MCP action surface (rollback_merge + get_rollback_history): wired
  end-to-end via `goals → publish_branch_version → set_canonical →
  rollback_merge` integration.

Re-point cascade tests live in tests/test_rollback_repoint.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tinyassets.branch_versions import (
    _connect as _branch_versions_connect,
)
from tinyassets.branch_versions import (
    get_branch_version,
    publish_branch_version,
)
from tinyassets.contribution_events import initialize_contribution_events_db
from tinyassets.rollback import (
    AUTO_ROLLBACK_WEIGHT_THRESHOLD,
    ROLLBACK_WEIGHTS,
    auto_rollback_on_canary_red,
    bisect_canary,
    compute_rollback_set,
    execute_rollback_set,
    get_rollback_history,
    list_watch_window_suspects,
    rollback_merge_orchestrator,
)

# ─── helpers ──────────────────────────────────────────────────────────────


def _seed_branch_dict(branch_def_id="b1", entry="n1"):
    return {
        "branch_def_id": branch_def_id,
        "entry_point": entry,
        "graph_nodes": [{"id": entry, "node_def_id": entry}],
        "edges": [{"from_node": entry, "to_node": "END"}],
        "node_defs": [
            {"node_id": entry, "display_name": entry, "prompt_template": "echo"},
        ],
        "state_schema": [],
        "conditional_edges": [],
    }


def _publish(tmp_path, branch_def_id, parent_version_id=None):
    """Publish a one-node branch_version and return its bvid."""
    branch = _seed_branch_dict(branch_def_id, entry=f"n_{branch_def_id}")
    v = publish_branch_version(
        tmp_path, branch,
        publisher="alice",
        parent_version_id=parent_version_id,
    )
    return v.branch_version_id


def _fork_branch(tmp_path, fork_def_id, fork_from_bvid):
    """Insert a branch_definitions row with fork_from set, then publish a
    version of it. Mirrors how `extensions action=fork_tree` would land
    a fork-child but skips the MCP layer for unit-test cleanliness.
    """
    from tinyassets.branches import (
        BranchDefinition,
        EdgeDefinition,
        GraphNodeRef,
        NodeDefinition,
    )
    from tinyassets.daemon_server import (
        initialize_author_server,
        save_branch_definition,
    )

    initialize_author_server(tmp_path)
    nd = NodeDefinition(node_id="n1", display_name="N1", prompt_template="echo")
    branch = BranchDefinition(
        branch_def_id=fork_def_id,
        name=f"Fork {fork_def_id}",
        graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
        edges=[EdgeDefinition(from_node="n1", to_node="END")],
        entry_point="n1",
        node_defs=[nd],
        state_schema=[],
        fork_from=fork_from_bvid,
    )
    save_branch_definition(tmp_path, branch_def=branch.to_dict())
    return publish_branch_version(
        tmp_path, branch.to_dict(), publisher="alice",
    ).branch_version_id


def _set_watch_metadata(
    tmp_path,
    branch_version_id,
    *,
    published_at,
    watch_window_seconds=86400,
    status="active",
):
    with _branch_versions_connect(tmp_path) as conn:
        conn.execute(
            """
            UPDATE branch_versions
               SET published_at = ?,
                   watch_window_seconds = ?,
                   status = ?
             WHERE branch_version_id = ?
            """,
            (
                published_at.isoformat(),
                watch_window_seconds,
                status,
                branch_version_id,
            ),
        )


# ─── compute_rollback_set ─────────────────────────────────────────────────


class TestComputeRollbackSet:
    def test_singleton_no_children(self, tmp_path):
        bvid = _publish(tmp_path, "b1")
        closure = compute_rollback_set(tmp_path, bvid)
        assert closure == [bvid]

    def test_forward_chain(self, tmp_path):
        # b1 → b2 → b3 (each child's parent_version_id points up)
        bvid1 = _publish(tmp_path, "chain-1")
        bvid2 = _publish(tmp_path, "chain-2", parent_version_id=bvid1)
        bvid3 = _publish(tmp_path, "chain-3", parent_version_id=bvid2)
        closure = compute_rollback_set(tmp_path, bvid1)
        assert set(closure) == {bvid1, bvid2, bvid3}

    def test_fork_children(self, tmp_path):
        # b1 (parent) ← fork-child branch_def with its own version
        parent = _publish(tmp_path, "fork-parent")
        fork_v = _fork_branch(tmp_path, "fork-child", parent)
        closure = compute_rollback_set(tmp_path, parent)
        assert parent in closure
        assert fork_v in closure

    def test_no_self_referential_cycle(self, tmp_path):
        # Verify the queue/visited logic terminates even if the schema
        # ever lets an ID enter the queue twice (defensive).
        bvid = _publish(tmp_path, "single")
        closure = compute_rollback_set(tmp_path, bvid)
        # Result is sorted + deduplicated.
        assert closure == sorted(set(closure))


# ─── execute_rollback_set ─────────────────────────────────────────────────


class TestExecuteRollbackSet:
    def test_happy_path_flips_status(self, tmp_path):
        bvid = _publish(tmp_path, "rollback-1")
        result = execute_rollback_set(
            tmp_path, [bvid], reason="test", set_by="alice", severity="P1",
        )
        assert result["status"] == "ok"
        assert result["rolled_back_count"] == 1
        version = get_branch_version(tmp_path, bvid)
        assert version.status == "rolled_back"
        assert version.rolled_back_by == "alice"
        assert version.rolled_back_reason == "test"

    def test_emits_caused_regression_event(self, tmp_path):
        bvid = _publish(tmp_path, "rollback-event")
        initialize_contribution_events_db(tmp_path)
        result = execute_rollback_set(
            tmp_path, [bvid], reason="canary RED", set_by="host", severity="P0",
        )
        assert result["status"] == "ok"
        assert len(result["event_ids"]) == 1
        # Confirm the event landed in contribution_events.
        from tinyassets.contribution_events import _connect
        with _connect(tmp_path) as conn:
            row = conn.execute(
                "SELECT event_type, source_artifact_id, weight, actor_id "
                "FROM contribution_events WHERE event_id = ?",
                (result["event_ids"][0],),
            ).fetchone()
        assert row is not None
        assert row["event_type"] == "caused_regression"
        assert row["source_artifact_id"] == bvid
        assert row["actor_id"] == "host"
        assert int(row["weight"]) == ROLLBACK_WEIGHTS["P0"]

    def test_empty_set_rejected(self, tmp_path):
        result = execute_rollback_set(
            tmp_path, [], reason="empty", set_by="alice",
        )
        assert result["status"] == "rejected"
        assert "empty" in result["error"].lower()

    def test_unknown_severity_rejected(self, tmp_path):
        bvid = _publish(tmp_path, "rollback-bad-sev")
        result = execute_rollback_set(
            tmp_path, [bvid], reason="x", set_by="alice", severity="P99",
        )
        assert result["status"] == "rejected"
        assert "severity" in result["error"].lower()
        # State must NOT have been mutated.
        version = get_branch_version(tmp_path, bvid)
        assert version.status == "active"

    def test_double_rollback_rejected_atomically(self, tmp_path):
        # First rollback flips status; second on same version must
        # reject AND not partially mutate any other version in the set.
        bvid_good = _publish(tmp_path, "rollback-good")
        bvid_already = _publish(tmp_path, "rollback-already")
        execute_rollback_set(
            tmp_path, [bvid_already], reason="first", set_by="alice",
        )
        # Now try a batch including the already-rolled version.
        result = execute_rollback_set(
            tmp_path, [bvid_good, bvid_already],
            reason="second", set_by="alice",
        )
        assert result["status"] == "rejected"
        assert "already" in result["error"].lower()
        # Atomic abort: the *good* version must NOT have been mutated.
        v_good = get_branch_version(tmp_path, bvid_good)
        assert v_good.status == "active"

    def test_nonexistent_version_rejected_atomically(self, tmp_path):
        bvid_good = _publish(tmp_path, "rollback-real")
        result = execute_rollback_set(
            tmp_path, [bvid_good, "ghost@deadbeef"],
            reason="x", set_by="alice",
        )
        assert result["status"] == "rejected"
        assert "ghost@deadbeef" in result["error"]
        # Atomic abort: the real version must remain active.
        v = get_branch_version(tmp_path, bvid_good)
        assert v.status == "active"

    def test_weight_severity_table(self):
        # Regression guard for the design §5 weight contract.
        assert ROLLBACK_WEIGHTS == {"P0": -10, "P1": -3, "P2": -1}
        assert AUTO_ROLLBACK_WEIGHT_THRESHOLD == -3


# ─── get_rollback_history ─────────────────────────────────────────────────


class TestWatchWindowSuspects:
    def test_suspects_are_active_within_window_after_last_green(self, tmp_path):
        now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
        last_green = now - timedelta(hours=2)

        before_green = _publish(tmp_path, "suspect-before-green")
        expired = _publish(tmp_path, "suspect-expired")
        rolled_back = _publish(tmp_path, "suspect-rolled")
        eligible = _publish(tmp_path, "suspect-eligible")

        _set_watch_metadata(
            tmp_path,
            before_green,
            published_at=last_green - timedelta(minutes=1),
        )
        _set_watch_metadata(
            tmp_path,
            expired,
            published_at=now - timedelta(hours=3),
            watch_window_seconds=60,
        )
        _set_watch_metadata(
            tmp_path,
            rolled_back,
            published_at=now - timedelta(minutes=30),
            status="rolled_back",
        )
        _set_watch_metadata(
            tmp_path,
            eligible,
            published_at=now - timedelta(minutes=10),
        )

        assert list_watch_window_suspects(
            tmp_path,
            last_green_at=last_green,
            now=now,
        ) == [eligible]


class TestBisectCanary:
    def test_bisect_returns_first_red_version_with_confirmation(self):
        suspects = ["v1", "v2", "v3", "v4"]
        calls = []

        def replay(version_id):
            calls.append(version_id)
            return "RED" if version_id in {"v3", "v4"} else "GREEN"

        assert bisect_canary(suspects, replay) == "v3"
        assert calls[-1] == "v3"

    def test_bisect_returns_none_when_confirmation_turns_green(self):
        suspects = ["v1", "v2"]
        calls = []

        def replay(version_id):
            calls.append(version_id)
            return "RED" if len(calls) == 1 else "GREEN"

        assert bisect_canary(suspects, replay) is None


class TestAutoRollbackOnCanaryRed:
    def test_single_p1_suspect_rolls_back_and_logs_without_replay(self, tmp_path):
        now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
        last_green = now - timedelta(hours=1)
        suspect = _publish(tmp_path, "auto-p1")
        _set_watch_metadata(
            tmp_path,
            suspect,
            published_at=now - timedelta(minutes=10),
        )

        result = auto_rollback_on_canary_red(
            tmp_path,
            canary_name="PROBE-001",
            last_green_at=last_green,
            severity="P1",
            reason="canary red",
            set_by="host",
            now=now,
            replay_canary_at_version=lambda _: pytest.fail("not needed"),
        )

        assert result["status"] == "rolled_back"
        assert result["culprit_version_id"] == suspect
        assert get_branch_version(tmp_path, suspect).status == "rolled_back"
        log_path = tmp_path / ".agents" / "rollback.log"
        assert "PROBE-001" in log_path.read_text(encoding="utf-8")

    def test_p2_suspect_records_event_without_rollback(self, tmp_path):
        now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
        suspect = _publish(tmp_path, "auto-p2")
        _set_watch_metadata(
            tmp_path,
            suspect,
            published_at=now - timedelta(minutes=10),
        )

        result = auto_rollback_on_canary_red(
            tmp_path,
            canary_name="PROBE-004",
            last_green_at=now - timedelta(hours=1),
            severity="P2",
            reason="low-severity canary red",
            set_by="host",
            now=now,
        )

        assert result["status"] == "recorded_only"
        assert get_branch_version(tmp_path, suspect).status == "active"
        assert result["event"]["weight"] == ROLLBACK_WEIGHTS["P2"]

    def test_many_suspects_escalate_without_rollback(self, tmp_path):
        now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
        version_ids = []
        for idx in range(33):
            bvid = _publish(tmp_path, f"too-many-{idx}")
            _set_watch_metadata(
                tmp_path,
                bvid,
                published_at=now - timedelta(minutes=33 - idx),
            )
            version_ids.append(bvid)

        result = auto_rollback_on_canary_red(
            tmp_path,
            canary_name="PROBE-001",
            last_green_at=now - timedelta(hours=1),
            severity="P1",
            reason="too many suspects",
            set_by="host",
            now=now,
        )

        assert result["status"] == "escalate"
        assert result["suspect_count"] == 33
        assert all(
            get_branch_version(tmp_path, bvid).status == "active"
            for bvid in version_ids
        )

    def test_multiple_suspects_bisect_then_rolls_back_culprit(self, tmp_path):
        now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
        suspects = []
        for idx in range(4):
            bvid = _publish(tmp_path, f"multi-{idx}")
            _set_watch_metadata(
                tmp_path,
                bvid,
                published_at=now - timedelta(minutes=4 - idx),
            )
            suspects.append(bvid)

        def replay(version_id):
            return "RED" if version_id in {suspects[2], suspects[3]} else "GREEN"

        result = auto_rollback_on_canary_red(
            tmp_path,
            canary_name="PROBE-002",
            last_green_at=now - timedelta(hours=1),
            severity="P1",
            reason="bisect red",
            set_by="host",
            now=now,
            replay_canary_at_version=replay,
        )

        assert result["status"] == "rolled_back"
        assert result["culprit_version_id"] == suspects[2]
        assert get_branch_version(tmp_path, suspects[2]).status == "rolled_back"
        assert get_branch_version(tmp_path, suspects[3]).status == "active"

    def test_p0_rollback_invokes_host_page_hook(self, tmp_path):
        now = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
        suspect = _publish(tmp_path, "auto-p0")
        _set_watch_metadata(
            tmp_path,
            suspect,
            published_at=now - timedelta(minutes=10),
        )
        pages = []

        def page_host(result):
            pages.append(result["culprit_version_id"])
            return {"status": "sent", "channel": "test"}

        result = auto_rollback_on_canary_red(
            tmp_path,
            canary_name="PROBE-001",
            last_green_at=now - timedelta(hours=1),
            severity="P0",
            reason="public MCP outage",
            set_by="host",
            now=now,
            page_host_on_p0=page_host,
        )

        assert result["status"] == "rolled_back"
        assert pages == [suspect]
        assert result["host_page"] == {"status": "sent", "channel": "test"}


class TestGetRollbackHistory:
    def test_empty_history(self, tmp_path):
        history = get_rollback_history(tmp_path, since_days=7)
        assert history == []

    def test_history_returns_rolled_back_only(self, tmp_path):
        bvid_active = _publish(tmp_path, "active-version")
        bvid_rolled = _publish(tmp_path, "rolled-version")
        execute_rollback_set(
            tmp_path, [bvid_rolled], reason="test", set_by="alice",
        )
        history = get_rollback_history(tmp_path, since_days=7)
        ids = [h["branch_version_id"] for h in history]
        assert bvid_rolled in ids
        assert bvid_active not in ids

    def test_history_includes_event_ids(self, tmp_path):
        bvid = _publish(tmp_path, "rolled-events")
        result = execute_rollback_set(
            tmp_path, [bvid], reason="test", set_by="alice", severity="P1",
        )
        history = get_rollback_history(tmp_path, since_days=7)
        assert len(history) == 1
        entry = history[0]
        assert entry["branch_version_id"] == bvid
        assert entry["rolled_back_reason"] == "test"
        assert result["event_ids"][0] in entry["event_ids"]
        assert ROLLBACK_WEIGHTS["P1"] in entry["weights"]


# ─── rollback_merge_orchestrator (compose closure → execute → re-point) ──


class TestOrchestrator:
    def test_orchestrator_composes_closure_execute_repoint(self, tmp_path):
        # Chain: parent → child. Roll back parent, expect both versions
        # in closure flipped, and a repoint dict in the result.
        parent = _publish(tmp_path, "orch-parent")
        child = _publish(tmp_path, "orch-child", parent_version_id=parent)
        result = rollback_merge_orchestrator(
            tmp_path, parent, reason="cascade test", set_by="host",
        )
        assert result["status"] == "ok"
        assert set(result["closure"]) == {parent, child}
        assert result["execute"]["rolled_back_count"] == 2
        assert "repoint" in result
        # No goals were bound, so repoint is a no-op.
        assert result["repoint"]["repointed_count"] == 0

    def test_orchestrator_propagates_reject(self, tmp_path):
        # Pre-roll a version, then try to orchestrator-roll it again.
        bvid = _publish(tmp_path, "orch-reject")
        execute_rollback_set(
            tmp_path, [bvid], reason="first", set_by="alice",
        )
        result = rollback_merge_orchestrator(
            tmp_path, bvid, reason="second", set_by="host",
        )
        assert result["status"] == "rejected"
        assert "already" in result["error"].lower()


# ─── MCP-surface tests (live universe_server fixture) ────────────────────
