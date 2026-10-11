"""Phase B post-rollback goal-canonical re-point tests (Task #22).

Spec: 2026-04-25-surgical-rollback-proposal.md (archived in git) §2.4 +
cross-DB refinement note in tinyassets/rollback.py module docstring.

Covers:
- Cascading walk-up: when both the canonical and its parent are in the
  closure, walk up to the next non-rolled-back ancestor.
- Missing-ancestor edge case: if no eligible ancestor exists, the goal's
  canonical is unset (re-pointed to None).
- Goals NOT pointing into the closure are untouched.
- The repoint dict logs every affected goal.
"""

from __future__ import annotations

from tinyassets.branch_versions import publish_branch_version
from tinyassets.daemon_server import (
    get_goal,
    initialize_author_server,
    save_goal,
    set_canonical_branch,
)
from tinyassets.rollback import (
    execute_rollback_set,
    repoint_goals_after_rollback,
    rollback_merge_orchestrator,
)


def _seed_branch_dict(branch_def_id, parent_version_id=None):
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


def _publish(tmp_path, branch_def_id, parent_version_id=None):
    v = publish_branch_version(
        tmp_path, _seed_branch_dict(branch_def_id),
        publisher="alice", parent_version_id=parent_version_id,
    )
    return v.branch_version_id


def _seed_goal(tmp_path, goal_id="g1", canonical_bvid=None):
    initialize_author_server(tmp_path)
    save_goal(tmp_path, goal={
        "goal_id": goal_id, "name": f"Goal {goal_id}", "author": "alice",
    })
    if canonical_bvid is not None:
        set_canonical_branch(
            tmp_path, goal_id=goal_id,
            branch_version_id=canonical_bvid, set_by="alice",
        )


# ─── single-version repoint ──────────────────────────────────────────────


class TestSingleVersionRepoint:
    def test_repoint_to_immediate_active_parent(self, tmp_path):
        # parent (active) ← child (rolled back); goal canonical was
        # pointing at child → should re-point to parent.
        parent = _publish(tmp_path, "rp-parent")
        child = _publish(tmp_path, "rp-child", parent_version_id=parent)
        _seed_goal(tmp_path, "g1", canonical_bvid=child)
        # Roll back the child only (parent stays active).
        execute_rollback_set(
            tmp_path, [child], reason="t", set_by="alice",
        )
        result = repoint_goals_after_rollback(
            tmp_path, [child], set_by="alice",
        )
        assert result["repointed_count"] == 1
        repoint = result["repoints"][0]
        assert repoint["goal_id"] == "g1"
        assert repoint["old_branch_version_id"] == child
        assert repoint["new_branch_version_id"] == parent
        # Confirm the goal canonical actually moved.
        goal = get_goal(tmp_path, goal_id="g1")
        assert goal["canonical_branch_version_id"] == parent

    def test_unaffected_goal_not_touched(self, tmp_path):
        # Goal canonical points at a DIFFERENT version, not in closure.
        unrelated = _publish(tmp_path, "rp-unrelated")
        target = _publish(tmp_path, "rp-target")
        _seed_goal(tmp_path, "g1", canonical_bvid=unrelated)
        execute_rollback_set(
            tmp_path, [target], reason="t", set_by="alice",
        )
        result = repoint_goals_after_rollback(
            tmp_path, [target], set_by="alice",
        )
        assert result["repointed_count"] == 0
        goal = get_goal(tmp_path, goal_id="g1")
        assert goal["canonical_branch_version_id"] == unrelated


# ─── cascading walk-up ───────────────────────────────────────────────────


class TestCascadingWalkUp:
    def test_walk_skips_rolled_back_ancestors(self, tmp_path):
        # active → rolled-1 → rolled-2 (canonical here).
        # Goal canonical at rolled-2 should re-point to `active`,
        # skipping rolled-1.
        active = _publish(tmp_path, "wu-active")
        rolled_1 = _publish(tmp_path, "wu-r1", parent_version_id=active)
        rolled_2 = _publish(tmp_path, "wu-r2", parent_version_id=rolled_1)
        _seed_goal(tmp_path, "g1", canonical_bvid=rolled_2)
        # Roll back BOTH rolled_1 and rolled_2.
        execute_rollback_set(
            tmp_path, [rolled_1, rolled_2], reason="t", set_by="alice",
        )
        result = repoint_goals_after_rollback(
            tmp_path, [rolled_1, rolled_2], set_by="alice",
        )
        assert result["repointed_count"] == 1
        assert result["repoints"][0]["new_branch_version_id"] == active
        goal = get_goal(tmp_path, goal_id="g1")
        assert goal["canonical_branch_version_id"] == active


# ─── missing-ancestor edge case ──────────────────────────────────────────


class TestMissingAncestor:
    def test_no_eligible_ancestor_unsets_canonical(self, tmp_path):
        # Single rolled-back version with no parent → walk-up returns
        # None → goal canonical is unset.
        only = _publish(tmp_path, "ma-only")  # no parent_version_id
        _seed_goal(tmp_path, "g1", canonical_bvid=only)
        execute_rollback_set(
            tmp_path, [only], reason="t", set_by="alice",
        )
        result = repoint_goals_after_rollback(
            tmp_path, [only], set_by="alice",
        )
        assert result["repointed_count"] == 1
        assert result["repoints"][0]["new_branch_version_id"] is None
        goal = get_goal(tmp_path, goal_id="g1")
        assert goal["canonical_branch_version_id"] is None

    def test_all_ancestors_rolled_back_unsets_canonical(self, tmp_path):
        # Every version in the chain is rolled back → no eligible ancestor.
        v1 = _publish(tmp_path, "ar-1")
        v2 = _publish(tmp_path, "ar-2", parent_version_id=v1)
        v3 = _publish(tmp_path, "ar-3", parent_version_id=v2)
        _seed_goal(tmp_path, "g1", canonical_bvid=v3)
        execute_rollback_set(
            tmp_path, [v1, v2, v3], reason="t", set_by="alice",
        )
        result = repoint_goals_after_rollback(
            tmp_path, [v1, v2, v3], set_by="alice",
        )
        assert result["repointed_count"] == 1
        assert result["repoints"][0]["new_branch_version_id"] is None


# ─── orchestrator integration ────────────────────────────────────────────


class TestOrchestratorIntegrationWithGoals:
    def test_orchestrator_repoints_goal_end_to_end(self, tmp_path):
        # Full path: orchestrator computes closure, executes, re-points.
        parent = _publish(tmp_path, "int-parent")
        child = _publish(tmp_path, "int-child", parent_version_id=parent)
        _seed_goal(tmp_path, "g1", canonical_bvid=child)
        # Roll back the parent — closure includes both parent + child;
        # goal canonical was at child → must re-point past both to None.
        result = rollback_merge_orchestrator(
            tmp_path, parent, reason="cascade", set_by="alice",
        )
        assert result["status"] == "ok"
        assert set(result["closure"]) == {parent, child}
        assert result["repoint"]["repointed_count"] == 1
        # Both versions in closure → walk-up has nowhere to go → None.
        assert result["repoint"]["repoints"][0]["new_branch_version_id"] is None
        goal = get_goal(tmp_path, goal_id="g1")
        assert goal["canonical_branch_version_id"] is None

    def test_multiple_goals_repointed_independently(self, tmp_path):
        # Goal A points at child; Goal B points at parent. Roll back
        # parent → A gets re-pointed to None, B gets re-pointed to None.
        parent = _publish(tmp_path, "mg-parent")
        child = _publish(tmp_path, "mg-child", parent_version_id=parent)
        _seed_goal(tmp_path, "ga", canonical_bvid=child)
        _seed_goal(tmp_path, "gb", canonical_bvid=parent)
        result = rollback_merge_orchestrator(
            tmp_path, parent, reason="t", set_by="alice",
        )
        assert result["status"] == "ok"
        assert result["repoint"]["repointed_count"] == 2
        assert get_goal(tmp_path, goal_id="ga")["canonical_branch_version_id"] is None
        assert get_goal(tmp_path, goal_id="gb")["canonical_branch_version_id"] is None


# ─── DESIGN-008 round 4 — selector repoint after rollback ───────────────


def _seed_goal_with_selector(tmp_path, goal_id, selector_bvid):
    """Bind a selector to a Goal via the storage helper. Bypasses the
    bind-time effects check by writing the column directly via
    update_goal, since the test fixture branches are pure prompt
    nodes anyway."""
    initialize_author_server(tmp_path)
    save_goal(tmp_path, goal={
        "goal_id": goal_id, "name": f"Goal {goal_id}", "author": "alice",
    })
    from tinyassets.daemon_server import update_goal
    update_goal(
        tmp_path,
        goal_id=goal_id,
        updates={"selector_branch_version_id": selector_bvid},
    )


class TestSelectorRepoint:
    """DESIGN-008 round 4 — selector_branch_version_id pointers are
    cleared (NULL) when the version is rolled back. Unlike canonical,
    selectors do NOT walk up to an ancestor: re-binding to a different
    version silently could introduce a ranking opinion the operator
    didn't choose. Cleared bindings trigger the platform-default
    fallback at the next leaderboard read."""

    def test_clears_selector_pointer_on_rollback(self, tmp_path):
        from tinyassets.rollback import repoint_selectors_after_rollback
        selector_v = _publish(tmp_path, "selector-v1")
        _seed_goal_with_selector(tmp_path, "g-sel", selector_v)
        execute_rollback_set(
            tmp_path, [selector_v], reason="t", set_by="alice",
        )
        result = repoint_selectors_after_rollback(
            tmp_path, [selector_v], set_by="alice",
        )
        assert result["status"] == "ok"
        assert result["repointed_count"] == 1
        repoint_row = result["repoints"][0]
        assert repoint_row["goal_id"] == "g-sel"
        assert repoint_row["old_branch_version_id"] == selector_v
        assert repoint_row["new_branch_version_id"] is None
        goal = get_goal(tmp_path, goal_id="g-sel")
        assert goal["selector_branch_version_id"] is None

    def test_unaffected_goal_left_alone(self, tmp_path):
        from tinyassets.rollback import repoint_selectors_after_rollback
        rolled = _publish(tmp_path, "rolled-selector")
        other = _publish(tmp_path, "other-selector")
        _seed_goal_with_selector(tmp_path, "g-rolled", rolled)
        _seed_goal_with_selector(tmp_path, "g-other", other)
        execute_rollback_set(
            tmp_path, [rolled], reason="t", set_by="alice",
        )
        result = repoint_selectors_after_rollback(
            tmp_path, [rolled], set_by="alice",
        )
        assert result["repointed_count"] == 1
        rolled_ids = {r["goal_id"] for r in result["repoints"]}
        assert rolled_ids == {"g-rolled"}
        # Other goal's selector binding untouched.
        assert get_goal(
            tmp_path, goal_id="g-other",
        )["selector_branch_version_id"] == other

    def test_no_bindings_no_op(self, tmp_path):
        from tinyassets.rollback import repoint_selectors_after_rollback
        bvid = _publish(tmp_path, "lonely-version")
        execute_rollback_set(tmp_path, [bvid], reason="t", set_by="alice")
        # No Goals bound to it → repoint is a clean no-op.
        result = repoint_selectors_after_rollback(
            tmp_path, [bvid], set_by="alice",
        )
        assert result["repointed_count"] == 0
        assert result["repoints"] == []

    def test_empty_version_set_short_circuits(self, tmp_path):
        from tinyassets.rollback import repoint_selectors_after_rollback
        initialize_author_server(tmp_path)
        result = repoint_selectors_after_rollback(
            tmp_path, [], set_by="alice",
        )
        assert result["repointed_count"] == 0

    def test_orchestrator_runs_both_canonical_and_selector_repoint(self, tmp_path):
        """End-to-end through ``rollback_merge_orchestrator``: a
        single rollback call clears BOTH a canonical pointer AND a
        selector pointer on the same Goal."""
        bvid = _publish(tmp_path, "dual-pointer-version")
        _seed_goal(tmp_path, "g-dual", canonical_bvid=bvid)
        # ALSO bind it as selector via the column write path.
        from tinyassets.daemon_server import update_goal
        update_goal(
            tmp_path,
            goal_id="g-dual",
            updates={"selector_branch_version_id": bvid},
        )
        result = rollback_merge_orchestrator(
            tmp_path, bvid, reason="dual cleanup", set_by="alice",
        )
        assert result["status"] == "ok"
        assert result["repoint"]["repointed_count"] == 1
        assert result["selector_repoint"]["repointed_count"] == 1
        goal = get_goal(tmp_path, goal_id="g-dual")
        assert goal["canonical_branch_version_id"] is None
        assert goal["selector_branch_version_id"] is None
