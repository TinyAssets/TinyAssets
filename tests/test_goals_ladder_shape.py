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
    return json.loads(getattr(us, f"_{tool}_impl")(action=action, **kwargs))


# ---------------------------------------------------------------------------
# `goals action=get` carries gate_ladder
# ---------------------------------------------------------------------------


def test_goals_get_returns_empty_ladder_when_undefined(us_env):
    us, _ = us_env
    g = _call(us, "goals", "propose", name="No-ladder goal")
    gid = g["goal"]["goal_id"]
    result = _call(us, "goals", "get", goal_id=gid)
    goal = result["goal"]
    # Empty ladder is `[]`, not absent — branch authors can read the
    # field unconditionally without a KeyError guard.
    assert goal.get("gate_ladder") == []


# ---------------------------------------------------------------------------
# Goal-genericity — different ladders for different Goals
# ---------------------------------------------------------------------------
