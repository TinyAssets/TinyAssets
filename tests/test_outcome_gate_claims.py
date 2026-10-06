"""Outcome Gates retract, list_claims, and leaderboard behavior.

Covers docs/specs/outcome_gates_phase6.md rollout details:
- retract: soft-delete, claimant/goal-owner/`retract_gate_claim`-grant
  authority, reason required.
- list_claims: one-filter rule, include_retracted, orphan tagging.
- leaderboard: highest-rung ordering, earliest-claim tiebreak, ignores
  retracted and orphaned claims.
- goals leaderboard metric=outcome delegation.
- define_ladder `define_gate_ladder`-grant override.
"""

from __future__ import annotations

import importlib
import json

import pytest

# Every scope this file's actions need. `gates.costly` covers `claim` /
# `claim_from_branch_run`; `gates.admin` covers `retract` / `define_ladder`
# (see `_GATES_COSTLY_ACTIONS` / `_GATES_ADMIN_ACTIONS` in auth/provider.py).
#
# Granting admin+costly here is NOT the vacuous-pass hazard the shared
# conftest default guards against: no test in this file asserts a *scope*
# refusal. The refusals it does assert — non-owner retract, non-author
# define_ladder — live one layer deeper, in market.py's actor/capability
# check, and are unreachable until the scope gate is satisfied. Withholding
# the scope made those tests fail on the wrong error and assert nothing
# about the authority rule they exist to pin.
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
def gates_env(tmp_path, monkeypatch, authenticate_request):
    """Temp data root plus an authenticated subject.

    Branch mutation requires a credential-derived subject; without it every
    action returns `{"error": "Authenticated branch subject required."}` and
    each test dies on `KeyError: 'branch_def_id'`. Authenticate BEFORE
    reloading `universe_server`, which rebinds module state.

    Yields the `authenticate_request` callable as its third element so tests
    can switch acting user. Setting `UNIVERSE_SERVER_USER` no longer does
    that: `_current_actor` prefers the request identity and only falls back
    to the env var when there is none, so once this fixture authenticates
    alice, a `monkeypatch.setenv` user switch is silently ignored.
    """
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    monkeypatch.setenv("GATES_ENABLED", "1")
    authenticate_request("alice", capabilities=_ALICE_SCOPES)
    from tinyassets import universe_server as us
    importlib.reload(us)
    yield us, base, authenticate_request
    importlib.reload(us)


def _call(us, tool, action, **kwargs):
    from tinyassets.api.market import gates

    fn = gates if tool == "gates" else getattr(us, f"_{tool}_impl")
    return json.loads(fn(action=action, **kwargs))


# ─── retract ───────────────────────────────────────────────────────────


# ─── list_claims ───────────────────────────────────────────────────────


# ─── leaderboard ───────────────────────────────────────────────────────


def test_leaderboard_requires_goal_id(gates_env):
    us, _, _ = gates_env
    result = _call(us, "gates", "leaderboard")
    assert result["status"] == "rejected"


def test_leaderboard_rejects_unknown_goal(gates_env):
    us, _, _ = gates_env
    result = _call(us, "gates", "leaderboard", goal_id="nope")
    assert result["status"] == "rejected"
    assert "not found" in result["error"]


# ─── goals leaderboard metric=outcome delegation ───────────────────────


# ─── define_ladder host override ───────────────────────────────────────


# ─── goals leaderboard outcome gated-off fallback (Phase 6.2.1) ───────


def test_goals_leaderboard_outcome_gated_off(tmp_path, monkeypatch):
    """GATES_ENABLED=0: outcome falls back to a friendly gated envelope,
    not an empty live-leaderboard result.
    """
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    monkeypatch.delenv("GATES_ENABLED", raising=False)
    from tinyassets import universe_server as us
    importlib.reload(us)
    try:
        gid = json.loads(us._goals_impl(
            action="propose", name="G", description="x",
        ))["goal"]["goal_id"]
        result = json.loads(us._goals_impl(
            action="leaderboard", goal_id=gid, metric="outcome",
        ))
        assert result["status"] == "gates_disabled"
        assert result["entries"] == []
        assert "GATES_ENABLED" in result["text"]
    finally:
        importlib.reload(us)


# ─── branch_rebound guard (Phase 6.2.1) ────────────────────────────────
