"""Task #11 — direct tests for `tinyassets.api.runs` after decomp Step 4.

The legacy test files (test_run_branch_failure_taxonomy.py,
test_query_runs.py, test_run_branch_version.py, test_canonical_branch_mcp.py)
still cover the chatbot-facing `tinyassets.universe_server` MCP wrappers. This
file exercises `tinyassets.api.runs` directly to lock in the canonical
implementation surface.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tinyassets.api import runs as runs_mod
from tinyassets.api.runs import (
    _FAILURE_TAXONOMY,
    _RUN_ACTIONS,
    _RUN_WRITE_ACTIONS,
    _action_run_branch,
    _actionable_by,
    _build_failure_taxonomy,
    _classify_run_error,
    _classify_run_outcome_error,
    _compose_run_snapshot,
    _ensure_runs_recovery,
    _failure_payload,
)

# ── module surface ──────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _pin_data_dir(tmp_path, monkeypatch):
    """The run handlers sweep in-flight runs in the data dir on first use:
    never the developer's real one."""
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


def test_module_exposes_expected_public_names():
    """The new submodule's contract surface — guards against silent removal."""
    expected = {
        "_RUN_ACTIONS", "_RUN_WRITE_ACTIONS", "_dispatch_run_action",
        "_action_run_branch", "_action_get_run", "_action_list_runs",
        "_action_stream_run", "_action_wait_for_run", "_action_cancel_run",
        "_action_get_run_output", "_action_attach_existing_child_run",
        "_action_resume_run",
        "_action_estimate_run_cost", "_action_query_runs",
        "_action_run_routing_evidence", "_action_get_memory_scope_status",
        "_action_run_branch_version", "_action_rollback_merge",
        "_action_get_rollback_history",
        "_classify_run_error", "_classify_run_outcome_error",
        "_actionable_by", "_failure_payload",
        "_ensure_runs_recovery", "_build_failure_taxonomy",
        "_FAILURE_TAXONOMY",
        "_run_mermaid_from_events", "_branch_name_for_run",
        "_compose_run_snapshot",
    }
    missing = expected - set(dir(runs_mod))
    assert not missing, f"runs.py is missing public names: {missing}"


# ── _RUN_ACTIONS dispatch table ─────────────────────────────────────────────


def test_run_actions_table_keys_are_expected_set():
    expected = {
        "run_branch", "run_branch_version", "get_run", "list_runs",
        "stream_run", "wait_for_run", "cancel_run", "get_run_output",
        "attach_existing_child_run", "resume_run", "estimate_run_cost", "query_runs",
        "record_run_receipt", "list_run_receipts",
        "get_routing_evidence", "get_memory_scope_status",
        "rollback_merge", "get_rollback_history",
        # Inbound channel-agnostic ops (webhook + source management via run dispatch)
        "mint_webhook", "revoke_webhook", "list_webhooks",
        "create_source", "revoke_source", "list_sources",
        # Structured cross-owner delivery management and safe receipt reads.
        "create_receiver", "update_receiver", "revoke_receiver",
        "connect_output", "disconnect_output", "deliver_output",
        "inspect_receiver", "list_output_links", "get_delivery",
        # discover_receivers: any authenticated user searching the receivers whose
        # owners marked them discoverable. A read, so it stays out of the write set.
        "discover_receivers",
    }
    assert set(_RUN_ACTIONS.keys()) == expected


def test_all_run_actions_are_callable():
    for action, handler in _RUN_ACTIONS.items():
        assert callable(handler), f"{action} handler is not callable"


def test_run_write_actions_is_subset_of_run_actions():
    assert _RUN_WRITE_ACTIONS <= set(_RUN_ACTIONS.keys())


def test_run_write_actions_includes_state_mutators():
    """State-mutating actions must be in the write-set so the ledger captures them."""
    assert "run_branch" in _RUN_WRITE_ACTIONS
    assert "cancel_run" in _RUN_WRITE_ACTIONS
    assert "resume_run" in _RUN_WRITE_ACTIONS
    assert "rollback_merge" in _RUN_WRITE_ACTIONS
    assert "run_branch_version" in _RUN_WRITE_ACTIONS
    assert "attach_existing_child_run" in _RUN_WRITE_ACTIONS


def test_run_write_actions_excludes_read_actions():
    """Read actions stay out of the ledger to avoid log spam."""
    for read_action in (
        "get_run", "list_runs", "stream_run", "wait_for_run",
        "get_run_output", "estimate_run_cost", "query_runs",
        "get_routing_evidence", "get_memory_scope_status",
        "get_rollback_history",
    ):
        assert read_action not in _RUN_WRITE_ACTIONS


# ── failure taxonomy ────────────────────────────────────────────────────────


def test_build_failure_taxonomy_returns_list_of_triples():
    """Each entry is (exc_type, failure_class_str, suggested_action_str)."""
    table = _build_failure_taxonomy()
    assert table, "taxonomy is empty"
    for entry in table:
        assert len(entry) == 3
        exc_type, failure_class, suggested_action = entry
        assert isinstance(exc_type, type)
        assert isinstance(failure_class, str)
        assert isinstance(suggested_action, str)


def test_build_failure_taxonomy_returns_equal_content_across_calls():
    """Multiple calls produce equal-content taxonomies (deterministic build)."""
    a = _build_failure_taxonomy()
    b = _build_failure_taxonomy()
    assert a == b


def test_failure_taxonomy_module_state_is_list():
    """Module-level _FAILURE_TAXONOMY is a list (legacy state guard slot)."""
    assert isinstance(_FAILURE_TAXONOMY, list)


def test_classify_run_error_unknown_falls_back_to_unknown_class():
    """A bare Exception not in the taxonomy returns the unknown-failure shape."""
    out = _classify_run_error(Exception("totally unique nonsense xyzzy"), "b1")
    assert out["status"] == "error"
    assert out["failure_class"] == "unknown"
    assert "actionable_by" in out
    assert "suggested_action" in out


def test_classify_run_error_returns_dict_shape():
    out = _classify_run_error(RuntimeError("x"), "b1")
    assert isinstance(out, dict)
    assert {"status", "failure_class", "actionable_by",
            "suggested_action"} <= set(out.keys())


def test_classify_run_outcome_error_returns_none_when_no_match():
    """An unrecognized outcome.error string returns None."""
    assert _classify_run_outcome_error("totally unknown error string xyzzy") is None


def test_classify_run_outcome_error_returns_none_for_empty():
    assert _classify_run_outcome_error("") is None


def test_actionable_by_returns_string():
    """Any failure_class — known or unknown — yields a string actor."""
    for cls in ("unknown", "empty_llm_response", "recursion_limit",
                "totally_unknown_class_xyzzy"):
        result = _actionable_by(cls)
        assert isinstance(result, str)
        assert result  # non-empty


def test_failure_payload_shape():
    """Direct unit on _failure_payload — used by _classify_run_error."""
    out = _failure_payload(RuntimeError("oops"), "test_class", "do something")
    assert out["status"] == "error"
    assert out["failure_class"] == "test_class"
    assert out["suggested_action"] == "do something"
    assert "actionable_by" in out
    assert "oops" in out["error"]
    assert out["error"].startswith("Run failed:")


# ── _ensure_runs_recovery idempotency ───────────────────────────────────────


def test_ensure_runs_recovery_is_idempotent(tmp_path, monkeypatch):
    """Multiple calls don't re-run the recovery sweep. Pinned to tmp_path: the
    sweep takes a lock and rewrites run rows in whatever data dir it is given."""
    import os

    from tinyassets.api import runs as api_runs

    monkeypatch.setattr(api_runs, "_RUNS_RECOVERY_DONE", False)
    monkeypatch.setattr(api_runs, "_RUNS_RECOVERY_LOCK", None)
    monkeypatch.setattr(api_runs, "_base_path", lambda: tmp_path)
    _ensure_runs_recovery()
    held = api_runs._RUNS_RECOVERY_LOCK
    _ensure_runs_recovery()  # second call must not raise
    assert api_runs._RUNS_RECOVERY_LOCK is held
    if held is not None and held.fd is not None:
        os.close(held.fd)


# ── handler error path (no-monkeypatch path) ────────────────────────────────


def test_action_run_branch_missing_branch_def_id_returns_error():
    """The dispatch entry returns a JSON error when branch_def_id is empty."""
    out = json.loads(_action_run_branch({}))
    assert "error" in out
    assert "branch_def_id" in out["error"]


def test_action_run_branch_returns_str():
    out = _action_run_branch({})
    assert isinstance(out, str)


def test_action_run_branch_guidance_uses_advertised_handles(monkeypatch):
    class RunnableBranch:
        version = 1

        @staticmethod
        def validate():
            return []

        @staticmethod
        def to_dict():
            # run_branch asks every branch for its declared inputs before it
            # picks a lane. A double that cannot answer is not a branch this
            # server would ever hold; this one declares no file input, so the
            # scalar lane under test is the one taken.
            return {"branch_def_id": "b-guidance"}

    monkeypatch.setattr(runs_mod, "_ensure_runs_recovery", lambda: None)
    monkeypatch.setattr(
        "tinyassets.api.branches._resolve_branch_id",
        lambda branch_id, _base_path: branch_id,
    )
    monkeypatch.setattr(
        "tinyassets.daemon_server.get_branch_definition",
        # `visibility: public` stated: an absent field now reads as PRIVATE, and
        # this double's subject is the guidance text, not the read gate.
        lambda _base_path, *, branch_def_id: {
            "branch_def_id": branch_def_id, "visibility": "public",
        },
    )
    monkeypatch.setattr(
        "tinyassets.branches.BranchDefinition.from_dict",
        lambda _source: RunnableBranch(),
    )
    monkeypatch.setattr(
        "tinyassets.runs.execute_branch_async",
        lambda *_args, **_kwargs: SimpleNamespace(
            run_id="r-guidance",
            status="queued",
            output={},
            error="",
        ),
    )

    payload = json.loads(_action_run_branch({"branch_def_id": "b-guidance"}))
    text = payload["text"]
    assert 'read_graph target="run"' in text
    assert "wait_for_run" not in text
    assert "get_run" not in text
    assert "cancel_run" not in text


# Arc A re-export shims removed in Task #18 retarget sweep — the
# `test_universe_server_reexports_run_actions` + parametrized identity tests
# are gone alongside the shim block.


def test_compile_failure_records_actionable_run_error(tmp_path, monkeypatch):
    """Compile-time failures must not collapse into background-worker crash."""
    from tinyassets.branches import (
        BranchDefinition,
        EdgeDefinition,
        GraphNodeRef,
        NodeDefinition,
    )
    from tinyassets.runs import (
        execute_branch_async,
        get_run,
        shutdown_executor,
        wait_for,
    )

    def boom(*_args, **_kwargs):
        raise ValueError("'source_manifest' is already being used as a state key")

    monkeypatch.setattr("tinyassets.runs.compile_branch", boom)

    branch = BranchDefinition(name="Compile failure", entry_point="node_a")
    branch.node_defs = [
        NodeDefinition(
            node_id="node_a",
            display_name="Node A",
            prompt_template="hello",
            output_keys=["source_manifest"],
        )
    ]
    branch.graph_nodes = [
        GraphNodeRef(id="node_a", node_def_id="node_a", position=0)
    ]
    branch.edges = [
        EdgeDefinition(from_node="START", to_node="node_a"),
        EdgeDefinition(from_node="node_a", to_node="END"),
    ]
    branch.state_schema = [{"name": "source_manifest", "type": "str"}]

    # A run records who asked for it. There is no default principal any more,
    # so the caller names one (founder, 2026-09-02).
    outcome = execute_branch_async(
        tmp_path, branch=branch, inputs={}, actor="universe:u-test",
    )
    try:
        wait_for(outcome.run_id, timeout=10.0)
        run = get_run(tmp_path, outcome.run_id)
    finally:
        shutdown_executor()

    assert run is not None
    assert run["status"] == "failed"
    assert run["error"].startswith("Compile failed: ValueError:")
    assert "already being used as a state key" in run["error"]
    assert "Background worker crashed" not in run["error"]


def test_compile_failed_outcome_is_classified():
    out = _classify_run_outcome_error(
        "Compile failed: ValueError: 'x' is already being used as a state key"
    )
    assert out is not None
    assert out[0] == "compile_error"


def test_provider_exhausted_outcome_is_classified():
    out = _classify_run_outcome_error(
        "Provider call failed in node 'make_source_manifest': "
        "All providers exhausted for role=writer. Daemon should retry with backoff."
    )
    assert out is not None
    assert out[0] == "provider_exhausted"


def test_failed_run_snapshot_marks_last_running_node_failed(monkeypatch):
    def _missing_branch(*_args, **_kwargs):
        raise KeyError("missing-branch")

    monkeypatch.setattr("tinyassets.daemon_server.get_branch_definition", _missing_branch)
    monkeypatch.setattr(
        runs_mod,
        "_run_mermaid_from_events",
        lambda _branch_def_id, _node_statuses: "```mermaid\nflowchart LR\n```",
    )

    snapshot = _compose_run_snapshot(
        {
            "run_id": "run-1",
            "branch_def_id": "missing-branch",
            "status": "failed",
            "actor": "tester",
            "last_node_id": "step1",
            "error": "Provider call failed in node 'step1': boom",
        },
        [{"node_id": "step1", "status": "running"}],
    )

    by_id = {node["node_id"]: node["status"] for node in snapshot["node_statuses"]}
    assert by_id["step1"] == "failed"
    assert "- step1: failed" in snapshot["summary"]


def test_failed_run_snapshot_surfaces_provider_chain_from_failed_event(monkeypatch):
    provider_chain = {
        "role": "writer",
        "chain": ["claude-code", "codex", "ollama-local"],
        "attempts": [
            {
                "provider": "claude-code",
                "status": "skipped",
                "skip_class": "not_in_registry",
            },
            {
                "provider": "codex",
                "status": "failed",
                "skip_class": "auth_invalid",
                "detail": "token expired",
            },
        ],
    }

    def _science_branch(*_args, **_kwargs):
        return {
            "branch_def_id": "b1",
            "name": "Science Branch",
            "description": "",
            "author": "tester",
            "domain_id": "workflow",
            "goal_id": "goal-1",
            "tags": [],
            "version": 1,
            "parent_def_id": "",
            "fork_from": None,
            "graph_nodes": [{"id": "step1", "node_def_id": "step1", "position": 0}],
            "edges": [],
            "conditional_edges": [],
            "entry_point": "step1",
            "node_defs": [],
            "state_schema": [],
            "published": False,
            "visibility": "public",
            "created_at": "",
            "updated_at": "",
            "stats": {},
            "default_llm_policy": None,
            "concurrency_budget": None,
        }

    monkeypatch.setattr(
        "tinyassets.daemon_server.get_branch_definition",
        _science_branch,
    )
    monkeypatch.setattr(
        runs_mod,
        "_run_mermaid_from_events",
        lambda _branch_def_id, _node_statuses: "```mermaid\nflowchart LR\n```",
    )

    snapshot = _compose_run_snapshot(
        {
            "run_id": "run-1",
            "branch_def_id": "b1",
            "status": "failed",
            "actor": "tester",
            "last_node_id": "step1",
            "error": (
                "CompilerError: Provider call failed in node 'step1': "
                "All providers exhausted for role=writer."
            ),
        },
        [{
            "node_id": "step1",
            "status": "failed",
            "detail": {"provider_chain": provider_chain},
        }],
    )

    assert snapshot["failure_class"] == "provider_exhausted"
    assert snapshot["error_detail"]["provider_chain"] == provider_chain


def test_failed_run_snapshot_surfaces_provider_chain_from_error_suffix(monkeypatch):
    provider_chain = {
        "role": "writer",
        "chain": ["codex"],
        "attempts": [
            {"provider": "codex", "status": "failed", "skip_class": "auth_invalid"}
        ],
    }
    error = (
        "CompilerError: Provider call failed in node 'step1': "
        "All providers exhausted for role=writer. "
        f"[chain_state]: {json.dumps(provider_chain, separators=(',', ':'))}"
    )

    def _missing_branch(*_args, **_kwargs):
        raise KeyError("b1")

    monkeypatch.setattr(
        "tinyassets.daemon_server.get_branch_definition",
        _missing_branch,
    )
    monkeypatch.setattr(
        runs_mod,
        "_run_mermaid_from_events",
        lambda _branch_def_id, _node_statuses: "```mermaid\nflowchart LR\n```",
    )

    snapshot = _compose_run_snapshot(
        {
            "run_id": "run-1",
            "branch_def_id": "b1",
            "status": "failed",
            "actor": "tester",
            "last_node_id": "step1",
            "error": error,
        },
        [],
    )

    assert snapshot["error_detail"]["provider_chain"] == provider_chain
