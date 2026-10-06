"""Outcome gates integration into goals get and branch get.

Covers docs/specs/outcome_gates_phase6.md rollout details:
- `goals get goal_id=X` gains `gate_summary` field.
- `extensions get_branch branch_def_id=X` gains `gate_claims` field.
- Both threads through GATES_ENABLED=0 fallback (6.2.1 precedent):
  `gate_summary: {status: "gates_disabled"}` and
  `gate_claims: [], gate_status: "gates_disabled"`.

Read-only surface. No new storage / git primitives. No mutation path.
"""

from __future__ import annotations

import importlib
import json

import pytest

# ───────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────


# Branch mutation requires a credential-derived subject, and the scope check
# is per-family: this file drives `extensions`, `gates` AND `goals`. Nothing
# here asserts a *scope* refusal — every assertion is about what the READ
# surfaces expose — so granting the writes these fixtures perform costs no
# assertion strength. `extensions.costly` stays withheld.
_ALICE_SCOPES = [
    "tinyassets.extensions.read",
    "tinyassets.extensions.write",
    "tinyassets.extensions.admin",
    "tinyassets.gates.read",
    "tinyassets.gates.write",
    "tinyassets.gates.costly",
    "tinyassets.gates.admin",
    "tinyassets.goals.read",
    "tinyassets.goals.write",
]


@pytest.fixture
def gates_on_env(tmp_path, monkeypatch, authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    monkeypatch.setenv("GATES_ENABLED", "1")
    authenticate_request("alice", capabilities=_ALICE_SCOPES)
    from tinyassets import universe_server as us
    importlib.reload(us)
    yield us, base
    importlib.reload(us)


@pytest.fixture
def gates_off_env(tmp_path, monkeypatch, authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    monkeypatch.delenv("GATES_ENABLED", raising=False)
    authenticate_request("alice", capabilities=_ALICE_SCOPES)
    from tinyassets import universe_server as us
    importlib.reload(us)
    yield us, base
    importlib.reload(us)


def _call(us, tool, action, **kwargs):
    return json.loads(getattr(us, f"_{tool}_impl")(action=action, **kwargs))


# ───────────────────────────────────────────────────────────────────────
# goal_gate_summary (backend unit)
# ───────────────────────────────────────────────────────────────────────


def test_goal_gate_summary_empty_when_no_ladder(gates_on_env):
    from tinyassets.daemon_server import goal_gate_summary

    us, base = gates_on_env
    g = _call(us, "goals", "propose", name="G", description="x")
    gid = g["goal"]["goal_id"]
    summary = goal_gate_summary(base, goal_id=gid)
    assert summary == {
        "ladder_length": 0,
        "claims_total": 0,
        "branches_with_claims": 0,
        "highest_rung_reached": "",
    }


# ───────────────────────────────────────────────────────────────────────
# goals get integration
# ───────────────────────────────────────────────────────────────────────


def test_goals_get_gate_summary_gated_off(gates_off_env):
    us, _base = gates_off_env
    g = _call(us, "goals", "propose", name="G", description="x")
    gid = g["goal"]["goal_id"]
    result = _call(us, "goals", "get", goal_id=gid)
    # Gate-disabled: surfaces a flag-gated placeholder, not counters.
    assert result["gate_summary"] == {"status": "gates_disabled"}


# ───────────────────────────────────────────────────────────────────────
# extensions get_branch integration
# ───────────────────────────────────────────────────────────────────────
