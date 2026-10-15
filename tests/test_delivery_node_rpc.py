"""Trusted parent-mediated delivery: real subprocess RPC and two-owner SQLite."""
# ruff: noqa: F811 -- imported pytest fixtures
import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from tests.test_delivery_public import linked  # noqa: F401
from tests.test_receiver_links import env  # noqa: F401
from tinyassets import engine_admissions, runs
from tinyassets.api import deliveries as api
from tinyassets.auth.middleware import identity_context
from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
from tinyassets.daemon_server import (
    get_branch_definition,
    grant_universe_access,
    revoke_universe_access,
    save_branch_definition,
)
from tinyassets.storage import deliveries

SOURCE = """def run(state):
    ids = []
    for occurrence in ['first', 'first', 'second']:
        receipt = invoke_mcp_action('deliver_output', link_id=state['link'],
            occurrence_id=occurrence, outputs={'result': state['topic']})
        ids.append(receipt['delivery_id'])
    return {'result': state['topic'], 'receipts': ids}
"""


@pytest.fixture
def node_env(linked, monkeypatch):
    base, auth, receiver, link = linked
    branch = BranchDefinition.from_dict(get_branch_definition(base, branch_def_id="b-sender"))
    node = branch.node_defs[0]
    node.prompt_template = ""
    node.source_code = SOURCE
    node.input_keys = ["topic", "link"]
    node.output_keys = ["result", "receipts"]
    node.tools_allowed = ["deliver_output"]
    # Attribution/approval is not an additional permission grant.
    node.approved = False
    branch.fork_from = "original-contributor-version"
    branch.state_schema += [{"name": "link", "type": "str"}, {"name": "receipts", "type": "list"}]
    save_branch_definition(base, branch_def=branch.to_dict())
    dispatched = []
    monkeypatch.setattr(api.delivery_runtime, "dispatch_accepted_delivery",
                        lambda *args, **kwargs: dispatched.append(kwargs))
    return base, auth, receiver, link, branch, dispatched


def _prepare(base, branch, *, owner="sender", universe="u-sender"):
    return runs._prepare_run(
        base, branch=branch, inputs={}, run_name="RPC source", actor=f"universe:{universe}",
        owner_user_id=owner, queue_universe_id=universe,
    )


def _execute(base, branch, link, *, run_id=None):
    run_id = run_id or _prepare(base, branch)
    # No request identity exists on a scheduled/background execution thread.
    with identity_context(None):
        outcome = runs._invoke_graph(
            base, run_id=run_id, branch=branch,
            inputs={"topic": "exact 🍉", "link": link["link_id"]},
            provider_call=None, recursion_limit=100,
        )
    return outcome


def _rows(base):
    with deliveries.transaction(base) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM graph_deliveries")]


def _source(base, branch, run_id):
    return api.NodeDeliverySource(
        owner_user_id="sender", universe_id="u-sender", actor="universe:u-sender",
        run_id=run_id, branch_def_id=branch.branch_def_id, node_id="entry",
        output_keys=tuple(branch.node_defs[0].output_keys),
    )


def _send(base, source, link, occurrence="one", outputs=None, cancel=lambda: False):
    return api.deliver_node_output(
        base, source=source, link_id=link["link_id"], occurrence_id=occurrence,
        outputs=outputs or {"result": "exact"}, should_cancel=cancel,
    )


def test_actual_subprocess_rpc_loop_uses_owner_and_placement_without_request_identity(node_env):
    base, _, _, link, branch, dispatched = node_env
    outcome = _execute(base, branch, link)
    assert outcome.status == "completed", outcome.error
    ids = outcome.output["receipts"]
    assert ids[0] == ids[1] != ids[2]
    rows = _rows(base)
    assert len(rows) == 2
    assert {r["source_run_id"] for r in rows} == {outcome.run_id}
    assert {r["source_node_id"] for r in rows} == {"entry"}  # not definition
    assert {r["sender_id"] for r in rows} == {"sender"}
    assert all(json.loads(r["inputs_json"]) == {"topic": "exact 🍉"} for r in rows)
    assert len(dispatched) == 3  # replay reconciles, never reserves a new receiver run
    again = _execute(base, branch, link)
    assert again.status == "completed", again.error
    assert len(_rows(base)) == 4


@pytest.mark.parametrize("extra", ["universe_id", "source_run_id", "source", "actor", "node_id"])
def test_child_cannot_forge_context_through_rpc(node_env, extra):
    base, _, _, link, branch, _ = node_env
    branch.node_defs[0].source_code = SOURCE.replace(
        "outputs={'result': state['topic']})",
        f"outputs={{'result': state['topic']}}, {extra}='forged')",
    )
    result = _execute(base, branch, link)
    assert result.status == "failed"
    assert "delivery_parameters" in result.error
    assert _rows(base) == []


def test_delivery_requires_tool_declaration(node_env):
    base, _, _, link, branch, _ = node_env
    branch.node_defs[0].tools_allowed = []
    result = _execute(base, branch, link)
    assert result.status == "failed"
    assert "tools_allowed" in result.error
    assert _rows(base) == []


@pytest.mark.parametrize("field,value", [
    ("owner_user_id", "outsider"), ("universe_id", "u-outsider"),
    ("actor", "universe:u-outsider"), ("run_id", "missing"),
    ("branch_def_id", "b-outsider"), ("node_id", "definition"),
    ("output_keys", ("other",)),
])
def test_trusted_source_must_match_persisted_run_link_and_compiled_outputs(node_env, field, value):
    base, auth, _, link, branch, _ = node_env
    run_id = _prepare(base, branch)
    runs.update_run_status(base, run_id, status="running")
    auth("sender")  # ambient valid identity cannot repair invalid source context
    with pytest.raises((PermissionError, ValueError)):
        _send(base, replace(_source(base, branch, run_id), **{field: value}), link)
    assert _rows(base) == []


@pytest.mark.parametrize("revoke", ["sender", "receiver", "link", "source_terminal"])
def test_current_authority_and_live_source_are_required(node_env, revoke):
    base, _, _, link, branch, _ = node_env
    run_id = _prepare(base, branch)
    runs.update_run_status(base, run_id, status="running")
    if revoke in {"sender", "receiver"}:
        revoke_universe_access(base, universe_id="u-" + revoke, actor_id=revoke)
    elif revoke == "link":
        api.management.disconnect_output(universe_id="u-sender", link_id=link["link_id"])
    else:
        runs.update_run_status(base, run_id, status="cancelled")
    with pytest.raises((PermissionError, ValueError)):
        _send(base, _source(base, branch, run_id), link)
    assert _rows(base) == []


def test_handler_cancellation_refuses_before_acceptance(node_env):
    base, _, _, link, branch, _ = node_env
    run_id = _prepare(base, branch)
    runs.update_run_status(base, run_id, status="running")
    source = _source(base, branch, run_id)
    with pytest.raises(ValueError, match="cancel"):
        _send(base, source, link, cancel=lambda: True)
    assert _rows(base) == []
    receipt = _send(base, source, link)
    runs.update_run_status(base, run_id, status="cancelled")
    assert _rows(base)[0]["delivery_id"] == receipt["delivery_id"]


def test_node_replay_content_conflict_and_direct_key_collision_preserve_original(node_env):
    base, _, _, link, branch, _ = node_env
    run_id = _prepare(base, branch)
    runs.update_run_status(base, run_id, status="running")
    source = _source(base, branch, run_id)
    receipt = _send(base, source, link)
    assert _send(base, source, link)["delivery_id"] == receipt["delivery_id"]
    with pytest.raises(deliveries.OccurrenceConflict):
        _send(base, source, link, outputs={"result": "different"})
    row = _rows(base)[0]
    with pytest.raises(deliveries.OccurrenceConflict):
        api.deliver_output(universe_id="u-sender", link_id=link["link_id"],
                           occurrence_id=row["occurrence_id"], outputs={"result": "exact"})
    # Legacy direct rows may use any key, including generated-looking keys.
    future = replace(source, run_id=_prepare(base, branch))
    runs.update_run_status(base, future.run_id, status="running")
    key = api.node_occurrence_id(future, link["link_id"], "one")
    direct = api.deliver_output(universe_id="u-sender", link_id=link["link_id"],
                                occurrence_id=key, outputs={"result": "exact"})
    with pytest.raises(deliveries.OccurrenceConflict):
        _send(base, future, link)
    assert len(_rows(base)) == 2
    legacy = next(r for r in _rows(base) if r["delivery_id"] == direct["delivery_id"])
    assert legacy["source_run_id"] is None


def test_transfer_settles_source_write_even_after_terminal_read_settlement(node_env):
    base, _, _, link, branch, _ = node_env
    run_id = _prepare(base, branch)
    ticket = engine_admissions.admit("u-sender")
    assert ticket > 0
    engine_admissions.attach_run(ticket, run_id)
    result = _execute(base, branch, link, run_id=run_id)
    assert result.status == "completed", result.error
    engine_admissions.reclassify_read(run_id)
    import sqlite3
    with sqlite3.connect(engine_admissions.ledger_path()) as conn:
        row = conn.execute("SELECT kind FROM settlements WHERE run_id=?", (run_id,)).fetchone()
        assert row[0] == "write"


def test_nested_invoke_uses_child_branch_run_and_inherited_owner(node_env):
    base, _, _, link, child, _ = node_env
    parent = BranchDefinition(
        branch_def_id="b-parent", name="parent", author="sender", visibility="private",
        node_defs=[NodeDefinition(
            node_id="invoke", display_name="invoke", input_keys=["topic", "link"],
            output_keys=["receipts"], invoke_branch_spec={"branch_def_id": child.branch_def_id,
                "inputs_mapping": {"topic": "topic", "link": "link"},
                "output_mapping": {"receipts": "receipts"}, "wait_mode": "blocking"})],
        graph_nodes=[GraphNodeRef(id="invoke")], entry_point="invoke",
        edges=[EdgeDefinition("START", "invoke"), EdgeDefinition("invoke", "END")],
        state_schema=child.state_schema,
    )
    save_branch_definition(base, branch_def=parent.to_dict())
    outcome = _execute(base, parent, link)
    assert outcome.status == "completed", outcome.error
    rows = _rows(base)
    assert len(rows) == 2
    assert {r["source_branch_id"] for r in rows} == {child.branch_def_id}
    assert all(r["source_run_id"] != outcome.run_id for r in rows)
    child_run = runs.get_run(base, rows[0]["source_run_id"])
    assert child_run["owner_user_id"] == "sender"
    assert child_run["queue_universe_id"] == "u-sender"


def test_resume_execution_context_includes_persisted_owner(node_env):
    base, _, _, _, branch, _ = node_env
    run_id = _prepare(base, branch)
    ctx = contextvars.Context().run(runs._execution_context_for_run, base, run_id, branch)
    assert ctx.owner_user_id == "sender"
    assert ctx.actor == "universe:u-sender"


def test_missing_run_owner_is_not_filled_from_ambient_identity(node_env):
    base, auth, _, link, branch, _ = node_env
    auth("sender")
    run_id = _prepare(base, branch, owner="")
    outcome = _execute(base, branch, link, run_id=run_id)
    assert outcome.status == "failed"
    assert "delivery_source_unavailable" in outcome.error
    assert _rows(base) == []


def test_concurrent_same_occurrence_reserves_one_receiver_run(node_env):
    base, _, _, link, branch, _ = node_env
    run_id = _prepare(base, branch)
    runs.update_run_status(base, run_id, status="running")
    source = _source(base, branch, run_id)
    with ThreadPoolExecutor(max_workers=2) as workers:
        receipts = list(workers.map(lambda _: _send(base, source, link), range(2)))
    assert receipts[0]["delivery_id"] == receipts[1]["delivery_id"]
    assert len(_rows(base)) == 1
    with deliveries.transaction(base) as conn:
        assert conn.execute("SELECT count(*) FROM graph_delivery_attempts").fetchone()[0] == 1


def test_actual_rpc_cancellation_is_checked_inside_handler_after_read(node_env):
    from tinyassets import graph_compiler
    from tinyassets.boxes import node as node_sandbox

    base, _, _, link, branch, _ = node_env
    run_id = _prepare(base, branch)
    runs.update_run_status(base, run_id, status="running")
    cancelled = []
    invoker = graph_compiler._build_node_mcp_invoker(
        branch.node_defs[0], base_path=base,
        event_sink=lambda **_: cancelled.append(True),
        delivery_source=_source(base, branch, run_id), should_cancel=lambda: bool(cancelled),
    )
    result = node_sandbox.NodeSandbox(timeout=10, universe_dir=base / "u-sender").run_sync(
        node_id="entry", source_code=SOURCE, input_keys=["topic", "link"],
        input_state={"topic": "exact", "link": link["link_id"]},
        output_keys=["result", "receipts"],
        invoke=lambda action, kwargs: invoker(action, **kwargs),
    )
    assert not result.success
    assert "delivery_source_cancelled" in str(result)
    assert _rows(base) == []


@pytest.mark.real_jail
def test_real_linux_jail_transports_delivery_rpc(node_env, monkeypatch):
    base, _, _, link, branch, _ = node_env
    outcome = _execute(base, branch, link)
    assert outcome.status == "completed", outcome.error
    assert len(_rows(base)) == 2


def test_foreground_graph_request_persists_authenticated_owner(node_env, monkeypatch):
    from tinyassets import universe_server

    base, auth, _, link, branch, _ = node_env
    auth("sender")
    monkeypatch.setattr("tinyassets.api.runs._bind_run_provider_call", lambda *a, **k: None)
    result = json.loads(universe_server.run_graph(
        branch_def_id=branch.branch_def_id, graph_id="u-sender",
        inputs_json=json.dumps({"topic": "exact", "link": link["link_id"]}),
    ))
    assert "run_id" in result, result
    runs.wait_for(result["run_id"], timeout=15)
    record = runs.get_run(base, result["run_id"])
    assert record["status"] == "completed", record["error"]
    assert record["owner_user_id"] == "sender"
    assert record["queue_universe_id"] == "u-sender"
    assert {row["source_run_id"] for row in _rows(base)} == {result["run_id"]}


def test_trigger_uses_explicit_owner_not_request_identity(node_env, monkeypatch):
    from tinyassets.api.runs import enqueue_universe_branch_run

    base, auth, _, link, branch, _ = node_env
    auth("outsider")
    monkeypatch.setattr("tinyassets.api.runs._bind_run_provider_call", lambda *a, **k: None)
    result = enqueue_universe_branch_run(
        base, universe_id="u-sender", branch_def_id=branch.branch_def_id,
        inputs={"topic": "exact", "link": link["link_id"]}, principal_id="sender",
    )
    runs.wait_for(result, timeout=15)
    record = runs.get_run(base, result)
    assert record["status"] == "completed", record["error"]
    assert record["owner_user_id"] == "sender"
    assert {row["sender_id"] for row in _rows(base)} == {"sender"}


@pytest.mark.parametrize("failure", [
    "revoked", "missing_owner", "spoofed_owner", "wrong_actor", "wrong_universe",
    "wrong_run", "missing_run", "public_foreign", "terminal", "row_owner_changed",
])
def test_owned_private_child_requires_persisted_current_parent_authority(node_env, failure):
    from tinyassets.graph_compiler import (
        BranchExecutionContext,
        CompilerError,
        _authorize_child_ref,
    )

    base, _, _, _, branch, _ = node_env
    run_id = _prepare(base, branch)
    runs.update_run_status(base, run_id, status="running")
    ctx = BranchExecutionContext(actor="universe:u-sender", universe_id="u-sender",
                                 owner_user_id="sender", definition_author="sender",
                                 caller_provenance="own")
    if failure == "revoked":
        revoke_universe_access(base, universe_id="u-sender", actor_id="sender")
    elif failure == "missing_owner":
        ctx = replace(ctx, owner_user_id="")
    elif failure == "spoofed_owner":
        ctx = replace(ctx, owner_user_id="outsider")
    elif failure == "wrong_actor":
        ctx = replace(ctx, actor="universe:u-outsider")
    elif failure == "wrong_universe":
        ctx = replace(ctx, universe_id="u-outsider")
    elif failure == "wrong_run":
        run_id = _prepare(base, branch, owner="outsider", universe="u-outsider")
        runs.update_run_status(base, run_id, status="running")
    elif failure == "missing_run":
        run_id = ""
    elif failure == "public_foreign":
        ctx = replace(ctx, caller_provenance="public-foreign")
    elif failure == "terminal":
        runs.update_run_status(base, run_id, status="cancelled")
    else:
        with runs._connect(base) as conn:
            conn.execute("UPDATE runs SET owner_user_id='outsider' WHERE run_id=?", (run_id,))
    with pytest.raises(CompilerError, match="child is not available"):
        _authorize_child_ref(base, branch.branch_def_id, ctx, parent_run_id=run_id)


def test_owned_private_child_of_owned_remix_is_available(node_env):
    from tinyassets.graph_compiler import _authorize_child_ref

    base, _, _, _, branch, _ = node_env
    run_id = _prepare(base, branch)
    runs.update_run_status(base, run_id, status="running")
    ctx = runs._execution_context_for_run(base, run_id, branch)
    assert ctx.definition_author == "sender"
    child = _authorize_child_ref(base, branch.branch_def_id, ctx, parent_run_id=run_id)
    assert child.branch_def_id == branch.branch_def_id
    assert child.fork_from == "original-contributor-version"


def test_owned_private_child_cannot_be_borrowed_by_coadmin_parent(node_env):
    from tinyassets.graph_compiler import CompilerError, _authorize_child_ref

    base, _, _, _, child, _ = node_env
    grant_universe_access(base, universe_id="u-sender", actor_id="outsider", permission="admin")
    parent = BranchDefinition.from_dict(child.to_dict())
    parent.branch_def_id = "coadmin-parent"
    parent.author = "outsider"
    save_branch_definition(base, branch_def=parent.to_dict())
    run_id = _prepare(base, parent)
    runs.update_run_status(base, run_id, status="running")
    ctx = runs._execution_context_for_run(base, run_id, parent)
    assert ctx.caller_provenance == "own"  # current co-admin is not this run's owner
    assert ctx.definition_author == "outsider"
    assert ctx.owner_user_id == "sender"
    with pytest.raises(CompilerError, match="child is not available"):
        _authorize_child_ref(base, child.branch_def_id, ctx, parent_run_id=run_id)


def test_owned_private_child_run_read_errors_refuse_uniformly(node_env, monkeypatch):
    from tinyassets.graph_compiler import CompilerError, _authorize_child_ref

    base, _, _, _, branch, _ = node_env
    run_id = _prepare(base, branch)
    runs.update_run_status(base, run_id, status="running")
    ctx = runs._execution_context_for_run(base, run_id, branch)

    def broken_read(*args, **kwargs):
        raise OSError("private database detail")

    monkeypatch.setattr(runs, "_connect", broken_read)
    with pytest.raises(CompilerError, match="^invoke_branch child is not available$"):
        _authorize_child_ref(base, branch.branch_def_id, ctx, parent_run_id=run_id)
