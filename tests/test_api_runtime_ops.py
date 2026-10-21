"""Task #13 — direct tests for `tinyassets.api.runtime_ops` after decomp Step 6.

The legacy test files (test_dry_inspect_node.py, test_project_memory.py,
test_teammate_message.py, test_scheduler*.py) import via
`tinyassets.universe_server` to cover chatbot-facing MCP wrappers. This file
exercises `tinyassets.api.runtime_ops` directly to lock in the canonical
implementation surface.
"""

from __future__ import annotations

import json

from tinyassets.api.runtime_ops import (
    _PROJECT_MEMORY_ACTIONS,
    _PROJECT_MEMORY_WRITE_ACTIONS,
    _action_dry_inspect_node,
    _action_dry_inspect_patch,
    _action_messaging_send,
    _action_project_memory_get,
    _action_project_memory_set,
    _load_branch_for_inspect,
)

# ── module surface ──────────────────────────────────────────────────────────




# ── _PROJECT_MEMORY_ACTIONS dispatch table ──────────────────────────────────




def test_project_memory_write_actions_only_set():
    """Only project_memory_set is a write action."""
    assert _PROJECT_MEMORY_WRITE_ACTIONS == frozenset({"project_memory_set"})


def test_project_memory_write_actions_subset_of_actions():
    assert _PROJECT_MEMORY_WRITE_ACTIONS <= set(_PROJECT_MEMORY_ACTIONS.keys())


# ── _INSPECT_DRY_ACTIONS dispatch table ─────────────────────────────────────




# ── _MESSAGING_ACTIONS dispatch table ───────────────────────────────────────




# ── _SCHEDULER_ACTIONS dispatch table ───────────────────────────────────────




# ── all dispatch table handlers callable ────────────────────────────────────




# ── helper unit tests ───────────────────────────────────────────────────────


def test_load_branch_for_inspect_with_neither_arg_returns_error_str():
    branch, err = _load_branch_for_inspect("", "")
    assert branch is None
    assert "branch_def_id or branch_spec_json is required" in err


def test_load_branch_for_inspect_invalid_json_returns_error_str():
    branch, err = _load_branch_for_inspect("", "{not valid json")
    assert branch is None
    assert "not valid JSON" in err




# ── handler error paths (no monkeypatch) ────────────────────────────────────


def test_action_project_memory_get_missing_args_returns_error():
    out = json.loads(_action_project_memory_get({}))
    assert "error" in out


def test_action_project_memory_set_missing_args_returns_error():
    out = json.loads(_action_project_memory_set({}))
    assert "error" in out


def test_action_dry_inspect_node_missing_branch_id_returns_error_dict():
    out = json.loads(_action_dry_inspect_node({}))
    assert "error" in out


def test_action_dry_inspect_patch_missing_args_returns_error_dict():
    out = json.loads(_action_dry_inspect_patch({}))
    assert "error" in out


def test_action_messaging_send_missing_args_returns_error_dict():
    out = json.loads(_action_messaging_send({}))
    assert "error" in out


# Arc A re-export shims removed in Task #18 retarget sweep — the dispatch-table
# identity tests are gone alongside the shim block.
