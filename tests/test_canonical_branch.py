"""Tests for canonical_branch storage layer.

Spec: openspec/specs/community-patch-loop/spec.md.
Topic: canonical_branch — first-experience fork target per Goal.
"""

from __future__ import annotations

import pytest

from tinyassets.branch_versions import publish_branch_version
from tinyassets.daemon_server import (
    get_canonical_branch_history,
    get_goal,
    save_goal,
    set_canonical_branch,
)


def _seed_goal(base_path, goal_id: str = "g1") -> dict:
    from tinyassets.daemon_server import initialize_author_server
    initialize_author_server(base_path)
    return save_goal(base_path, goal={"goal_id": goal_id, "name": "Test Goal",
                                       "author": "alice"})


def _seed_branch_version(base_path, branch_id: str = "b1") -> str:
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
    v = publish_branch_version(base_path, branch.to_dict(), publisher="alice")
    return v.branch_version_id


class TestCanonicalBranchDefaults:
    def test_goal_has_no_canonical_by_default(self, tmp_path):
        _seed_goal(tmp_path)
        goal = get_goal(tmp_path, goal_id="g1")
        assert goal["canonical_branch_version_id"] is None

    def test_goal_has_empty_canonical_history_by_default(self, tmp_path):
        _seed_goal(tmp_path)
        goal = get_goal(tmp_path, goal_id="g1")
        assert goal["canonical_branch_history"] == []

    def test_get_canonical_branch_history_nonexistent_goal_returns_empty(self, tmp_path):
        from tinyassets.daemon_server import initialize_author_server
        initialize_author_server(tmp_path)
        result = get_canonical_branch_history(tmp_path, goal_id="missing_goal")
        assert result == []


class TestSetCanonicalBranch:
    def test_set_canonical_records_version_id(self, tmp_path):
        _seed_goal(tmp_path)
        bvid = _seed_branch_version(tmp_path)
        goal = set_canonical_branch(
            tmp_path, goal_id="g1",
            branch_version_id=bvid, set_by="alice",
        )
        assert goal["canonical_branch_version_id"] == bvid

    def test_set_canonical_no_initial_history_entry(self, tmp_path):
        _seed_goal(tmp_path)
        bvid = _seed_branch_version(tmp_path)
        set_canonical_branch(
            tmp_path, goal_id="g1",
            branch_version_id=bvid, set_by="alice",
        )
        history = get_canonical_branch_history(tmp_path, goal_id="g1")
        assert history == []

    def test_set_canonical_twice_records_history(self, tmp_path):
        _seed_goal(tmp_path)
        bvid1 = _seed_branch_version(tmp_path, "b1")
        bvid2 = _seed_branch_version(tmp_path, "b2")
        set_canonical_branch(
            tmp_path, goal_id="g1",
            branch_version_id=bvid1, set_by="alice",
        )
        set_canonical_branch(
            tmp_path, goal_id="g1",
            branch_version_id=bvid2, set_by="alice",
        )
        goal = get_goal(tmp_path, goal_id="g1")
        assert goal["canonical_branch_version_id"] == bvid2
        history = get_canonical_branch_history(tmp_path, goal_id="g1")
        assert len(history) == 1
        assert history[0]["branch_version_id"] == bvid1
        assert history[0]["replaced_by"] == bvid2
        assert "unset_at" in history[0]

    def test_history_accumulates_across_multiple_changes(self, tmp_path):
        _seed_goal(tmp_path)
        bvid1 = _seed_branch_version(tmp_path, "b1")
        bvid2 = _seed_branch_version(tmp_path, "b2")
        bvid3 = _seed_branch_version(tmp_path, "b3")
        set_canonical_branch(tmp_path, goal_id="g1", branch_version_id=bvid1, set_by="alice")
        set_canonical_branch(tmp_path, goal_id="g1", branch_version_id=bvid2, set_by="alice")
        set_canonical_branch(tmp_path, goal_id="g1", branch_version_id=bvid3, set_by="alice")
        history = get_canonical_branch_history(tmp_path, goal_id="g1")
        assert len(history) == 2
        assert history[0]["branch_version_id"] == bvid1
        assert history[1]["branch_version_id"] == bvid2

    def test_unset_canonical_sets_to_none(self, tmp_path):
        _seed_goal(tmp_path)
        bvid = _seed_branch_version(tmp_path)
        set_canonical_branch(tmp_path, goal_id="g1", branch_version_id=bvid, set_by="alice")
        goal = set_canonical_branch(tmp_path, goal_id="g1", branch_version_id=None, set_by="alice")
        assert goal["canonical_branch_version_id"] is None
        history = get_canonical_branch_history(tmp_path, goal_id="g1")
        assert len(history) == 1
        assert history[0]["branch_version_id"] == bvid
        assert history[0]["replaced_by"] is None

    def test_nonexistent_goal_raises_key_error(self, tmp_path):
        from tinyassets.daemon_server import initialize_author_server
        initialize_author_server(tmp_path)
        bvid = _seed_branch_version(tmp_path)
        with pytest.raises(KeyError):
            set_canonical_branch(
                tmp_path, goal_id="missing",
                branch_version_id=bvid, set_by="alice",
            )

    def test_invalid_branch_version_id_raises_value_error(self, tmp_path):
        _seed_goal(tmp_path)
        with pytest.raises(ValueError, match="branch_version_id"):
            set_canonical_branch(
                tmp_path, goal_id="g1",
                branch_version_id="not-a-real-version",
                set_by="alice",
            )

    def test_get_goal_includes_canonical_in_response(self, tmp_path):
        _seed_goal(tmp_path)
        bvid = _seed_branch_version(tmp_path)
        set_canonical_branch(tmp_path, goal_id="g1", branch_version_id=bvid, set_by="alice")
        goal = get_goal(tmp_path, goal_id="g1")
        assert "canonical_branch_version_id" in goal
        assert "canonical_branch_history" in goal
        assert goal["canonical_branch_version_id"] == bvid
