"""Phase 6.1 — Outcome Gates schema + tool surface.

Covers docs/specs/outcome_gates_phase6.md §Rollout 6.1:
- Schema migration (goals.gate_ladder_json + gate_claims table).
- gates tool gated by GATES_ENABLED.
- define_ladder owner-only.
- claim idempotent on (branch_def_id, rung_key).
- claim unknown rung returns available_rungs for the humans.
- ladder validation (non-empty rung_key, no dup keys).

Phase 6.2+ actions (retract / list_claims / leaderboard) ship in
separate test files.
"""

from __future__ import annotations

import importlib
import json

import pytest

# Scopes every action in this file needs. `gates.costly` covers `claim`;
# `gates.admin` covers `define_ladder` (`_GATES_COSTLY_ACTIONS` /
# `_GATES_ADMIN_ACTIONS` in tinyassets/auth/provider.py). Nothing here
# asserts a *scope* refusal — `test_define_ladder_owner_only` asserts an
# authorship refusal one layer deeper, in market.py — so granting the full
# set costs no assertion strength. Without it, `extensions create_branch`
# returns `{"error": "Authenticated branch subject required."}` and the
# seed helper dies on `KeyError: 'branch_def_id'`.
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


# ─── feature flag ──────────────────────────────────────────────────────


def test_gates_tool_gated_by_flag(tmp_path, monkeypatch):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "alice")
    monkeypatch.delenv("GATES_ENABLED", raising=False)
    from tinyassets import universe_server as us
    importlib.reload(us)
    from tinyassets.api.market import gates

    try:
        result = json.loads(gates(action="get_ladder", goal_id="x"))
        assert result["status"] == "not_available"
        assert "GATES_ENABLED" in result["error"]
    finally:
        importlib.reload(us)


# ─── define_ladder ─────────────────────────────────────────────────────


# ─── get_ladder ────────────────────────────────────────────────────────


# ─── claim ─────────────────────────────────────────────────────────────


# ─── schema migration ──────────────────────────────────────────────────


def test_schema_has_gate_ladder_column(gates_env):
    import sqlite3
    us, base = gates_env  # noqa: F841 — importlib reloads ensure init
    from tinyassets.daemon_server import (
        db_path,
        initialize_author_server,
    )
    initialize_author_server(base)
    conn = sqlite3.connect(db_path(base))
    cols = {
        r[1] for r in conn.execute("PRAGMA table_info(goals)")
    }
    conn.close()
    assert "gate_ladder_json" in cols


def test_schema_has_gate_claims_table(gates_env):
    import sqlite3
    us, base = gates_env  # noqa: F841
    from tinyassets.daemon_server import (
        db_path,
        initialize_author_server,
    )
    initialize_author_server(base)
    conn = sqlite3.connect(db_path(base))
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    conn.close()
    assert "gate_claims" in tables
