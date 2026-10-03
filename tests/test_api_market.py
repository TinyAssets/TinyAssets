"""Task #14 — direct tests for `tinyassets.api.market` after decomp Step 7.

Legacy goals/gates/escrow/outcome/attribution tests still cover the
chatbot-facing `tinyassets.universe_server` MCP wrappers. This file exercises
`tinyassets.api.market` directly to lock in the canonical implementation surface.
"""

from __future__ import annotations

import json

import pytest

from tinyassets.api import market as mkt_mod
from tinyassets.api.market import (
    _ATTRIBUTION_ACTIONS,
    _ESCROW_ACTIONS,
    _GATE_EVENT_ACTIONS,
    _GATES_ACTIONS,
    _GOAL_ACTIONS,
    _GOAL_WRITE_ACTIONS,
    _OUTCOME_ACTIONS,
    _action_attest_gate_event,
    _action_escrow_inspect,
    _action_get_outcome,
    _action_goal_propose,
    _action_record_remix,
    _gates_enabled,
    gates,
    goals,
)

# ── module surface ──────────────────────────────────────────────────────────


def test_module_exposes_expected_public_names():
    """Contract surface — guards against silent removal."""
    expected = {
        # Dispatch tables
        "_ESCROW_ACTIONS", "_OUTCOME_ACTIONS", "_ATTRIBUTION_ACTIONS",
        "_GOAL_ACTIONS", "_GOAL_WRITE_ACTIONS", "_dispatch_goal_action",
        "_GATES_ACTIONS", "_GATE_EVENT_ACTIONS",
        # Plain MCP-tool callables (Pattern A2)
        "goals", "gates",
        # Escrow handlers
        "_action_escrow_lock", "_action_escrow_release",
        "_action_escrow_refund", "_action_escrow_inspect",
        # Outcome handlers
        "_action_record_outcome", "_action_list_outcomes",
        "_action_get_outcome",
        # Attribution handlers
        "_action_record_remix", "_action_get_provenance",
        # Goal handlers (9)
        "_action_goal_propose", "_action_goal_update", "_action_goal_bind",
        "_action_goal_list", "_action_goal_get", "_action_goal_search",
        "_action_goal_leaderboard", "_action_goal_common_nodes",
        "_action_goal_set_canonical",
        # Gates main handlers (10)
        "_action_gates_define_ladder", "_action_gates_get_ladder",
        "_action_gates_claim", "_action_gates_claim_from_branch_run",
        "_action_gates_retract",
        "_action_gates_list_claims", "_action_gates_leaderboard",
        "_action_gates_stake_bonus", "_action_gates_unstake_bonus",
        "_action_gates_release_bonus",
        # Gate event handlers (6)
        "_action_attest_gate_event", "_action_verify_gate_event",
        "_action_dispute_gate_event", "_action_retract_gate_event",
        "_action_get_gate_event", "_action_list_gate_events",
        # Internal helpers
        "_current_actor_or_anon", "_gates_enabled",
    }
    missing = expected - set(dir(mkt_mod))
    assert not missing, f"market.py is missing public names: {missing}"


# ── _ESCROW_ACTIONS dispatch table ──────────────────────────────────────────


def test_escrow_actions_keys():
    expected = {
        "escrow_lock", "escrow_release", "escrow_refund", "escrow_inspect",
        "escrow_fund", "escrow_balance", "escrow_set_wallet", "escrow_withdraw",
    }
    assert set(_ESCROW_ACTIONS.keys()) == expected


# ── _OUTCOME_ACTIONS dispatch table ─────────────────────────────────────────


def test_outcome_actions_keys():
    assert set(_OUTCOME_ACTIONS.keys()) == {"record_outcome", "list_outcomes", "get_outcome"}


# ── _ATTRIBUTION_ACTIONS dispatch table ─────────────────────────────────────


def test_attribution_actions_keys():
    assert set(_ATTRIBUTION_ACTIONS.keys()) == {"record_remix", "get_provenance"}


# ── _GOAL_ACTIONS dispatch table ────────────────────────────────────────────


def test_goal_actions_keys():
    expected = {
        "propose", "update", "bind", "list", "get", "search",
        "leaderboard", "common_nodes", "set_canonical",
        "archive_consultation",
        # PR-127 — leaderboard-driven canonical dispatch.
        "run_canonical",
        # DESIGN-008 — user-buildable selector primitive.
        "set_selector",
        # Goal-bound branch protocols.
        "define_protocol", "get_protocol",
    }
    assert set(_GOAL_ACTIONS.keys()) == expected


def test_goal_write_actions_subset_of_goal_actions():
    assert _GOAL_WRITE_ACTIONS <= set(_GOAL_ACTIONS.keys())


def test_goal_write_actions_includes_state_mutators():
    """propose, update, bind, set_canonical are state-mutating writes."""
    for w in ("propose", "update", "bind", "set_canonical"):
        assert w in _GOAL_WRITE_ACTIONS


def test_goal_read_actions_excluded_from_write_set():
    for r in ("list", "get", "search", "leaderboard", "common_nodes"):
        assert r not in _GOAL_WRITE_ACTIONS


@pytest.mark.parametrize(
    "provider_action, canonical_action, kwargs",
    [
        ("list_workflow_goals", "list", {}),
        ("search_workflow_goals", "search", {"query": "onboarding"}),
        ("get_workflow_goal", "get", {"goal_id": "goal_123"}),
        ("propose_workflow_goal", "propose", {"name": "Alias smoke"}),
    ],
)
def test_goal_directory_action_aliases_dispatch_to_canonical_actions(
    monkeypatch, provider_action, canonical_action, kwargs,
):
    """ChatGPT can route directory tool names through legacy Goals."""
    from tinyassets.api import branches as branches_mod

    monkeypatch.setattr(branches_mod, "_ensure_workflow_db", lambda: None)
    seen = {}

    def fake_dispatch(action, handler, goal_kwargs):
        seen["action"] = action
        seen["handler"] = handler
        seen["goal_kwargs"] = goal_kwargs
        return json.dumps({"status": "ok", "canonical_action": action})

    monkeypatch.setattr(mkt_mod, "_dispatch_goal_action", fake_dispatch)

    out = json.loads(goals(action=provider_action, **kwargs))

    assert out == {"status": "ok", "canonical_action": canonical_action}
    assert seen["action"] == canonical_action
    assert seen["handler"] is _GOAL_ACTIONS[canonical_action]
    for key, value in kwargs.items():
        assert seen["goal_kwargs"][key] == value


def test_goals_unknown_action_lists_directory_aliases():
    out = json.loads(goals(action="totally_bogus_action"))
    assert "propose_workflow_goal" in out["available_actions"]
    assert "search_workflow_goals" in out["available_actions"]


# ── _GATES_ACTIONS dispatch table ───────────────────────────────────────────


def test_gates_actions_keys():
    expected = {
        "define_ladder", "get_ladder", "claim", "retract", "list",
        "list_claims", "leaderboard",
        "stake_bonus", "unstake_bonus", "release_bonus",
        # PR-126 M5 — claim from completed run's recommended_rung_claim.
        "claim_from_branch_run",
        # Conformance-pack registry handlers.
        "record_conformance_pack", "list_conformance_packs", "get_conformance_pack",
    }
    assert set(_GATES_ACTIONS.keys()) == expected


def test_gate_event_actions_keys():
    expected = {
        "attest_gate_event", "verify_gate_event", "dispute_gate_event",
        "retract_gate_event", "get_gate_event", "list_gate_events",
    }
    assert set(_GATE_EVENT_ACTIONS.keys()) == expected


# ── all dispatch handlers callable ──────────────────────────────────────────


@pytest.mark.parametrize(
    "table_name, table",
    [
        ("_ESCROW_ACTIONS", _ESCROW_ACTIONS),
        ("_OUTCOME_ACTIONS", _OUTCOME_ACTIONS),
        ("_ATTRIBUTION_ACTIONS", _ATTRIBUTION_ACTIONS),
        ("_GOAL_ACTIONS", _GOAL_ACTIONS),
        ("_GATES_ACTIONS", _GATES_ACTIONS),
        ("_GATE_EVENT_ACTIONS", _GATE_EVENT_ACTIONS),
    ],
)
def test_dispatch_table_handlers_are_callable(table_name, table):
    for action, handler in table.items():
        assert callable(handler), f"{table_name}[{action}] not callable"


# ── handler error paths (no monkeypatch) ────────────────────────────────────


def test_action_escrow_inspect_missing_args_returns_error():
    out = json.loads(_action_escrow_inspect({}))
    assert "error" in out


def test_action_get_outcome_missing_args_returns_error():
    out = json.loads(_action_get_outcome({}))
    assert "error" in out


def test_action_record_remix_missing_args_returns_error():
    out = json.loads(_action_record_remix({}))
    assert "error" in out


def test_action_goal_propose_missing_name_returns_error():
    out = json.loads(_action_goal_propose({}))
    assert "error" in out


def test_action_attest_gate_event_missing_args_returns_error():
    out = json.loads(_action_attest_gate_event({}))
    assert "error" in out


# ── plain goals() and gates() callables ─────────────────────────────────────


def test_goals_unknown_action_returns_error():
    out = json.loads(goals(action="totally_bogus_action"))
    assert "error" in out
    assert "available_actions" in out


def test_gates_when_flag_off_returns_not_available(monkeypatch):
    """With GATES_ENABLED unset, gates() short-circuits to not_available."""
    monkeypatch.delenv("GATES_ENABLED", raising=False)
    out = json.loads(gates(action="claim"))
    assert out["status"] == "not_available"


def test_gates_list_returns_discovery_payload(monkeypatch):
    monkeypatch.setenv("GATES_ENABLED", "1")

    out = json.loads(gates(action="list"))

    assert out["status"] == "ok"
    assert out["tool"] == "gates"
    assert "list" in out["available_actions"]
    assert "list_claims" in out["available_actions"]


def test_gates_enabled_default_off(monkeypatch):
    monkeypatch.delenv("GATES_ENABLED", raising=False)
    assert _gates_enabled() is False


def test_gates_enabled_truthy_values(monkeypatch):
    for v in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("GATES_ENABLED", v)
        assert _gates_enabled() is True, f"expected True for GATES_ENABLED={v!r}"


# ── universe_server.goals/gates wrapper sanity ──────────────────────────────


def test_universe_server_goals_and_gates_are_decorated_wrappers():
    """The us.goals + us.gates are NEW callables (decorated) wrapping
    market.goals + market.gates. They are NOT the same object as the
    market plain callables (because @mcp.tool wraps them)."""
    from tinyassets import universe_server as us
    # FastMCP wraps the function; just verify they're callable.
    assert callable(us.goals)
    assert callable(us.gates)


# Arc A re-export shims removed in Task #18 retarget sweep — the parametrized
# `test_universe_server_reexport_identity` covering 40+ market symbols is gone
# alongside the shim block.
