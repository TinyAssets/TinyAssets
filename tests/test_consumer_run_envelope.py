"""One canonical turn reserves the common execution envelope, never starts it."""

import json
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.test_consumer_selection import HOME, OWNER, component, install
from tests.test_consumer_selection import store as store
from tests.test_conversation_run_admissions import source_version
from tinyassets.runs import runs_db_path
from tinyassets.storage import conversation_run_admissions as cr
from tinyassets.storage import db_path, run_input_admissions


def setup(base, *, reply_key="answer"):
    from tinyassets.consumer_runtime import initialize

    initialize(base)
    version = source_version(base)
    config = component()
    config.update(branch_version_id=version.branch_version_id, content_hash=version.content_hash,
                  reply_key=reply_key)
    binding, _ = install(base, source=config)
    return {"version": 1, "message": "unaltered question", "input_method": "typed",
            "model_choice": None, "binding_id": binding["agent_binding_id"],
            "binding_revision": binding["revision"]}


def reserve(base, key, intent, context=None):
    from tinyassets.consumer_runtime import reserve_prepared_turn

    return reserve_prepared_turn(
        base, owner=OWNER, universe=HOME, request_key=key, intent=intent,
        context=context or {"version": 1, "history": [], "preferences": None},
    )


def test_single_run_envelope_replay_keeps_original_context_after_selection_disabled(store):
    intent = setup(store)
    key = str(uuid.uuid4())
    first = reserve(store, key, intent)
    with sqlite3.connect(db_path(store)) as conn:
        conn.execute("UPDATE agent_bindings SET configuration_json='{}'")
    again = reserve(store, key, intent, {"version": 1, "history": ["later"]})
    assert first == again
    with sqlite3.connect(runs_db_path(store)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        envelope = run_input_admissions.load_in_transaction(
            conn, run_id=first["run_id"], owner_id=OWNER, universe_id=HOME,
        )
        assert envelope["inputs"] == {"message": "unaltered question", "context": []}
        assert envelope["execution_started_at"] is None
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 1


def test_envelope_failure_rolls_back_both_canonical_and_run_records(store, monkeypatch):
    intent = setup(store)
    monkeypatch.setattr(run_input_admissions, "accept_in_transaction", lambda *a, **k: (
        _ for _ in ()).throw(RuntimeError("envelope failed")))
    with pytest.raises(RuntimeError, match="envelope failed"):
        reserve(store, str(uuid.uuid4()), intent)
    with sqlite3.connect(runs_db_path(store)) as conn:
        assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM conversation_run_admissions").fetchone()[0] == 0


@pytest.mark.parametrize("mutation", [
    "UPDATE branch_definitions SET visibility='private'",
    "UPDATE agent_bindings SET revision=revision+1",
    "UPDATE branch_versions SET status='retired'",
])
def test_prepare_rechecks_source_and_selection_without_new_connections(
    store, monkeypatch, mutation,
):
    from tinyassets.consumer_runtime import validate_prepared_turn

    intent = setup(store)
    row = reserve(store, str(uuid.uuid4()), intent)
    with sqlite3.connect(db_path(store)) as author, sqlite3.connect(runs_db_path(store)) as runs:
        author.row_factory = runs.row_factory = sqlite3.Row
        author.execute("BEGIN IMMEDIATE")
        runs.execute("BEGIN IMMEDIATE")
        envelope = run_input_admissions.load_in_transaction(
            runs, run_id=row["run_id"], owner_id=OWNER, universe_id=HOME,
        )
        assert validate_prepared_turn(
            store, envelope, author_conn=author, runs_conn=runs,
        )["admission_id"] == row["admission_id"]
        (runs if "branch_versions" in mutation else author).execute(mutation)
        monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("new connection"))
        with pytest.raises((PermissionError, ValueError)):
            validate_prepared_turn(store, envelope, author_conn=author, runs_conn=runs)


def test_static_consumer_adapter_keeps_v1_execution_options_after_default_changes(
    store, monkeypatch,
):
    from tinyassets import runs as runtime
    from tinyassets.consumer_runtime import prepare_admitted_consumer

    row = reserve(store, str(uuid.uuid4()), setup(store))
    monkeypatch.setattr(runtime, "DEFAULT_RECURSION_LIMIT", 77)
    monkeypatch.setattr("tinyassets.providers.work_candidate_data.prepare_captured_choices",
                        lambda *a, **k: pytest.fail("no discovery under SQL writers"))
    with sqlite3.connect(db_path(store)) as author, sqlite3.connect(runs_db_path(store)) as runs:
        author.row_factory = runs.row_factory = sqlite3.Row
        author.execute("BEGIN IMMEDIATE")
        runs.execute("BEGIN IMMEDIATE")
        envelope = run_input_admissions.load_in_transaction(
            runs, run_id=row["run_id"], owner_id=OWNER, universe_id=HOME,
        )
        prepared = prepare_admitted_consumer(
            store, envelope, author_conn=author, runs_conn=runs,
        )
        assert prepared.recursion_limit == 100
        assert prepared.concurrency_budget_override is None
        assert prepared.identity.user_id == OWNER
        assert prepared.actor == f"universe:{HOME}"


def test_changed_intent_conflicts_before_current_installation_lookup(store):
    intent = setup(store)
    key = str(uuid.uuid4())
    reserve(store, key, intent)
    with sqlite3.connect(db_path(store)) as conn:
        conn.execute("UPDATE agent_bindings SET configuration_json=?", (json.dumps({}),))
    with pytest.raises(cr.IntentConflict):
        reserve(store, key, {**intent, "message": "different"})


def test_concurrent_identical_intent_reserves_one_common_envelope(store):
    intent = setup(store)
    key = str(uuid.uuid4())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: reserve(store, key, intent), range(4)))
    assert len({item["run_id"] for item in results}) == 1
    with sqlite3.connect(runs_db_path(store)) as conn:
        assert conn.execute("SELECT count(*) FROM run_input_admissions").fetchone()[0] == 1


def test_prepared_input_must_match_original_canonical_mapping(store):
    from tinyassets.consumer_runtime import validate_prepared_turn

    row = reserve(store, str(uuid.uuid4()), setup(store))
    with sqlite3.connect(db_path(store)) as author, sqlite3.connect(runs_db_path(store)) as runs:
        author.row_factory = runs.row_factory = sqlite3.Row
        author.execute("BEGIN IMMEDIATE")
        runs.execute("BEGIN IMMEDIATE")
        envelope = run_input_admissions.load_in_transaction(
            runs, run_id=row["run_id"], owner_id=OWNER, universe_id=HOME,
        )
        envelope["inputs"]["message"] = "different instructions"
        with pytest.raises(PermissionError, match="input envelope changed"):
            validate_prepared_turn(store, envelope, author_conn=author, runs_conn=runs)


def test_empty_common_envelope_schema_does_not_block_previously_supported_reset(store):
    from tinyassets.consumer_runtime import initialize
    from tinyassets.daemon_server import ensure_universe_registered
    from tinyassets.scoped_reset import TestIdentityRoster, plan_test_identity_reset

    initialize(store)
    ensure_universe_registered(store, universe_id=HOME, universe_path=store / HOME)
    roster = TestIdentityRoster("consumer-reset-v1", {"receiver": OWNER}, frozenset({OWNER}))
    plan = plan_test_identity_reset(store, alias="receiver", roster=roster)
    assert plan["blockers"] == []


def test_account_erasure_removes_common_envelope_after_home_rebind(store):
    from tinyassets.account_deletion import delete_account
    from tinyassets.daemon_server import set_founder_home

    reserve(store, str(uuid.uuid4()), setup(store))
    (store / "u-next").mkdir()
    set_founder_home(store, founder_sub=OWNER, universe_id="u-next", platform_generated=True)
    receipt = delete_account(store, founder_sub=OWNER, cancel_billing=lambda _: "none",
                             delete_identity=lambda _: "deleted")
    assert receipt["unfinished_phases"] == []
    with sqlite3.connect(runs_db_path(store)) as conn:
        assert conn.execute("SELECT count(*) FROM run_input_admissions").fetchone()[0] == 0


@pytest.mark.parametrize("reported", ["", "actual-model"])
def test_real_compiler_observation_reaches_only_declared_reply_history(
    store, monkeypatch, reported,
):
    from tinyassets.branches import NodeDefinition
    from tinyassets.conversation_store import load_recent
    from tinyassets.graph_compiler import _build_prompt_template_node
    from tinyassets.providers.base import ProviderResponse
    from tinyassets.providers.execution_receipt import normalize_execution_receipt
    from tinyassets.runs import RunStepEvent, record_event

    row = reserve(store, str(uuid.uuid4()), setup(store, reply_key="reply_output"))
    def recorded_call(_prompt, _system, *, response_observer=None, **_kwargs):
        response = ProviderResponse(
            "answer", "owned", "configured-alias", "test", 0,
            reported_model=reported,
        )
        response_observer(response)
        return response.text

    def event_sink(node_id, phase, **detail):
        if phase == "ran":
            record_event(store, RunStepEvent(row["run_id"], 1, node_id, "ran", 0, detail=detail))

    node = _build_prompt_template_node(
        NodeDefinition(node_id="reply", display_name="Reply", prompt_template="hello"),
        provider_call=recorded_call, event_sink=event_sink,
    )
    output = node({})
    with sqlite3.connect(runs_db_path(store)) as conn:
        conn.execute("UPDATE runs SET status='completed', output_json=? WHERE run_id=?",
                     (json.dumps(output), row["run_id"]))
    with cr.authorized_scope(store, owner=OWNER, universe=HOME) as scope:
        projected = cr.project_terminal(scope, row["admission_id"])
    receipt = {
        "provider": "owned", "model": reported,
        "model_status": "reported" if reported else "unknown",
        "resolved_model": "configured-alias", "family": "test",
        "execution": "model_response", "outcome": "succeeded",
        "usage_provenance": "unknown",
        **({"reported_model": reported} if reported else {}),
    }
    assert json.loads(projected["terminal_json"])["execution"] == receipt
    history = load_recent(store / HOME, f"principal:{OWNER}")
    assert history[0].execution is None
    assert normalize_execution_receipt(history[-1].execution) == receipt
