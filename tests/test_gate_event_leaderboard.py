"""Tests for gate-event leaderboard — rank branches by attributed gate events.

Spec: openspec/specs/paid-market-economy/spec.md §gate-based leaderboard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tinyassets.branch_versions import publish_branch_version
from tinyassets.gate_events import attest_gate_event, leaderboard_by_gate_events, verify_gate_event
from tinyassets.gate_events.store import dispute_gate_event, retract_gate_event
from tinyassets.runs import initialize_runs_db


def _seed_bv(base_path: Path, branch_id: str, publisher: str = "alice") -> str:
    from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
    from tinyassets.daemon_server import initialize_author_server, save_branch_definition

    initialize_author_server(base_path)
    nd = NodeDefinition(node_id="n1", display_name="N1", prompt_template="do X")
    branch = BranchDefinition(
        branch_def_id=branch_id,
        name=f"Branch {branch_id}",
        graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
        edges=[EdgeDefinition(from_node="n1", to_node="END")],
        entry_point="n1",
        node_defs=[nd],
        state_schema=[],
    )
    save_branch_definition(base_path, branch_def=branch.to_dict())
    v = publish_branch_version(base_path, branch.to_dict(), publisher=publisher)
    return v.branch_version_id


class TestLeaderboardEmpty:
    def test_zero_events_returns_empty(self, tmp_path):
        initialize_runs_db(tmp_path)
        result = leaderboard_by_gate_events(tmp_path, goal_id="g1")
        assert result["goal_id"] == "g1"
        assert result["ranked"] == []
        assert result["total_events_in_window"] == 0

    def test_window_field_echoed(self, tmp_path):
        initialize_runs_db(tmp_path)
        result = leaderboard_by_gate_events(tmp_path, goal_id="g1", window="30d")
        assert result["window"] == "30d"

    def test_invalid_window_raises(self, tmp_path):
        initialize_runs_db(tmp_path)
        with pytest.raises(ValueError, match="window"):
            leaderboard_by_gate_events(tmp_path, goal_id="g1", window="invalid")

    def test_missing_goal_id_raises(self, tmp_path):
        initialize_runs_db(tmp_path)
        with pytest.raises(ValueError, match="goal_id"):
            leaderboard_by_gate_events(tmp_path, goal_id="")


class TestLeaderboardRanking:
    def test_single_event_ranks_single_branch(self, tmp_path):
        initialize_runs_db(tmp_path)
        bv1 = _seed_bv(tmp_path, "b1")
        attest_gate_event(
            tmp_path,
            goal_id="g1",
            event_type="peer_review_accepted",
            event_date="2026-04-24",
            attested_by="alice",
            cites=[{"branch_version_id": bv1}],
        )
        result = leaderboard_by_gate_events(tmp_path, goal_id="g1")
        assert len(result["ranked"]) == 1
        assert result["ranked"][0]["branch_version_id"] == bv1
        assert result["ranked"][0]["gate_event_count"] == 1
        assert result["total_events_in_window"] == 1

    def test_higher_count_ranks_first(self, tmp_path):
        initialize_runs_db(tmp_path)
        bv1 = _seed_bv(tmp_path, "b1")
        bv2 = _seed_bv(tmp_path, "b2")
        # b2 gets 2 events, b1 gets 1.
        attest_gate_event(
            tmp_path, goal_id="g1", event_type="publication",
            event_date="2026-04-20", attested_by="alice",
            cites=[{"branch_version_id": bv2}],
        )
        attest_gate_event(
            tmp_path, goal_id="g1", event_type="publication",
            event_date="2026-04-21", attested_by="alice",
            cites=[{"branch_version_id": bv2}],
        )
        attest_gate_event(
            tmp_path, goal_id="g1", event_type="publication",
            event_date="2026-04-22", attested_by="alice",
            cites=[{"branch_version_id": bv1}],
        )
        result = leaderboard_by_gate_events(tmp_path, goal_id="g1")
        assert result["ranked"][0]["branch_version_id"] == bv2
        assert result["ranked"][1]["branch_version_id"] == bv1

    def test_disputed_events_excluded(self, tmp_path):
        initialize_runs_db(tmp_path)
        bv1 = _seed_bv(tmp_path, "b1")
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="publication",
            event_date="2026-04-24", attested_by="alice",
            cites=[{"branch_version_id": bv1}],
        )
        dispute_gate_event(
            tmp_path, event_id=evt.event_id,
            disputed_by="bob", reason="contested",
        )
        result = leaderboard_by_gate_events(tmp_path, goal_id="g1")
        assert result["ranked"] == []
        assert result["total_events_in_window"] == 0

    def test_retracted_events_excluded(self, tmp_path):
        initialize_runs_db(tmp_path)
        bv1 = _seed_bv(tmp_path, "b1")
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="publication",
            event_date="2026-04-24", attested_by="alice",
            cites=[{"branch_version_id": bv1}],
        )
        retract_gate_event(
            tmp_path, event_id=evt.event_id,
            retracted_by="alice", note="error",
        )
        result = leaderboard_by_gate_events(tmp_path, goal_id="g1")
        assert result["ranked"] == []

    def test_verified_events_weighted_2x_by_default(self, tmp_path):
        initialize_runs_db(tmp_path)
        bv1 = _seed_bv(tmp_path, "b1")
        bv2 = _seed_bv(tmp_path, "b2")
        # b1 gets 1 verified event (score = 2x), b2 gets 2 attested events (score = 2).
        # Tie at score 2 — b2 has more recent date so it should sort second by date.
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="publication",
            event_date="2026-04-20", attested_by="alice",
            cites=[{"branch_version_id": bv1}],
        )
        verify_gate_event(tmp_path, event_id=evt.event_id, verifier_id="bob")
        attest_gate_event(
            tmp_path, goal_id="g1", event_type="publication",
            event_date="2026-04-21", attested_by="alice",
            cites=[{"branch_version_id": bv2}],
        )
        attest_gate_event(
            tmp_path, goal_id="g1", event_type="publication",
            event_date="2026-04-22", attested_by="alice",
            cites=[{"branch_version_id": bv2}],
        )
        result = leaderboard_by_gate_events(tmp_path, goal_id="g1")
        # b1 verified score = 2.0; b2 attested x2 score = 2.0 — tied.
        # Among ties, most_recent_event_date breaks tie: b2 has 2026-04-22 vs b1 2026-04-20.
        # But both score exactly 2.0. With date tiebreak, b2 should rank first.
        scores = {e["branch_version_id"]: e["score"] for e in result["ranked"]}
        assert scores[bv1] == pytest.approx(2.0)
        assert scores[bv2] == pytest.approx(2.0)

    def test_verified_count_in_entry(self, tmp_path):
        initialize_runs_db(tmp_path)
        bv1 = _seed_bv(tmp_path, "b1")
        evt = attest_gate_event(
            tmp_path, goal_id="g1", event_type="publication",
            event_date="2026-04-24", attested_by="alice",
            cites=[{"branch_version_id": bv1}],
        )
        verify_gate_event(tmp_path, event_id=evt.event_id, verifier_id="bob")
        result = leaderboard_by_gate_events(tmp_path, goal_id="g1")
        entry = result["ranked"][0]
        assert entry["verified_event_count"] == 1
        assert entry["gate_event_count"] == 1

    def test_event_type_breakdown(self, tmp_path):
        initialize_runs_db(tmp_path)
        bv1 = _seed_bv(tmp_path, "b1")
        attest_gate_event(
            tmp_path, goal_id="g1", event_type="peer_review_accepted",
            event_date="2026-04-23", attested_by="alice",
            cites=[{"branch_version_id": bv1}],
        )
        attest_gate_event(
            tmp_path, goal_id="g1", event_type="publication",
            event_date="2026-04-24", attested_by="alice",
            cites=[{"branch_version_id": bv1}],
        )
        result = leaderboard_by_gate_events(tmp_path, goal_id="g1")
        types = result["ranked"][0]["gate_event_types"]
        assert types.get("peer_review_accepted") == 1
        assert types.get("publication") == 1

    def test_limit_caps_results(self, tmp_path):
        initialize_runs_db(tmp_path)
        # Create 3 branches with events.
        for i in range(1, 4):
            bv = _seed_bv(tmp_path, f"b{i}")
            attest_gate_event(
                tmp_path, goal_id="g1", event_type="publication",
                event_date=f"2026-04-2{i}", attested_by="alice",
                cites=[{"branch_version_id": bv}],
            )
        result = leaderboard_by_gate_events(tmp_path, goal_id="g1", limit=2)
        assert len(result["ranked"]) == 2

    def test_most_recent_event_date_in_entry(self, tmp_path):
        initialize_runs_db(tmp_path)
        bv1 = _seed_bv(tmp_path, "b1")
        attest_gate_event(
            tmp_path, goal_id="g1", event_type="publication",
            event_date="2026-04-20", attested_by="alice",
            cites=[{"branch_version_id": bv1}],
        )
        attest_gate_event(
            tmp_path, goal_id="g1", event_type="publication",
            event_date="2026-04-24", attested_by="alice",
            cites=[{"branch_version_id": bv1}],
        )
        result = leaderboard_by_gate_events(tmp_path, goal_id="g1")
        assert result["ranked"][0]["most_recent_event_date"] == "2026-04-24"
