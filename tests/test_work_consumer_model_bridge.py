"""Captured preference DATA through real work admission, journal and router."""

import json
import sqlite3

import pytest

from tests import test_workflow_http_agent as work_tests
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_run_provider_session import _branch, _run_branch
from tinyassets.foreground_run_provider import _ForegroundRunProviderSession
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.storage.provider_work_authority import db_path

http_wire = work_tests.http_wire
work_agent = work_tests.work_agent


pytestmark = pytest.mark.usefixtures("cloud_runtime")


def captured_selection(monkeypatch):
    from tinyassets.config import load_universe_config
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.model_preferences import ModelPreferences

    original = _ForegroundRunProviderSession.__init__
    sessions = []

    def init(session, base_path, **kwargs):
        provider = load_universe_config(base_path / kwargs["universe_id"]).preferred_writer
        choices = ModelPreferences("explicit", ModelRef(provider, "synthetic-model"),
                                   (ModelRef(provider, "future-company/new-choice"),))
        # A TAB-LOCAL `current` choice, which is the conversation path's shape and
        # must win. `new_foreground_run_provider_session` now supplies the owner's
        # SAVED document itself, so this has to replace that keyword rather than
        # add a second one. Until it did, this injection was the only thing that
        # ever reached a run session -- which is the bug these tests hid.
        kwargs.pop("model_preference_data", None)
        original(session, base_path, **kwargs, model_preference_data={
            "version": 1, "saved": None, "observed_generation": 0,
            "current": choices.document(),
        })
        sessions.append(session)

    monkeypatch.setattr(_ForegroundRunProviderSession, "__init__", init)
    return sessions


@pytest.mark.parametrize("removed_after_failure", [False, True])
def test_selected_work_agent_changes_model_inside_same_journal_without_repeating_tool(
    tmp_path, monkeypatch, authenticate_request, work_agent, removed_after_failure,
):
    sessions = captured_selection(monkeypatch)
    if removed_after_failure:
        from tinyassets.providers import discovery_snapshot

        original_read = discovery_snapshot.read_http_discovery_document

        def catalogue(**kwargs):
            doc = original_read(**kwargs)
            if len(work_agent.wires) >= 2:
                doc["data"] = [m for m in doc["data"] if m["id"] != "synthetic-model"]
            return doc

        monkeypatch.setattr(discovery_snapshot, "read_http_discovery_document", catalogue)
    from tinyassets.exceptions import ProviderAuthorityHeldError
    from tinyassets.workflow_agent import WorkAgentAdapter
    original_infer = WorkAgentAdapter.infer
    transitions = []

    async def inspect_transition(adapter, **kwargs):
        if kwargs["context"].model_selection != adapter.selection:
            # A FAILED old carrier remains invalid for tools/observer checks;
            # only the pure staged-candidate prelude may reach fresh admission.
            with pytest.raises(ProviderAuthorityHeldError):
                adapter.check(kwargs["context"], kwargs["config"])
            with pytest.raises(PermissionError, match="receipt, claim or invocation changed"):
                adapter.session._check_agent_authority(adapter.carrier)
            transitions.append((adapter.carrier._receipt, adapter.carrier._claim))
        response = await original_infer(adapter, **kwargs)
        if transitions:
            assert (adapter.carrier._receipt, adapter.carrier._claim) == transitions[-1]
        return response

    monkeypatch.setattr(WorkAgentAdapter, "infer", inspect_transition)
    work_agent.mode = "later_model_capacity"
    branch = _branch(node_count=1)
    branch.node_defs[0].tools_allowed = ["universe_self"]
    result, _, _ = _run_branch(tmp_path, monkeypatch, authenticate_request, branch,
                               open_provider=True, model_access=ModelAccess("discovered"))
    assert result["terminal_status"] == "completed", (result, work_agent.errors)
    assert len(sessions) == 1
    assert [wire["body"]["model"] for wire in work_agent.wires] == [
        "synthetic-model", "synthetic-model", "future-company/new-choice",
    ]
    assert len(work_agent.tools) == 1
    assert len(transitions) == 1
    turn = work_agent.latest()
    assert turn.state == "completed" and len(turn.rounds) == 3
    assert turn.rounds[0].tools[0].state == "completed"
    assert "known work result" in json.dumps(work_agent.wires[-1]["body"])
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_turns").fetchone() == (1,)
        reservations = [json.loads(row[0]) for row in conn.execute(
            "SELECT record_json FROM provider_invocation_reservations ORDER BY ordinal",
        )]
    assert [row["state"] for row in reservations] == ["succeeded", "failed", "succeeded"]
    assert len({row["reservation_id"] for row in reservations}) == 3
    assert {row["receipt_id"] for row in reservations} == {turn.work_receipt_id}
    from tinyassets.runs import runs_db_path
    with sqlite3.connect(runs_db_path(tmp_path)) as conn:
        detail = json.loads(conn.execute(
            "SELECT detail_json FROM run_events WHERE run_id=? AND status='ran'",
            (result["run_id"],),
        ).fetchone()[0])
    execution = dict(detail["execution"])
    usage = execution.pop("usage")
    assert usage["dispatched"] == 3
    assert usage["quota_authoritative"] is False
    assert sum(source["failed"] for source in usage["sources"]) == 1
    assert execution == {
        "provider": sessions[0]._work_candidates.order[0].connection_id,
        # The owner's own name for the connection that answered, beside the
        # routing identity (never instead of it).
        "provider_display": "compute:synthetic",
        "model": "actual-work-model",
        "model_status": "reported",
    }
    assert detail["provider_model"] == "future-company/new-choice"
    assert detail["provider_attempts"] == 3


@pytest.mark.parametrize("fail_round", [1, 2])
def test_observer_failure_after_durable_intent_is_held_not_replayed(
    tmp_path, monkeypatch, authenticate_request, work_agent, fail_round,
):
    from tests.test_workflow_http_agent import run
    from tinyassets.agent_turn_coordinator import AgentTurnCoordinator

    original = AgentTurnCoordinator._begin

    def fail_after_intent(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if len(self.turn.rounds) == fail_round:
            raise RuntimeError("synthetic observer failure after durable intent")

    monkeypatch.setattr(AgentTurnCoordinator, "_begin", fail_after_intent)
    result = run(tmp_path, monkeypatch, authenticate_request)
    assert result["terminal_status"] == "failed"
    assert len(work_agent.wires) == len(work_agent.tools) == fail_round - 1
    assert len(work_agent.latest().rounds) == fail_round
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_turns").fetchone() == (1,)
        assert conn.execute("SELECT COUNT(*) FROM provider_invocation_reservations").fetchone() == (
            fail_round,
        )


@pytest.mark.parametrize("mode,expected", [("authentication", 1), ("all_models_full", 2)])
def test_authentication_holds_and_exhaustion_never_restarts_first_model(
    tmp_path, monkeypatch, authenticate_request, work_agent, mode, expected,
):
    captured_selection(monkeypatch)
    work_agent.mode = mode
    branch = _branch(node_count=1)
    branch.node_defs[0].tools_allowed = ["universe_self"]
    result, _, _ = _run_branch(tmp_path, monkeypatch, authenticate_request, branch,
                               open_provider=True, model_access=ModelAccess("discovered"))
    assert result["terminal_status"] == "failed"
    assert len(work_agent.wires) == expected and work_agent.tools == []
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM agent_turns").fetchone() == (1,)


@pytest.mark.parametrize("restart_before_submit", [False, True])
def test_public_selected_reusable_graph_uses_reserved_run_and_exact_reply_projection(
    tmp_path, monkeypatch, authenticate_request, work_agent, restart_before_submit,
):
    import uuid

    from tests.test_run_provider_session import _seed_open_serving_assignment
    from tinyassets import universe_server
    from tinyassets.api import branches, helpers, permissions
    from tinyassets.branch_versions import publish_branch_version
    from tinyassets.consumer_runtime import initialize
    from tinyassets.custom_agents import create_binding, publish_definition
    from tinyassets.daemon_server import (
        get_branch_definition,
        save_branch_definition,
        set_founder_home,
    )
    from tinyassets.providers import call
    from tinyassets.providers.model_policy import ModelRef
    from tinyassets.providers.model_preferences import ModelPreferences
    from tinyassets.providers.router import ProviderRouter
    from tinyassets.runs import wait_for

    owner, home = "acct_alice", "universe_alice"
    authenticate_request(owner)
    set_founder_home(tmp_path, founder_sub=owner, universe_id=home, platform_generated=True)
    router = ProviderRouter({})
    monkeypatch.setattr(call, "_real_router", router)
    monkeypatch.setattr(call, "get_provider_router", lambda: router)
    provider = _seed_open_serving_assignment(tmp_path, monkeypatch,
                                            model_access=ModelAccess("discovered"))
    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(permissions, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(branches, "_base_path", lambda: tmp_path)
    branch = _branch(node_count=1, author="public-creator")
    branch.node_defs[0].tools_allowed = ["universe_self"]
    branch.node_defs[0].llm_policy = None
    branch.visibility = "public"
    save_branch_definition(tmp_path, branch_def=branch.to_dict())
    # The creator's explicit publish marks THIS version public; only a marked
    # version of a public branch is readable (so remixable) by anyone else.
    version = publish_branch_version(tmp_path, branch.to_dict(), publisher="public-creator",
                                     public=True)
    assert "name" not in version.snapshot  # Existing published executable-pin format.
    public_version = version
    remixed = json.loads(universe_server.write_graph(target="branch", operation="remix",
        payload_json=json.dumps({"name": "Receiver remix", "fork_from": version.branch_version_id,
                                 "visibility": "private"})))
    assert "branch_def_id" in remixed, remixed
    owned = get_branch_definition(tmp_path, branch_def_id=remixed["branch_def_id"])
    assert owned["author"] == owner and owned["fork_from"] == public_version.branch_version_id
    assert owned["parent_def_id"] == branch.branch_def_id
    version = publish_branch_version(tmp_path, owned, publisher=owner)
    definition = publish_definition(tmp_path, author_id="public-creator", payload={
        "schema_version": 1, "name": "Reusable test conversation", "description": "Test fixture",
        "tags": [], "components": {"turn": {"kind": "tinyassets.turn-graph.v1", "version": 1,
            "branch_version_id": version.branch_version_id, "content_hash": version.content_hash,
            "input_map": {"message": "message"}, "reply_key": "answer_1"}},
    })
    binding = create_binding(tmp_path, universe_id=home,
                             definition_id=definition["agent_definition_id"], created_by=owner,
                             payload={"schema_version": 1, "name": "Chosen conversation",
        "role": "app_experience", "turn_consumer": {"version": 1, "state": "active",
            "component_key": "turn", "definition_fingerprint": definition["content_fingerprint"]}})
    initialize(tmp_path)
    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(permissions, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(helpers, "_universe_dir", lambda _: tmp_path / home)
    monkeypatch.setattr("tinyassets.universe_intelligence.converse",
                        lambda *a, **k: pytest.fail("default writer must never run"))
    choice = ModelPreferences("explicit", ModelRef(provider, "synthetic-model"),
                              (ModelRef(provider, "future-company/new-choice"),)).document()
    request = {"version": 1, "request_key": str(uuid.uuid4()),
               "binding_id": binding["agent_binding_id"], "binding_revision": binding["revision"]}
    work_agent.mode = "later_model_capacity"
    from tinyassets import run_input_origins, runs

    with monkeypatch.context() as crash_window:
        if restart_before_submit:
            crash_window.setattr(run_input_origins, "dispatch_initial_run", lambda *a, **k: None)
        first = json.loads(universe_server.converse("Hello", home, "typed", choice, request))
    assert "consumer_turn" in first, first
    run_id = first["consumer_turn"]["run_id"]
    if restart_before_submit:
        import contextvars

        assert not work_agent.wires and not work_agent.tools
        assert runs.get_run(tmp_path, run_id)["status"] == "queued"
        # Simulate loss after admission commit, then independently nominate in
        # a fresh process context. No request-local identity/closure is retained.
        monkeypatch.setattr(run_input_origins, "_settled", lambda *a, **k: None)
        contextvars.Context().run(run_input_origins.reconcile_admitted_runs, tmp_path)
    wait_for(first["consumer_turn"]["run_id"], timeout=10)
    observed = json.loads(universe_server.read_graph(target="conversation_turn", graph_id=home,
                                                     request_key=request["request_key"]))
    assert observed["consumer_turn"]["state"] == "completed", observed
    if restart_before_submit:
        # Read alone repairs the dropped callback; resending is not needed.
        assert observed["consumer_turn"]["projection"] == "committed"
        run_input_origins.reconcile_admitted_runs(tmp_path)
    result = json.loads(universe_server.converse("Hello", home, "typed", choice, request))
    assert result["consumer_turn"]["state"] == "completed", (result, work_agent.errors)
    assert result["reply"] == "work completed"
    execution = dict(result["execution"])
    usage = execution.pop("usage")
    assert usage["dispatched"] == 3
    assert usage["quota_authoritative"] is False
    assert sum(source["failed"] for source in usage["sources"]) == 1
    assert execution == {"provider": provider, "model": "actual-work-model",
                                    "provider_display": "compute:synthetic",
                                    "model_status": "reported"}
    assert len(work_agent.wires) == 3 and len(work_agent.tools) == 1
    assert work_agent.latest().state == "completed"
    assert len(work_agent.latest().rounds) == 3
    assert json.loads(universe_server.read_graph(target="conversation_turn", graph_id=home,
                                                request_key=request["request_key"])) == result
    assert json.loads(universe_server.converse("Hello", home, "typed", choice, request)) == result
    from tinyassets.conversation_store import load_recent_readonly

    history = load_recent_readonly(tmp_path / home, f"principal:{owner}")
    assert [item.text for item in history] == ["Hello", "work completed"]
