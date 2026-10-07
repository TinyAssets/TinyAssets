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


_LADDER = [
    {"rung_key": "draft_complete", "name": "Draft complete",
     "description": "Full draft."},
    {"rung_key": "peer_reviewed", "name": "Peer reviewed",
     "description": "At least 2 reviewers."},
    {"rung_key": "submitted", "name": "Submitted",
     "description": "Submission tracked."},
]


def _bound_branch(us, gid, name):
    """Build a branch bound to ``gid`` (``build_branch`` takes ``goal_id``;
    an empty one leaves it unbound)."""
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


def _claim(base, bid, rung, url):
    """Write a gate claim through storage; the ``gates claim`` action is gone."""
    from tinyassets.daemon_server import claim_gate, get_branch_definition

    gid = get_branch_definition(base, branch_def_id=bid)["goal_id"]
    return claim_gate(base, branch_def_id=bid, goal_id=gid, rung_key=rung,
                      evidence_url=url, claimed_by="alice")


def _seed_goal_with_ladder(us, base):
    """A Goal with ``_LADDER`` and one bound branch. The ladder is written
    through storage; the ``define_ladder`` action is gone."""
    from tinyassets.daemon_server import set_goal_ladder

    g = _call(us, "goals", "propose", name="Research paper", description="x")
    gid = g["goal"]["goal_id"]
    bid = _bound_branch(us, gid, "LoRA v3")
    set_goal_ladder(base, goal_id=gid, ladder=_LADDER)
    return gid, bid


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


def test_goal_gate_summary_ladder_length_reflects_rungs(gates_on_env):
    from tinyassets.daemon_server import goal_gate_summary

    us, base = gates_on_env
    gid, _bid = _seed_goal_with_ladder(us, base)
    summary = goal_gate_summary(base, goal_id=gid)
    assert summary["ladder_length"] == 3
    assert summary["claims_total"] == 0
    assert summary["branches_with_claims"] == 0
    assert summary["highest_rung_reached"] == ""


def test_goal_gate_summary_counts_across_multiple_branches(gates_on_env):
    from tinyassets.daemon_server import goal_gate_summary

    us, base = gates_on_env
    gid, bid_a = _seed_goal_with_ladder(us, base)
    bid_b = _bound_branch(us, gid, "B")
    _claim(base, bid_a, "draft_complete", "https://example.com/a")
    _claim(base, bid_a, "peer_reviewed", "https://example.com/a2")
    _claim(base, bid_b, "draft_complete", "https://example.com/b")
    summary = goal_gate_summary(base, goal_id=gid)
    assert summary["claims_total"] == 3
    assert summary["branches_with_claims"] == 2
    assert summary["highest_rung_reached"] == "peer_reviewed"


def test_goal_gate_summary_ignores_retracted(gates_on_env):
    from tinyassets.daemon_server import goal_gate_summary, retract_gate_claim

    us, base = gates_on_env
    gid, bid = _seed_goal_with_ladder(us, base)
    _claim(base, bid, "submitted", "https://example.com/x")
    retract_gate_claim(base, branch_def_id=bid, rung_key="submitted",
                      reason="bogus evidence")
    summary = goal_gate_summary(base, goal_id=gid)
    assert summary["claims_total"] == 0
    assert summary["branches_with_claims"] == 0
    assert summary["highest_rung_reached"] == ""


def test_goal_gate_summary_ignores_orphaned_rungs(gates_on_env):
    from tinyassets.daemon_server import goal_gate_summary, set_goal_ladder

    us, base = gates_on_env
    gid, bid = _seed_goal_with_ladder(us, base)
    _claim(base, bid, "peer_reviewed", "https://example.com/x")
    # Shrink ladder so peer_reviewed becomes orphaned.
    shrunk = [r for r in _LADDER if r["rung_key"] != "peer_reviewed"]
    set_goal_ladder(base, goal_id=gid, ladder=shrunk)
    summary = goal_gate_summary(base, goal_id=gid)
    # Ladder length dropped; orphaned claim doesn't count.
    assert summary["ladder_length"] == 2
    assert summary["claims_total"] == 0
    assert summary["branches_with_claims"] == 0


# ───────────────────────────────────────────────────────────────────────
# goals get integration
# ───────────────────────────────────────────────────────────────────────


def test_goals_get_returns_gate_summary_populated(gates_on_env):
    us, base = gates_on_env
    gid, bid = _seed_goal_with_ladder(us, base)
    _claim(base, bid, "submitted", "https://example.com/x")
    result = _call(us, "goals", "get", goal_id=gid)
    summary = result["gate_summary"]
    assert summary["ladder_length"] == 3
    assert summary["claims_total"] == 1
    assert summary["branches_with_claims"] == 1
    assert summary["highest_rung_reached"] == "submitted"


def test_goals_get_gate_summary_empty_before_claims(gates_on_env):
    us, base = gates_on_env
    gid, _bid = _seed_goal_with_ladder(us, base)
    result = _call(us, "goals", "get", goal_id=gid)
    summary = result["gate_summary"]
    assert summary["ladder_length"] == 3
    assert summary["claims_total"] == 0
    assert summary["highest_rung_reached"] == ""


def test_goals_get_gate_summary_gated_off(gates_off_env):
    us, base = gates_off_env
    g = _call(us, "goals", "propose", name="G", description="x")
    gid = g["goal"]["goal_id"]
    result = _call(us, "goals", "get", goal_id=gid)
    # Gate-disabled: surfaces a flag-gated placeholder, not counters.
    assert result["gate_summary"] == {"status": "gates_disabled"}


# ───────────────────────────────────────────────────────────────────────
# extensions get_branch integration
# ───────────────────────────────────────────────────────────────────────


def test_get_branch_includes_gate_claims_populated(gates_on_env):
    us, base = gates_on_env
    gid, bid = _seed_goal_with_ladder(us, base)
    _claim(base, bid, "draft_complete", "https://example.com/x")
    _claim(base, bid, "peer_reviewed", "https://example.com/y")
    result = _call(us, "extensions", "get_branch", branch_def_id=bid)
    assert "gate_claims" in result
    assert len(result["gate_claims"]) == 2
    rungs = {c["rung_key"] for c in result["gate_claims"]}
    assert rungs == {"draft_complete", "peer_reviewed"}


def test_get_branch_gate_claims_excludes_retracted(gates_on_env):
    from tinyassets.daemon_server import retract_gate_claim

    us, base = gates_on_env
    gid, bid = _seed_goal_with_ladder(us, base)
    _claim(base, bid, "draft_complete", "https://example.com/x")
    _claim(base, bid, "peer_reviewed", "https://example.com/y")
    retract_gate_claim(base, branch_def_id=bid, rung_key="peer_reviewed",
                      reason="bogus")
    result = _call(us, "extensions", "get_branch", branch_def_id=bid)
    # Only non-retracted claim surfaces.
    assert len(result["gate_claims"]) == 1
    assert result["gate_claims"][0]["rung_key"] == "draft_complete"


def test_get_branch_empty_gate_claims_when_no_claims(gates_on_env):
    us, _ = gates_on_env
    bid = _bound_branch(us, "", "Solo")
    result = _call(us, "extensions", "get_branch", branch_def_id=bid)
    assert result["gate_claims"] == []
    # No gate_status when flag is on — empty list means "no claims yet."
    assert "gate_status" not in result


def test_get_branch_gated_off(gates_off_env):
    us, _ = gates_off_env
    bid = _bound_branch(us, "", "Solo")
    result = _call(us, "extensions", "get_branch", branch_def_id=bid)
    # Gate-disabled: empty list + explicit status, so UI can render
    # "gates off" distinct from "no claims."
    assert result["gate_claims"] == []
    assert result["gate_status"] == "gates_disabled"


def test_goal_gate_summary_hides_existing_claims(tmp_path, monkeypatch, authenticate_request):
    """Symmetric to the branch-get flip test: seeding claims under
    GATES_ENABLED=1 and then flipping to 0 must make goals-get
    surface the `gates_disabled` placeholder, hiding stored counters.
    """
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    monkeypatch.setenv("GATES_ENABLED", "1")
    # Built inline rather than via gates_on_env, so the credential has to be
    # issued here too — seeding the claims is a write.
    authenticate_request("alice", capabilities=_ALICE_SCOPES)
    from tinyassets import universe_server as us
    importlib.reload(us)
    try:
        gid, bid = _seed_goal_with_ladder(us, base)
        _claim(base, bid, "draft_complete", "https://example.com/x")
        _claim(base, bid, "peer_reviewed", "https://example.com/y")
        # Sanity: on-state surfaces populated counters.
        on_result = _call(us, "goals", "get", goal_id=gid)
        assert on_result["gate_summary"]["claims_total"] == 2
        assert on_result["gate_summary"]["highest_rung_reached"] == "peer_reviewed"
        # Flip to off; reload so the env-check re-reads.
        monkeypatch.delenv("GATES_ENABLED", raising=False)
        importlib.reload(us)
        off_result = _call(us, "goals", "get", goal_id=gid)
        assert off_result["gate_summary"] == {"status": "gates_disabled"}
        # Stored claims are NOT leaked through any other response key.
        assert "claims_total" not in json.dumps(off_result["gate_summary"])
    finally:
        importlib.reload(us)


def test_get_branch_gates_off_hides_existing_claims(tmp_path, monkeypatch, authenticate_request):
    """A daemon flipping GATES_ENABLED from 1 to 0 must hide previously
    stored claims — the fallback is not a cache of the last on-state.
    """
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    monkeypatch.setenv("GATES_ENABLED", "1")
    # Built inline rather than via gates_on_env, so the credential has to be
    # issued here too — seeding the claims is a write.
    authenticate_request("alice", capabilities=_ALICE_SCOPES)
    from tinyassets import universe_server as us
    importlib.reload(us)
    try:
        gid, bid = _seed_goal_with_ladder(us, base)
        _claim(base, bid, "draft_complete", "https://example.com/x")
        # Flip flag off; reload module so the env-check re-reads.
        monkeypatch.delenv("GATES_ENABLED", raising=False)
        importlib.reload(us)
        result = _call(us, "extensions", "get_branch", branch_def_id=bid)
        assert result["gate_claims"] == []
        assert result["gate_status"] == "gates_disabled"
        # Same flip for goals get.
        goal_result = _call(us, "goals", "get", goal_id=gid)
        assert goal_result["gate_summary"] == {"status": "gates_disabled"}
    finally:
        importlib.reload(us)
