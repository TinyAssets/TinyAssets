"""DESIGN-008 — tests for goals action=set_selector MCP wiring.

Spec: drafts/concepts/selector-branch-contract.md
Implementation:
  * tinyassets/api/market.py::_action_goal_set_selector
  * tinyassets/daemon_server.py::set_selector_branch
  * tinyassets/api/quality_leaderboard.py — consumes the binding
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def env(tmp_path: Path, monkeypatch, authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    # Scopes are per tool family; this file drives `goals` as well as
    # `extensions`, and the default credential only carries `extensions.*`.
    authenticate_request(
        "alice",
        capabilities=[
            "tinyassets.extensions.read",
            "tinyassets.extensions.write",
            "tinyassets.extensions.admin",
            "tinyassets.goals.read",
            "tinyassets.goals.write",
        ],
    )
    monkeypatch.setenv("_FORCE_MOCK", "true")
    from tinyassets import universe_server as us
    importlib.reload(us)
    yield us, base
    importlib.reload(us)


def _call(us, tool, action, **kwargs):
    fn = getattr(us, f"_{tool}_impl")
    return json.loads(fn(action=action, **kwargs))


def _seed_goal(us, name="Selector binding test Goal"):
    result = _call(us, "goals", "propose", name=name)
    assert result["status"] == "proposed", result
    return result["goal"]["goal_id"]


# ---------------------------------------------------------------------------
# Dispatch table wiring
# ---------------------------------------------------------------------------


def test_set_selector_action_in_goal_actions():
    from tinyassets.api.market import _GOAL_ACTIONS
    assert "set_selector" in _GOAL_ACTIONS


def test_set_selector_in_goal_write_actions():
    from tinyassets.api.market import _GOAL_WRITE_ACTIONS
    assert "set_selector" in _GOAL_WRITE_ACTIONS


# ---------------------------------------------------------------------------
# MCP surface — happy path
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Authority
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# DESIGN-008 round 2 P1.3 — bind rejects effectful selector branches
# ---------------------------------------------------------------------------


def _publish_effectful_branch(base, name="effectful-selector"):
    """Publish a branch_version whose snapshot declares effects.

    Selector branches that declare ``effects`` (e.g. github_pull_request)
    would silently fire external writes on every leaderboard read —
    set_selector_branch must reject the bind.
    """
    from tinyassets.branch_versions import publish_branch_version
    from tinyassets.daemon_server import (
        get_branch_definition,
        save_branch_definition,
    )
    bid = "effectful_rank_branch"
    save_branch_definition(
        base,
        branch_def=dict(
            branch_def_id=bid,
            name=name,
            description="",
            author="alice",
            tags=[],
            graph_nodes=[
                {
                    "id": "rank",
                    "type": "prompt",
                    "phase": "custom",
                    "input_keys": ["candidate_branches"],
                    "output_keys": ["ranked_entries"],
                },
            ],
            edges=[
                {"from": "START", "to": "rank"},
                {"from": "rank", "to": "END"},
            ],
            state_schema=[
                {"name": "candidate_branches", "type": "list"},
                {"name": "ranked_entries", "type": "list"},
            ],
            entry_point="rank",
            published=True,
            visibility="public",
            node_defs=[
                {
                    "node_id": "rank",
                    "display_name": "Rank",
                    "phase": "custom",
                    "input_keys": ["candidate_branches"],
                    "output_keys": ["ranked_entries"],
                    "prompt_template": "rank {candidate_branches}",
                    # P1.3 fail-trigger: this node declares an effect.
                    "effects": ["github_pull_request"],
                },
            ],
        ),
    )
    branch_dict = get_branch_definition(base, branch_def_id=bid)
    version = publish_branch_version(base, branch_dict, publisher="alice")
    return version.branch_version_id


def test_set_selector_storage_layer_raises_selector_has_effects(env):
    """Storage-layer regression — set_selector_branch raises
    SelectorHasEffectsError on an effectful version, no MCP wrapper
    involved."""
    us, base = env
    gid = _seed_goal(us)
    bvid = _publish_effectful_branch(base)
    from tinyassets.daemon_server import (
        SelectorHasEffectsError,
        set_selector_branch,
    )
    with pytest.raises(SelectorHasEffectsError) as exc_info:
        set_selector_branch(
            base, goal_id=gid,
            branch_version_id=bvid, set_by="host",
        )
    assert "effects" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# DESIGN-008 round 3 P1.B — child-branch invocation rejected at bind time
# ---------------------------------------------------------------------------


def _publish_branch_with_invoke_branch_spec(base, name="invoking-selector"):
    """Publish a branch_version with no direct effects but a node
    that invokes a child branch via ``invoke_branch_spec``.

    Round-3 P1.B: the round-2 purity scan only inspected direct
    ``effects`` on node_defs. A selector with no direct effects can
    still spawn a child run via invoke_branch_spec — the child's
    completion fires the child's effectors. Bind must reject.
    """
    from tinyassets.branch_versions import publish_branch_version
    from tinyassets.daemon_server import (
        get_branch_definition,
        save_branch_definition,
    )
    bid = "invoking_rank_branch"
    save_branch_definition(
        base,
        branch_def=dict(
            branch_def_id=bid,
            name=name,
            description="",
            author="alice",
            tags=[],
            graph_nodes=[
                {
                    "id": "rank",
                    "type": "prompt",
                    "phase": "custom",
                    "input_keys": ["candidate_branches"],
                    "output_keys": ["ranked_entries"],
                },
            ],
            edges=[
                {"from": "START", "to": "rank"},
                {"from": "rank", "to": "END"},
            ],
            state_schema=[
                {"name": "candidate_branches", "type": "str"},
                {"name": "ranked_entries", "type": "str"},
            ],
            entry_point="rank",
            published=True,
            visibility="public",
            node_defs=[
                {
                    "node_id": "rank",
                    "display_name": "Rank",
                    "phase": "custom",
                    "input_keys": ["candidate_branches"],
                    "output_keys": ["ranked_entries"],
                    # No direct effects, but it invokes a child
                    # branch — that child's completion path fires
                    # ITS effectors. P1.B fail-trigger.
                    "invoke_branch_spec": {
                        "branch_def_id": "some_effectful_child",
                        "inputs_mapping": {},
                        "output_mapping": {"ranked_entries": "result"},
                        "wait_mode": "blocking",
                    },
                },
            ],
        ),
    )
    branch_dict = get_branch_definition(base, branch_def_id=bid)
    version = publish_branch_version(base, branch_dict, publisher="alice")
    return version.branch_version_id


def test_set_selector_storage_layer_raises_on_child_invoker(env):
    """Direct storage-layer assertion that the new purity guard fires
    on a branch that has no direct effects but invokes children."""
    us, base = env
    gid = _seed_goal(us)
    bvid = _publish_branch_with_invoke_branch_spec(base)
    from tinyassets.daemon_server import (
        SelectorHasEffectsError,
        set_selector_branch,
    )
    with pytest.raises(SelectorHasEffectsError) as exc_info:
        set_selector_branch(
            base, goal_id=gid,
            branch_version_id=bvid, set_by="host",
        )
    msg = str(exc_info.value).lower()
    assert "child" in msg or "invoke" in msg
