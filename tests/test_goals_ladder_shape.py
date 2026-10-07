"""PR-126 M5 sub-task 1 — confirm the bound Goal's ladder is queryable
in structured form so branch authors can populate `recommended_rung_claim`
against a real rung_key vocabulary.

The substrate already returns the ladder rungs via two paths:

  * `goals action=get goal_id=<g>` → response.goal.gate_ladder is a
    list of `{rung_key, name, description}` dicts (Phase 6 schema).
  * `gates action=get_ladder goal_id=<g>` → response.gate_ladder is
    the same list.

This module locks in both contracts so a future schema migration that
flattens the ladder would surface here first, not when a branch's
`claim_from_branch_run` call hits an unexpected shape.
"""

from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture
def us_env(tmp_path, monkeypatch, authenticate_request):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path / "output"))
    (tmp_path / "output").mkdir()
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    monkeypatch.setenv("GATES_ENABLED", "1")
    monkeypatch.setenv("TINYASSETS_STORAGE_BACKEND", "sqlite")
    authenticate_request("tester")
    from tinyassets.auth.middleware import current_identity
    current_identity().capabilities.extend([
        "tinyassets.goals.read",
        "tinyassets.goals.write",
        "tinyassets.gates.read",
        "tinyassets.gates.admin",
    ])
    from tinyassets.catalog import backend as backend_mod
    backend_mod.invalidate_backend_cache()
    from tinyassets import universe_server as us
    importlib.reload(us)
    yield us, tmp_path / "output"
    backend_mod.invalidate_backend_cache()
    importlib.reload(us)


def _call(us, tool, action, **kwargs):
    if tool == "gates":
        from tinyassets.api.market import gates as fn
    else:
        fn = getattr(us, f"_{tool}_impl")
    return json.loads(fn(action=action, **kwargs))


_PATCH_LOOP_LADDER = [
    {"rung_key": "draft_ready",
     "name": "Draft ready",
     "description": "Branch emitted a candidate patch."},
    {"rung_key": "review_passed",
     "name": "Review passed",
     "description": "Cross-family checker approved."},
    {"rung_key": "merged",
     "name": "Merged",
     "description": "Patch landed on main."},
]


_FANTASY_LADDER = [
    {"rung_key": "first_draft",
     "name": "First draft",
     "description": "Chapter complete."},
    {"rung_key": "beta_reader_pass",
     "name": "Beta reader pass",
     "description": "Two beta readers approved."},
    {"rung_key": "published",
     "name": "Published",
     "description": "Available to read."},
]



# ---------------------------------------------------------------------------
# `goals action=get` carries gate_ladder
# ---------------------------------------------------------------------------


def test_goals_get_returns_gate_ladder_in_structured_form(us_env):
    from tinyassets.daemon_server import set_goal_ladder

    us, base = us_env
    g = _call(us, "goals", "propose", name="Patch loop", description="x")
    gid = g["goal"]["goal_id"]
    set_goal_ladder(base, goal_id=gid, ladder=_PATCH_LOOP_LADDER)
    result = _call(us, "goals", "get", goal_id=gid)
    goal = result["goal"]
    assert "gate_ladder" in goal
    ladder = goal["gate_ladder"]
    assert isinstance(ladder, list)
    assert [r["rung_key"] for r in ladder] == [
        "draft_ready", "review_passed", "merged",
    ]
    # Each rung has the three Phase-6 fields a branch author needs to
    # render the ladder vocabulary back to the user.
    for rung in ladder:
        assert isinstance(rung, dict)
        assert rung.get("rung_key")
        assert rung.get("name")
        assert "description" in rung


def test_goals_get_returns_empty_ladder_when_undefined(us_env):
    us, _ = us_env
    g = _call(us, "goals", "propose", name="No-ladder goal")
    gid = g["goal"]["goal_id"]
    result = _call(us, "goals", "get", goal_id=gid)
    goal = result["goal"]
    # Empty ladder is `[]`, not absent — branch authors can read the
    # field unconditionally without a KeyError guard.
    assert goal.get("gate_ladder") == []


def test_gates_get_ladder_returns_same_shape(us_env):
    from tinyassets.daemon_server import set_goal_ladder

    us, base = us_env
    g = _call(us, "goals", "propose", name="Fantasy novel")
    gid = g["goal"]["goal_id"]
    set_goal_ladder(base, goal_id=gid, ladder=_FANTASY_LADDER)
    via_gates = _call(us, "gates", "get_ladder", goal_id=gid)
    via_goals = _call(us, "goals", "get", goal_id=gid)
    assert via_gates["gate_ladder"] == via_goals["goal"]["gate_ladder"]


# ---------------------------------------------------------------------------
# Goal-genericity — different ladders for different Goals
# ---------------------------------------------------------------------------


def test_two_goals_can_have_independent_ladders(us_env):
    """Same primitive, different ladder vocabularies — patch loop's
    `draft_ready` doesn't collide with fantasy's `first_draft`.
    Ladders are written through storage; ``define_ladder`` is gone."""
    from tinyassets.daemon_server import set_goal_ladder

    us, base = us_env
    g_patch = _call(us, "goals", "propose", name="Patch loop")
    g_fantasy = _call(us, "goals", "propose", name="Fantasy novel")
    pid = g_patch["goal"]["goal_id"]
    fid = g_fantasy["goal"]["goal_id"]
    set_goal_ladder(base, goal_id=pid, ladder=_PATCH_LOOP_LADDER)
    set_goal_ladder(base, goal_id=fid, ladder=_FANTASY_LADDER)
    patch_rungs = [
        r["rung_key"]
        for r in _call(us, "goals", "get", goal_id=pid)["goal"]["gate_ladder"]
    ]
    fantasy_rungs = [
        r["rung_key"]
        for r in _call(us, "goals", "get", goal_id=fid)["goal"]["gate_ladder"]
    ]
    assert patch_rungs == ["draft_ready", "review_passed", "merged"]
    assert fantasy_rungs == ["first_draft", "beta_reader_pass", "published"]
    # No cross-contamination.
    assert set(patch_rungs) & set(fantasy_rungs) == set()
