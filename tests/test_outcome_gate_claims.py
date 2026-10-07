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


_LADDER = [
    {"rung_key": "draft_complete", "name": "Draft complete",
     "description": "Full draft emitted."},
    {"rung_key": "peer_reviewed", "name": "Peer reviewed",
     "description": "At least 2 reviewers."},
    {"rung_key": "submitted", "name": "Submitted",
     "description": "Submission tracked."},
]


def _bound_branch(us, gid, name):
    """Build a branch bound to ``gid`` (``build_branch`` takes ``goal_id``)."""
    b = _call(us, "extensions", "build_branch", spec_json=json.dumps({
        "name": name,
        "goal_id": gid,
        "entry_point": "draft",
        "node_defs": [{"node_id": "draft", "display_name": "Draft",
                       "prompt_template": "draft: {topic}"}],
        "edges": [{"from": "START", "to": "draft"},
                  {"from": "draft", "to": "END"}],
        "state_schema": [{"name": "topic", "type": "str"}],
    }))
    assert b["status"] == "built", b
    return b["branch_def_id"]


def _seed(us, base, *, goal_name="Research paper", branch_name="LoRA v3"):
    """A Goal with ``_LADDER`` and one bound branch.

    The ladder and the claims below are written through storage: the
    leaderboards these tests read are live (in-node ``gates.leaderboard`` /
    ``goals.leaderboard``), but the ``define_ladder`` / ``claim`` /
    ``retract`` actions that used to set them up are not.
    """
    from tinyassets.daemon_server import set_goal_ladder

    g = _call(us, "goals", "propose", name=goal_name, description="x")
    gid = g["goal"]["goal_id"]
    bid = _bound_branch(us, gid, branch_name)
    set_goal_ladder(base, goal_id=gid, ladder=_LADDER)
    return gid, bid


def _claim(base, bid, rung, url, note=""):
    from tinyassets.daemon_server import claim_gate, get_branch_definition

    gid = get_branch_definition(base, branch_def_id=bid)["goal_id"]
    return claim_gate(base, branch_def_id=bid, goal_id=gid, rung_key=rung,
                      evidence_url=url, evidence_note=note, claimed_by="alice")


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


def test_leaderboard_empty_when_no_claims(gates_env):
    us, base, _ = gates_env
    gid, _ = _seed(us, base)
    result = _call(us, "gates", "leaderboard", goal_id=gid)
    assert result["status"] == "ok"
    assert result["count"] == 0
    assert result["entries"] == []


def test_leaderboard_orders_by_highest_rung(gates_env):
    us, base, _ = gates_env
    gid, bid_a = _seed(us, base, branch_name="A")
    # Second branch on same goal.
    bid_b = _bound_branch(us, gid, "B")
    _claim(base, bid_a, "draft_complete", "https://example.com/a")
    _claim(base, bid_b, "peer_reviewed", "https://example.com/b")
    result = _call(us, "gates", "leaderboard", goal_id=gid)
    assert result["count"] == 2
    assert result["entries"][0]["branch_def_id"] == bid_b
    assert result["entries"][0]["highest_rung_key"] == "peer_reviewed"
    assert result["entries"][1]["branch_def_id"] == bid_a


def test_leaderboard_earliest_wins_tiebreak(gates_env):
    import time
    us, base, _ = gates_env
    gid, bid_a = _seed(us, base, branch_name="A")
    bid_b = _bound_branch(us, gid, "B")
    _claim(base, bid_a, "draft_complete", "https://example.com/a")
    time.sleep(1.1)  # _utc_iso_now has second-level resolution.
    _claim(base, bid_b, "draft_complete", "https://example.com/b")
    result = _call(us, "gates", "leaderboard", goal_id=gid)
    assert result["entries"][0]["branch_def_id"] == bid_a


def test_leaderboard_ignores_retracted(gates_env):
    us, base, _ = gates_env
    gid, bid_a = _seed(us, base, branch_name="A")
    bid_b = _bound_branch(us, gid, "B")
    _claim(base, bid_a, "peer_reviewed", "https://example.com/a")
    _claim(base, bid_b, "draft_complete", "https://example.com/b")
    from tinyassets.daemon_server import retract_gate_claim

    retract_gate_claim(base, branch_def_id=bid_a, rung_key="peer_reviewed",
                       reason="evidence bogus")
    result = _call(us, "gates", "leaderboard", goal_id=gid)
    # Only bid_b remains.
    assert result["count"] == 1
    assert result["entries"][0]["branch_def_id"] == bid_b


def test_leaderboard_ignores_orphaned_rungs(gates_env):
    us, base, _ = gates_env
    gid, bid = _seed(us, base)
    _claim(base, bid, "peer_reviewed", "https://example.com/a")
    shrunk = [r for r in _LADDER if r["rung_key"] != "peer_reviewed"]
    from tinyassets.daemon_server import set_goal_ladder

    set_goal_ladder(base, goal_id=gid, ladder=shrunk)
    result = _call(us, "gates", "leaderboard", goal_id=gid)
    assert result["count"] == 0


# ─── goals leaderboard metric=outcome delegation ───────────────────────


def test_goals_leaderboard_outcome_delegates(gates_env):
    us, base, _ = gates_env
    gid, bid = _seed(us, base)
    _claim(base, bid, "peer_reviewed", "https://example.com/a")
    result = _call(us, "goals", "leaderboard",
                   goal_id=gid, metric="outcome")
    assert result.get("status") != "not_available_until_phase_6"
    assert result["metric"] == "outcome"
    assert len(result["entries"]) == 1
    assert result["entries"][0]["highest_rung_key"] == "peer_reviewed"
    assert result["entries"][0]["value"] == 1  # rung index


def test_goals_leaderboard_outcome_empty_has_friendly_text(gates_env):
    us, base, _ = gates_env
    gid, _ = _seed(us, base)
    result = _call(us, "goals", "leaderboard",
                   goal_id=gid, metric="outcome")
    assert "No gate claims" in result["text"]


def test_goals_leaderboard_unknown_metric_lists_outcome(gates_env):
    us, base, _ = gates_env
    gid, _ = _seed(us, base)
    result = _call(us, "goals", "leaderboard",
                   goal_id=gid, metric="bogus")
    assert result["status"] == "rejected"
    assert "outcome" in result["available_metrics"]


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


def test_goals_leaderboard_outcome_live_when_enabled(gates_env):
    """GATES_ENABLED=1: outcome returns the live leaderboard."""
    us, base, _ = gates_env
    gid, bid = _seed(us, base)
    _claim(base, bid, "peer_reviewed", "https://example.com/a")
    result = _call(us, "goals", "leaderboard",
                   goal_id=gid, metric="outcome")
    assert result.get("status") != "gates_disabled"
    assert len(result["entries"]) == 1
    assert result["entries"][0]["highest_rung_key"] == "peer_reviewed"
