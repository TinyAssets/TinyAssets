"""TINYASSETS_UNIFIED_EXECUTION unified-execution tests.

Covers phase_d_preflight.md (archived in git) §4.4:
- Compiler-extension tests (5): opaque-node resolution + error modes.
- Wrapper-registration tests (2).
- Flag-gated dispatch tests (3).
- R11 compile-failure hard-fail (1).
- Pause/stop latency tests (2, §4.3 invariant 3 + §4.10).
- Producer no-double-register test (1, §4.3 invariant 4).
- State-field boundary test (1, §4.3 invariant 5, wrapped-path only).
- Parity tests (4 × 2 flag states = 8).
- Regression safety (2).

Pause/stop and checkpoint regressions under flag-on are accepted for
v1 per §4.10 and §4.11. Tests assert wrapper-boundary granularity,
not per-inner-phase parity.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

# ───────────────────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def clean_registry():
    """Reset the domain registry between tests so monkey-patches
    don't leak across test boundaries.
    """
    from tinyassets import domain_registry
    saved = dict(domain_registry._REGISTRY)
    yield domain_registry
    domain_registry._REGISTRY.clear()
    domain_registry._REGISTRY.update(saved)


@pytest.fixture
def fresh_registrations(clean_registry):
    """Guarantee fantasy_daemon.branch_registrations has registered
    before the test runs, even if earlier tests cleared the registry.
    """
    import fantasy_daemon.branch_registrations  # noqa: F401

    # Re-registration is idempotent.
    from fantasy_daemon.branch_registrations import universe_cycle_wrapper
    clean_registry.register_domain_callable(
        "fantasy_author", "universe_cycle_wrapper", universe_cycle_wrapper,
    )
    return clean_registry


def _minimal_branch(
    *,
    domain_id: str = "workflow",
    node_body: dict | None = None,
    state_schema: list | None = None,
):
    """Build a tiny BranchDefinition for compiler tests.

    ``state_schema`` defaults to a couple of fields so the
    synthesized TypedDict lets callers pass input dicts through the
    StateGraph filter.
    """
    from tinyassets.branches import (
        BranchDefinition,
        EdgeDefinition,
        GraphNodeRef,
        NodeDefinition,
    )
    node_kwargs = {
        "node_id": "x",
        "display_name": "X",
        "description": "",
        "phase": "custom",
        "input_keys": [],
        "output_keys": [],
    }
    if node_body:
        node_kwargs.update(node_body)
    # PR #1349 fail-closed gate: a genuine in-process approval must record the
    # source hash. Route ``approved: True`` through mark_approved() so the
    # fixture binds the hash to the source actually being approved, instead of
    # a bare flag the runtime now refuses.
    want_approved = bool(node_kwargs.pop("approved", False))
    node = NodeDefinition(**node_kwargs)
    if want_approved:
        node.mark_approved()
    return BranchDefinition(
        name="T",
        domain_id=domain_id,
        node_defs=[node],
        graph_nodes=[GraphNodeRef(id="x", node_def_id="x")],
        edges=[EdgeDefinition(from_node="x", to_node="END")],
        entry_point="x",
        state_schema=state_schema or [
            {"name": "foo", "type": "str"},
            {"name": "name", "type": "str"},
            {"name": "seen", "type": "bool"},
            {"name": "ok", "type": "bool"},
        ],
    )


# ───────────────────────────────────────────────────────────────────────
# Compiler-extension tests (§4.4: 5 tests)
# ───────────────────────────────────────────────────────────────────────


def test_opaque_node_resolves_via_registry(clean_registry):
    """A body-less node with a registered (domain_id, node_id) is
    compiled as an opaque node that invokes the registered callable.
    """
    called: list[dict] = []

    def fake(state: dict) -> dict:
        called.append(state)
        return {"seen": True}

    clean_registry.register_domain_callable("testdom", "x", fake)
    from tinyassets.graph_compiler import compile_branch

    branch = _minimal_branch(domain_id="testdom")
    compiled = compile_branch(branch)
    runner = compiled.graph.compile()
    result = runner.invoke({"foo": "bar"})
    assert called and called[0].get("foo") == "bar"
    assert result.get("seen") is True


def test_source_code_node_run_sees_top_level_helpers(clean_registry):
    """BUG-112 regression: a source_code node's run() must see sibling
    top-level helper functions. Split globals/locals put the defs in
    locals while run.__globals__ stayed the globals dict, so calling a
    helper raised NameError (the live `name '_text' is not defined`).
    A single exec namespace restores module-level visibility.
    """
    from tinyassets.graph_compiler import compile_branch

    src = (
        "def _text(value):\n"
        "    return f'helper:{value}'\n"
        "\n"
        "def run(state):\n"
        "    return {'foo': _text(state.get('name', 'n'))}\n"
    )
    branch = _minimal_branch(
        node_body={"source_code": src, "approved": True, "input_keys": ["name"]},
    )
    compiled = compile_branch(branch)
    runner = compiled.graph.compile()
    result = runner.invoke({"name": "Z"})
    assert result.get("foo") == "helper:Z"


def test_unregistered_opaque_node_raises_compiler_error(clean_registry):
    """A body-less node whose (domain_id, node_id) is not in the
    registry raises CompilerError at compile time, not at runtime.
    """
    from tinyassets.graph_compiler import CompilerError, compile_branch

    branch = _minimal_branch(domain_id="testdom")  # nothing registered
    with pytest.raises(CompilerError) as exc_info:
        compile_branch(branch)
    assert "prompt_template" in str(exc_info.value)


def test_opaque_node_bypasses_source_code_validation(clean_registry):
    """Opaque-node path never touches _validate_source_code — the
    per-node `approved` flag is irrelevant. Domain registry is the
    trust boundary.
    """
    from tinyassets.graph_compiler import compile_branch

    clean_registry.register_domain_callable(
        "testdom", "x", lambda s: {"ok": True},
    )
    branch = _minimal_branch(
        domain_id="testdom",
        node_body={"approved": False},  # would fail source_code gate
    )
    compiled = compile_branch(branch)
    runner = compiled.graph.compile()
    result = runner.invoke({})
    assert result.get("ok") is True


def test_empty_domain_id_raises_on_bodyless_node():
    """Empty domain_id + no template + no source = malformed Branch.
    Raises CompilerError, not a silent pass-through.
    """
    from tinyassets.graph_compiler import CompilerError, compile_branch

    branch = _minimal_branch(domain_id="")  # no domain, no body
    with pytest.raises(CompilerError):
        compile_branch(branch)


def test_template_path_unchanged_by_domain_id_thread(clean_registry, connected_fantasy_model):
    """Passing domain_id doesn't affect prompt_template dispatch —
    template nodes still compile normally regardless.
    """
    from tinyassets.graph_compiler import compile_branch

    branch = _minimal_branch(
        domain_id="testdom",
        node_body={"prompt_template": "Hello {name}"},
    )
    compiled = compile_branch(branch, provider_call=connected_fantasy_model)
    runner = compiled.graph.compile()
    result = runner.invoke({"name": "World"})
    assert isinstance(result, dict)
    assert connected_fantasy_model.receipts[-1].reported_model == "deterministic-v1"
    assert connected_fantasy_model.receipts[-1].outcome == "succeeded"


# ───────────────────────────────────────────────────────────────────────
# Wrapper-registration tests (§4.4: 2 tests)
# ───────────────────────────────────────────────────────────────────────


def test_universe_cycle_wrapper_is_registered(fresh_registrations):
    """fantasy_daemon.branch_registrations module import registers
    the wrapper at ("fantasy_author", "universe_cycle_wrapper").
    """
    fn = fresh_registrations.resolve_domain_callable(
        "fantasy_author", "universe_cycle_wrapper",
    )
    assert fn is not None
    assert callable(fn)


def test_wrapper_returns_boundary_fields(fresh_registrations, monkeypatch):
    """The wrapper callable returns only the boundary fields from the
    inner graph's final state — not the full UniverseState.
    """
    # Monkey-patch build_universe_graph to a toy one-node graph so we
    # don't invoke the full fantasy phases.
    from langgraph.graph import END, StateGraph
    from typing_extensions import TypedDict

    class _ToyState(TypedDict, total=False):
        total_words: int
        total_chapters: int
        health: dict
        premise_kernel: str
        workflow_instructions: dict  # mid-cycle — must NOT leak out

    def _toy_step(state):
        return {
            "total_words": 42,
            "total_chapters": 1,
            "health": {"stopped": True},
            "workflow_instructions": {"mid_cycle_only": True},
        }

    def fake_build():
        g = StateGraph(_ToyState)
        g.add_node("step", _toy_step)
        g.set_entry_point("step")
        g.add_edge("step", END)
        return g

    import fantasy_daemon.branch_registrations as br
    import fantasy_daemon.graphs.universe as uni
    monkeypatch.setattr(uni, "build_universe_graph", fake_build)

    out = br.universe_cycle_wrapper({
        "universe_id": "u1",
        "universe_path": "/tmp/u1",
        "premise_kernel": "test premise",
    })
    assert out.get("total_words") == 42
    assert out.get("total_chapters") == 1
    # Mid-cycle fields must NOT surface at the boundary.
    assert "workflow_instructions" not in out


# ───────────────────────────────────────────────────────────────────────
# Flag-gated dispatch tests (§4.4: 3 tests)
# ───────────────────────────────────────────────────────────────────────


def test_flag_off_uses_direct_graph(monkeypatch):
    """Flag off → `_run_graph` constructs graph via
    `build_universe_graph()` directly.
    """
    monkeypatch.delenv("TINYASSETS_UNIFIED_EXECUTION", raising=False)
    from fantasy_daemon.__main__ import _workflow_unified_execution_enabled
    assert _workflow_unified_execution_enabled() is False


def test_flag_on_uses_compile_branch(monkeypatch, fresh_registrations):
    """Flag on → `_build_unified_graph_builder` loads the seed YAML
    and calls `compile_branch`. Returns a StateGraph, same shape the
    direct path returns.
    """
    monkeypatch.setenv("TINYASSETS_UNIFIED_EXECUTION", "1")
    from langgraph.graph import StateGraph

    from fantasy_daemon.__main__ import (
        _build_unified_graph_builder,
        _workflow_unified_execution_enabled,
    )

    assert _workflow_unified_execution_enabled() is True
    graph = _build_unified_graph_builder()
    assert isinstance(graph, StateGraph)


@pytest.mark.parametrize(
    "flag_value,expected",
    [
        ("1", True),
        ("true", True),
        ("True", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("", False),
        ("no", False),
        ("off", False),
    ],
)
def test_flag_parsing_accepts_common_truthy_spellings(
    monkeypatch, flag_value, expected,
):
    """Flag parser matches `_gates_enabled()` pattern from
    `tinyassets/universe_server.py`.
    """
    monkeypatch.setenv("TINYASSETS_UNIFIED_EXECUTION", flag_value)
    from fantasy_daemon.__main__ import _workflow_unified_execution_enabled
    assert _workflow_unified_execution_enabled() is expected


# ───────────────────────────────────────────────────────────────────────
# R11 compile-failure hard-fail (§4.4: 1 test)
# ───────────────────────────────────────────────────────────────────────


def test_flag_on_compile_failure_raises_no_silent_fallthrough(
    monkeypatch, clean_registry,
):
    """If the domain registry is missing the wrapper callable under
    flag-on, `_build_unified_graph_builder` must raise — NOT silently
    fall through to the direct path. Preflight §4.8 R11.
    """
    monkeypatch.setenv("TINYASSETS_UNIFIED_EXECUTION", "1")
    # Clear and intentionally do NOT register the wrapper.
    clean_registry.clear_registry()
    from fantasy_daemon.__main__ import _build_unified_graph_builder
    from tinyassets.graph_compiler import CompilerError

    with pytest.raises(CompilerError):
        _build_unified_graph_builder()


# ───────────────────────────────────────────────────────────────────────
# Pause/stop latency tests (§4.3 invariant 3, §4.10)
# ───────────────────────────────────────────────────────────────────────


def test_stop_event_observed_at_wrapper_boundary(fresh_registrations):
    """Under flag-on, the outer stream loop checks _stop_event once
    per wrapper invocation (not per inner phase). A wrapper that
    completes and returns gives the outer loop a chance to observe
    stop; that's the only guarantee.
    """
    # Simulate: the wrapper executes in one step (returning a dict
    # with boundary fields), and the outer stream loop thereafter
    # checks the stop event. We don't invoke the real daemon loop
    # here; instead we assert the wrapper is a single-invocation
    # callable that returns a dict (wrapper-boundary granularity).
    wrapper = fresh_registrations.resolve_domain_callable(
        "fantasy_author", "universe_cycle_wrapper",
    )
    assert callable(wrapper)
    # The wrapper's contract is `state -> dict` — a single function
    # call, not a generator. Outer loop sees one event per call.


def test_pause_file_halts_outer_stream_loop_both_flags():
    """The `.pause` file check is in the outer stream loop in
    `_run_graph`; it halts under both flag states. Preflight §4.3
    invariant 3.
    """
    # Static assertion: `_run_graph` source contains the pause-file
    # check, and neither branch of the flag gate short-circuits it.
    source = Path(
        "fantasy_daemon/__main__.py",
    ).read_text(encoding="utf-8")
    assert ".pause" in source
    # The check sits inside `_run_graph` after compile, before the
    # flag-guarded build runs. Both paths enter the same stream loop.
    assert "_build_unified_graph_builder" in source
    assert "build_universe_graph()" in source


# ───────────────────────────────────────────────────────────────────────
# Producer no-double-register (§4.3 invariant 4)
# ───────────────────────────────────────────────────────────────────────


def test_producer_registry_no_double_registration(fresh_registrations):
    """After the wrapped graph is built, the domain registry has one
    entry per (domain_id, node_id) key — no duplicates — counted by
    object id so identical re-registrations still show as one slot.
    """
    import fantasy_daemon.branch_registrations  # re-import is idempotent
    from tinyassets import domain_registry as dr

    # Simulate two imports (re-registration).
    importlib.reload(fantasy_daemon.branch_registrations)
    registry = dr._REGISTRY
    # Distinct object ids by entry (value). The INVARIANT as written
    # in preflight §4.3 #4 says: len({id(p) for p in registry}) ==
    # len(registry). For a dict, "registry" iterates keys, so use
    # .values() to count callables.
    entries = list(registry.values())
    assert len({id(p) for p in entries}) == len(entries)


# ───────────────────────────────────────────────────────────────────────
# State-field boundary (§4.3 invariant 5, wrapped-path only)
# ───────────────────────────────────────────────────────────────────────


def test_boundary_state_round_trip(fresh_registrations, monkeypatch):
    """Wrapper-path only: drive one inner cycle with a toy inner
    graph, assert boundary-returned state carries counters while
    mid-cycle state (e.g. workflow_instructions) is filtered out.
    Preflight §4.3 invariant 5.
    """
    from langgraph.graph import END, StateGraph
    from typing_extensions import TypedDict

    class _InnerState(TypedDict, total=False):
        total_words: int
        total_chapters: int
        workflow_instructions: dict
        selected_target_id: str
        health: dict

    def _inner_dispatch(state):
        return {
            "workflow_instructions": {
                **(state.get("workflow_instructions") or {}),
                "selected_target_id": "tgt-inner",
            },
            "selected_target_id": "tgt-inner",
        }

    def _inner_run(state):
        # Mid-cycle state is readable by the next node INSIDE the
        # wrapped graph — demonstrates the first half of invariant 5.
        assert state.get("selected_target_id") == "tgt-inner"
        return {
            "total_words": 100,
            "total_chapters": 1,
            "health": {"stopped": True},
        }

    def fake_build():
        g = StateGraph(_InnerState)
        g.add_node("dispatch", _inner_dispatch)
        g.add_node("run", _inner_run)
        g.set_entry_point("dispatch")
        g.add_edge("dispatch", "run")
        g.add_edge("run", END)
        return g

    import fantasy_daemon.branch_registrations as br
    import fantasy_daemon.graphs.universe as uni
    monkeypatch.setattr(uni, "build_universe_graph", fake_build)

    out = br.universe_cycle_wrapper({
        "universe_id": "u1", "universe_path": "/tmp/u1",
        "premise_kernel": "p",
    })
    # Boundary state contains counters.
    assert out.get("total_words") == 100
    assert out.get("total_chapters") == 1
    # Mid-cycle state does NOT leak to the boundary.
    assert "workflow_instructions" not in out
    assert "selected_target_id" not in out


def test_worldbuild_soft_stop_clears_when_branch_task_queue_has_work(tmp_path):
    """BUG-055: a checkpointed ``worldbuild_stuck`` stop must not keep
    exiting when the durable BranchTask queue has pickable work.
    """
    from fantasy_daemon.branch_registrations import clear_restartable_soft_stop
    from tinyassets.branch_tasks import BranchTask, append_task

    append_task(
        tmp_path,
        BranchTask(
            branch_task_id="bt-1",
            branch_def_id="fantasy_author:universe_cycle_wrapper",
            universe_id="u1",
            status="pending",
            trigger_source="owner_queued",
        ),
    )

    repaired = clear_restartable_soft_stop({
        "universe_id": "u1",
        "universe_path": str(tmp_path),
        "health": {
            "stopped": True,
            "idle_reason": "worldbuild_stuck",
            "worldbuild_noop_streak": 3,
            "cycle_noop_streak": 0,
        },
    })

    assert repaired["health"]["stopped"] is False
    assert repaired["health"]["idle_reason"] == ""
    assert repaired["health"]["worldbuild_noop_streak"] == 0


def test_epoch2_probe_failure_does_not_mask_pending_legacy_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    import sqlite3

    import fantasy_daemon.branch_registrations as br

    (tmp_path / "requests.json").write_text(
        json.dumps([{"status": "pending"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        br,
        "_live_epoch2_claim_exists",
        lambda _universe_path: (_ for _ in ()).throw(
            sqlite3.OperationalError("database locked")
        ),
    )

    repaired = br.clear_restartable_soft_stop(
        {
            "universe_id": "u1",
            "universe_path": str(tmp_path),
            "health": {
                "stopped": True,
                "idle_reason": "worldbuild_stuck",
                "worldbuild_noop_streak": 3,
                "cycle_noop_streak": 0,
            },
        }
    )

    assert repaired["health"]["stopped"] is False


def test_legacy_probe_failure_does_not_mask_live_epoch2_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fantasy_daemon.branch_registrations as br

    monkeypatch.setattr(
        "tinyassets.work_targets.sync_source_synthesis_priorities",
        lambda _universe_path: (_ for _ in ()).throw(
            OSError("legacy registry unavailable")
        ),
    )
    monkeypatch.setattr(
        br,
        "_live_epoch2_claim_exists",
        lambda _universe_path: True,
    )

    assert br._restartable_work_exists(tmp_path) is True


def test_epoch2_restart_probe_rejects_same_basename_outside_data_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fantasy_daemon.branch_registrations as br

    data_root = tmp_path / "data"
    outside_universe = tmp_path / "outside" / "test-universe"
    data_root.mkdir()
    outside_universe.mkdir(parents=True)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(data_root))
    monkeypatch.setenv("TINYASSETS_WORKER_ID", "worker-a")
    monkeypatch.setattr(
        "tinyassets.branch_tasks_v2.EPOCH2_QUEUE_CONSUMER_READY",
        True,
    )
    monkeypatch.setattr(
        "tinyassets.branch_tasks_v2.Epoch2BranchTaskAdapter",
        lambda _base: (_ for _ in ()).throw(
            AssertionError("outside-root lookup reached canonical adapter")
        ),
    )

    assert br._live_epoch2_claim_exists(outside_universe) is False


def test_soft_stop_observes_only_this_workers_live_canonical_epoch2_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wrapper may resume for a won live claim, never pending v2 work."""
    import hashlib
    import sqlite3
    from datetime import datetime, timedelta, timezone

    import rfc8785

    from fantasy_daemon.branch_registrations import (
        clear_restartable_soft_stop,
    )
    from tinyassets.branch_tasks_v2 import (
        Epoch2BranchTaskAdapter,
        WorkerClaimDescriptor,
    )
    from tinyassets.daemon_server import initialize_author_server
    from tinyassets.storage import db_path
    from tinyassets.storage.request_admissions import RequestAdmissionStore

    universe_path = tmp_path / "test-universe"
    universe_path.mkdir()
    initialize_author_server(tmp_path)
    body = rfc8785.dumps({
        "branch_id": "",
        "directed_daemon_id": "",
        "directed_daemon_instruction": "",
        "pickup_incentive": "",
        "priority_weight": 25.0,
        "request_type": "general",
        "schema_version": "request-admission-v2",
        "text": "Resume this claimed request.",
        "universe_id": universe_path.name,
    })
    committed = RequestAdmissionStore(tmp_path).commit_admission(
        tenant_id="tenant-a",
        actor_id="actor-a",
        universe_id=universe_path.name,
        idempotency_key_hash=(
            "hmac-sha256:"
            + hashlib.sha256(b"restart-key").hexdigest()
        ),
        body_digest="sha256:" + hashlib.sha256(body).hexdigest(),
        body_digest_version="rfc8785-v1",
        request_type="general",
        text="Resume this claimed request.",
        branch_id="",
        branch_def_id="fantasy_author:universe_cycle_wrapper",
        trigger_source="operator_request",
        accepted_priority_weight=25.0,
        policy_version="operator-priority-v1",
        grant_generation=1,
        receipt={
            "authority": "request-local",
            "grant_generation": 1,
            "priority_policy_version": "operator-priority-v1",
            "directed_assignment": {},
        },
        directed_daemon_id="",
        created_at="2026-07-24T10:00:00+00:00",
    )
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_WORKER_ID", "worker-a")
    monkeypatch.setattr(
        "tinyassets.branch_tasks_v2.EPOCH2_QUEUE_CONSUMER_READY",
        True,
    )
    stopped = {
        "universe_id": universe_path.name,
        "universe_path": str(universe_path),
        "health": {
            "stopped": True,
            "idle_reason": "worldbuild_stuck",
            "worldbuild_noop_streak": 3,
            "cycle_noop_streak": 0,
        },
    }

    assert clear_restartable_soft_stop(stopped)["health"]["stopped"] is True

    now = datetime.now(timezone.utc)
    descriptor = WorkerClaimDescriptor(
        queue_protocol_version=2,
        capabilities=frozenset({"operator_request_v1"}),
        worker_id="worker-a",
        runtime_instance_id="runtime-a",
        boot_id="boot-a",
        build_sha="a" * 40,
        config_hash="b" * 64,
        universe_id=universe_path.name,
        expires_at=(now + timedelta(seconds=60)).isoformat(),
    )
    claimed = Epoch2BranchTaskAdapter(
        tmp_path,
        clock=lambda: now,
    ).claim(
        committed["branch_task_id"],
        descriptor=descriptor,
        descriptor_reader=lambda _conn, _worker_id: descriptor,
    )
    assert claimed is not None
    with sqlite3.connect(db_path(tmp_path)) as conn:
        before = {
            "request": conn.execute(
                "SELECT * FROM user_requests ORDER BY rowid"
            ).fetchall(),
            "admission": conn.execute(
                "SELECT * FROM request_admissions ORDER BY rowid"
            ).fetchall(),
            "task": conn.execute(
                "SELECT * FROM branch_tasks_v2 ORDER BY rowid"
            ).fetchall(),
            "event": conn.execute(
                "SELECT * FROM request_admission_events ORDER BY rowid"
            ).fetchall(),
        }

    monkeypatch.setenv("TINYASSETS_WORKER_ID", "worker-b")
    assert clear_restartable_soft_stop(stopped)["health"]["stopped"] is True
    monkeypatch.setenv("TINYASSETS_WORKER_ID", "worker-a")
    repaired = clear_restartable_soft_stop(stopped)

    assert repaired["health"]["stopped"] is False
    assert repaired["health"]["idle_reason"] == ""
    assert not (universe_path / "requests.json").exists()
    assert not (universe_path / "work_targets.json").exists()
    with sqlite3.connect(db_path(tmp_path)) as conn:
        after = {
            "request": conn.execute(
                "SELECT * FROM user_requests ORDER BY rowid"
            ).fetchall(),
            "admission": conn.execute(
                "SELECT * FROM request_admissions ORDER BY rowid"
            ).fetchall(),
            "task": conn.execute(
                "SELECT * FROM branch_tasks_v2 ORDER BY rowid"
            ).fetchall(),
            "event": conn.execute(
                "SELECT * FROM request_admission_events ORDER BY rowid"
            ).fetchall(),
        }
    assert after == before


# ───────────────────────────────────────────────────────────────────────
# Checkpoint regression under flag-on (§4.11, option-1 accepted)
# ───────────────────────────────────────────────────────────────────────


def test_flag_on_boundary_state_resumes_only_six_fields(
    tmp_path, fresh_registrations,
):
    """§4.11 regression: under flag-on, the SqliteSaver stores only
    the boundary state. Mid-cycle state (workflow_instructions,
    task_queue) is NOT persisted across wrapper invocations. Accept
    this asymmetry; test just pins it.
    """
    # Build the outer Branch's StateGraph, compile with a saver,
    # run one invocation, check saved state shape.
    from langgraph.checkpoint.sqlite import SqliteSaver

    from fantasy_daemon.__main__ import _build_unified_graph_builder

    graph = _build_unified_graph_builder()
    db_path = tmp_path / "ckpt.sqlite"
    with SqliteSaver.from_conn_string(str(db_path)) as saver:
        compiled = graph.compile(checkpointer=saver)
        # Stub the wrapper so we don't invoke the full fantasy
        # graph in this test. Re-register with a fake that returns
        # boundary-shaped state.
        fresh_registrations.register_domain_callable(
            "fantasy_author",
            "universe_cycle_wrapper",
            lambda s: {
                "total_words": 10, "total_chapters": 1,
                "health": {"stopped": True},
            },
        )
        # Re-build + recompile so the new callable is bound.
        graph2 = _build_unified_graph_builder()
        compiled = graph2.compile(checkpointer=saver)
        config = {"configurable": {"thread_id": "u1"}}
        compiled.invoke(
            {"universe_id": "u1", "universe_path": str(tmp_path),
             "premise_kernel": "x"},
            config=config,
        )
        state = compiled.get_state(config)
    # Only boundary fields survive.
    values = state.values
    assert "total_words" in values
    assert "total_chapters" in values
    # Mid-cycle fields are absent — they were never in the outer
    # state_schema to begin with.
    assert "workflow_instructions" not in values
    assert "task_queue" not in values


# ───────────────────────────────────────────────────────────────────────
# Regression safety (§4.4 closing 2)
# ───────────────────────────────────────────────────────────────────────


def test_flag_off_default_is_off(monkeypatch):
    """No env var set → flag is off. Direct path is the default."""
    monkeypatch.delenv("TINYASSETS_UNIFIED_EXECUTION", raising=False)
    from fantasy_daemon.__main__ import _workflow_unified_execution_enabled
    assert _workflow_unified_execution_enabled() is False


def test_seed_yaml_exists_and_compiles(fresh_registrations):
    """The committed seed YAML is a valid BranchDefinition that
    compiles cleanly. Landing regression guard.
    """
    from fantasy_daemon.__main__ import _build_unified_graph_builder
    graph = _build_unified_graph_builder()
    assert graph is not None
    compiled = graph.compile()
    assert compiled is not None
