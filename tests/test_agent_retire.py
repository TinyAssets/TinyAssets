"""Retirement is an owner operation, never deletion or loss of conversation."""
# ruff: noqa: F811 -- shared pytest fixtures
import asyncio
import json

import pytest

from tests.test_converse_addressed_agent import (  # noqa: F401
    OWNER,
    _agent,
    _become,
    _converse,
    _reset_auth,
    world,
)
from tinyassets import addressed_agents, universe_server
from tinyassets.custom_agents import (
    AgentConflictError,
    get_binding,
    list_bindings,
    set_binding_retired,
)


def change(world, operation="retire", revision=1, binding_id=None):
    return json.loads(universe_server.write_graph(
        target="agent_binding", operation=operation, graph_id="u-home",
        agent_binding_id=binding_id or world["weaver"], expected_revision=revision,
    ))


def test_retire_hides_refuses_address_and_restore_preserves_history_and_files(world):
    from tinyassets.conversation_store import load_recent_readonly

    _converse(message="Keep the orchard evidence", agent_id=world["weaver"])
    session = addressed_agents.memory_session(OWNER, world["weaver"])
    before = load_recent_readonly(world["udir"], session)
    note = world["udir"] / "evidence.txt"
    note.write_text("original evidence", encoding="utf-8")
    result = change(world)
    assert result["status"] == "retired" and result["binding"]["revision"] == 2
    from tinyassets.api.custom_agents import custom_agents

    assert custom_agents(action="list_bindings", universe_id="u-home")["bindings"] == []
    assert list_bindings(world["base"], universe_id="u-home", include_retired=False) == []
    assert addressed_agents.roster(world["base"], universe_id="u-home", owner=OWNER) == [
        {"agent_id": "main", "name": "Your agent"}]
    with pytest.raises(addressed_agents.AgentNotAddressable):
        addressed_agents.resolve(world["base"], universe_id="u-home", owner=OWNER,
                                 agent_id=world["weaver"])
    assert "error" in _converse(message="Cannot run", agent_id=world["weaver"])
    assert len(list_bindings(world["base"], universe_id="u-home", include_retired=True)) == 1
    assert len(list_bindings(world["base"], universe_id="u-home")) == 1
    assert change(world, "restore", 2)["binding"]["retired"] is False
    assert load_recent_readonly(world["udir"], session) == before
    assert note.read_text(encoding="utf-8") == "original evidence"
    assert addressed_agents.resolve(world["base"], universe_id="u-home", owner=OWNER,
                                    agent_id=world["weaver"]).name == "Evidence Weaver"
    assert "reply" in _converse(message="Continue", agent_id=world["weaver"])
    assert change(world, revision=3)["status"] == "retired"  # restore is reversible too


@pytest.mark.parametrize("target", ["main", "serving", "app_experience", "foreign"])
def test_protected_targets_are_refused(world, target):
    from tinyassets.custom_agents import _agent_connect

    bid = world["weaver"]
    if target == "main":
        bid = "main"
    elif target == "serving":
        with _agent_connect(world["base"]) as conn:
            conn.execute("UPDATE agent_bindings SET status='serving' WHERE agent_binding_id=?",
                         (bid,))
    elif target == "app_experience":
        bid = _agent(world["base"], "u-home", OWNER, "Main UI", role="app_experience")
    else:
        _become(world["base"], "another-owner", "u-home")  # even shared admin is not owner
    before = get_binding(world["base"], universe_id="u-home", binding_id=bid)
    assert "error" in change(world, binding_id=bid)
    assert get_binding(world["base"], universe_id="u-home", binding_id=bid) == before


def test_stale_revision_cannot_restore_or_retire(world):
    change(world)
    assert change(world, "restore", 1)["error"] == "agent_conflict"
    with pytest.raises(AgentConflictError):
        set_binding_retired(world["base"], universe_id="u-home", binding_id=world["weaver"],
                            updated_by=OWNER, expected_revision=1, retired=True)
    assert get_binding(world["base"], universe_id="u-home",
                       binding_id=world["weaver"])["retired"]


def test_retired_binding_cannot_change_configuration_or_become_serving(world):
    from tinyassets.api.custom_agents import custom_agents
    from tinyassets.custom_agents import (
        _agent_connect,
        set_binding_provider_ref_in_transaction,
        set_binding_serving_in_transaction,
    )

    change(world)
    result = custom_agents(action="update_binding", universe_id="u-home",
                           binding_id=world["weaver"], expected_revision=2,
                           payload={"schema_version": 1, "name": "Changed"})
    assert result["error"] == "agent_validation_error"
    for method, extra in [(set_binding_provider_ref_in_transaction, {"provider_ref": "p"}),
                          (set_binding_serving_in_transaction, {"enabled": True})]:
        with _agent_connect(world["base"]) as conn:
            conn.execute("BEGIN IMMEDIATE")
            with pytest.raises(PermissionError, match="agent retired"):
                method(conn, universe_id="u-home", binding_id=world["weaver"],
                       expected_revision=2, owner_user_id=OWNER, **extra)
    assert change(world, "restore", 2)["binding"]["configuration"]["name"] == "Evidence Weaver"


@pytest.mark.parametrize("kind", ["platform", "legacy_platform", "disconnected_provider"])
def test_main_binding_is_protected_before_connection_and_after_disconnect(world, kind):
    from tinyassets.custom_agents import (
        _agent_connect,
        create_binding,
        publish_definition,
        set_binding_provider_ref_in_transaction,
    )
    from tinyassets.onboarding.serving import (
        _PLATFORM_DEFINITION,
        PLATFORM_DEFINITION_AUTHOR,
        RETIRED_PLATFORM_DEFINITION_AUTHOR,
    )

    author = {"platform": PLATFORM_DEFINITION_AUTHOR,
              "legacy_platform": RETIRED_PLATFORM_DEFINITION_AUTHOR}.get(kind, OWNER)
    definition = publish_definition(world["base"], author_id=author, payload=_PLATFORM_DEFINITION)
    payload = {"schema_version": 1, "name": "Your agent", "role": "writer"}
    binding = create_binding(world["base"], universe_id="u-home", created_by=OWNER,
                             definition_id=definition["agent_definition_id"], payload=payload)
    if kind == "disconnected_provider":
        with _agent_connect(world["base"]) as conn:
            conn.execute("BEGIN IMMEDIATE")
            binding = set_binding_provider_ref_in_transaction(
                conn, universe_id="u-home", binding_id=binding["agent_binding_id"],
                expected_revision=1, owner_user_id=OWNER, provider_ref="connected-provider",
            )
    assert binding["status"] == "configured"
    result = change(world, binding_id=binding["agent_binding_id"], revision=binding["revision"])
    assert result["error"] == "agent_validation_error"
    assert "main agent" in result["detail"]
    assert get_binding(world["base"], universe_id="u-home",
                       binding_id=binding["agent_binding_id"]) == binding


def test_account_deletion_includes_retired_bindings(tmp_path):
    from tests.test_account_deletion import HOME_A, HOME_B, A, B, _seed_user
    from tinyassets.account_deletion import delete_account

    _seed_user(tmp_path, A, HOME_A)
    _seed_user(tmp_path, B, HOME_B)
    retired = _agent(tmp_path, HOME_A, A, "Retired")
    other = _agent(tmp_path, HOME_B, B, "Other owner")
    set_binding_retired(tmp_path, universe_id=HOME_A, binding_id=retired,
                        expected_revision=1, updated_by=A, retired=True)
    receipt = delete_account(tmp_path, founder_sub=A, cancel_billing=lambda _: "cancelled",
                             delete_identity=lambda _: "deleted")
    assert receipt["unfinished_phases"] == []
    assert get_binding(tmp_path, universe_id=HOME_A, binding_id=retired) is None
    assert get_binding(tmp_path, universe_id=HOME_B, binding_id=other) is not None


def test_existing_binding_database_gains_retirement_without_changing_rows(world):
    from tinyassets.custom_agents import _agent_connect

    before = get_binding(world["base"], universe_id="u-home", binding_id=world["weaver"])
    with _agent_connect(world["base"]) as conn:
        conn.execute("ALTER TABLE agent_bindings DROP COLUMN retired")
    from tinyassets import custom_agents

    custom_agents._SCHEMA_INITIALIZED.clear()
    assert get_binding(world["base"], universe_id="u-home", binding_id=world["weaver"]) == before
    assert change(world)["status"] == "retired"


def test_running_turn_stops_with_retirement_reason_and_other_agent_does_not(world):
    from tinyassets.turn_interrupt import TurnInterrupted, interactive_turn

    async def scenario():
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def inference():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        with interactive_turn(OWNER, "u-home", agent_id=world["weaver"],
                              base_path=world["base"]) as live:
            with interactive_turn(OWNER, "u-home") as main:
                task = asyncio.create_task(live.run(inference()))
                await started.wait()
                assert change(world)["status"] == "retired"
                with pytest.raises(TurnInterrupted, match="agent retired"):
                    await asyncio.wait_for(task, 5)
                assert cleaned.is_set()
                assert not main.requested()
        # A late registration after retirement is refused by the durable check.
        with interactive_turn(OWNER, "u-home", agent_id=world["weaver"],
                              base_path=world["base"]) as late:
            with pytest.raises(TurnInterrupted, match="agent retired"):
                late.check()

    asyncio.run(scenario())


def test_served_turn_reports_why_it_stopped(world, monkeypatch):
    import tinyassets.universe_intelligence as intelligence

    def provider(*args, **kwargs):
        change(world)
        return "finished inference"

    monkeypatch.setattr(intelligence, "call_provider", provider)
    result = _converse(message="Work until stopped", agent_id=world["weaver"])
    assert result["interrupted"] is True
    assert result["reason"] == "agent retired"
    assert result["history_saved"] is True


def test_restore_cannot_revive_an_old_turn_in_another_process(world, monkeypatch):
    from tinyassets import turn_interrupt

    addressed = addressed_agents.resolve(world["base"], universe_id="u-home", owner=OWNER,
                                          agent_id=world["weaver"])
    # Model the engine tool process: its registry cannot see the served turn.
    monkeypatch.setattr(turn_interrupt, "request_interrupt", lambda *a, **kw: 0)
    with turn_interrupt.interactive_turn(
        OWNER, "u-home", agent_id=world["weaver"], base_path=world["base"],
        retirement_revision=addressed.retirement_revision,
    ) as live:
        change(world)
        change(world, "restore", 2)
        with pytest.raises(turn_interrupt.TurnInterrupted, match="agent retired"):
            live.check()
    assert "reply" in _converse(message="New work", agent_id=world["weaver"])


def test_lifecycle_read_failure_preserves_error_and_cleans_up_inference(world, monkeypatch):
    import sqlite3

    from tinyassets import custom_agents, turn_interrupt

    async def scenario():
        cleaned = asyncio.Event()

        def unavailable(*args, **kwargs):
            raise sqlite3.OperationalError("retirement store unavailable")

        async def inference():
            monkeypatch.setattr(custom_agents, "get_binding", unavailable)
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        with turn_interrupt.interactive_turn(
            OWNER, "u-home", agent_id=world["weaver"], base_path=world["base"],
        ) as live:
            with pytest.raises(sqlite3.OperationalError, match="retirement store unavailable"):
                await asyncio.wait_for(live.run(inference()), 5)
            assert cleaned.is_set()
            assert live.reason == "the owner stopped this turn"  # no fabricated retirement

    asyncio.run(scenario())


def test_served_tool_and_ta_reach_retirement(world, monkeypatch):
    from tinyassets import engine_mcp_server as engine
    from tinyassets import ta_cli

    monkeypatch.setattr(engine, "_GRAPH_ID", "u-home")
    monkeypatch.setattr(engine, "_ACTOR_ID", OWNER)
    from tests.engine_authority_helpers import seed_bound_engine

    seed_bound_engine(monkeypatch)
    args = {"target": "agent_binding", "operation": "retire", "expected_revision": 1,
            "payload_json": json.dumps({"agent_binding_id": world["weaver"]})}

    def wire(message):
        if message["op"] == "catalog":
            return {"extension_roots": {}, "capabilities": [{"name": "write_graph"}]}
        return {"result": json.loads(engine.write_graph(**message["arguments"]))}

    monkeypatch.setattr(ta_cli, "remote", wire)
    result = ta_cli.main(["write_graph", "--json", json.dumps(args)])
    assert result.get("status") == "retired", result
    args.update(operation="restore", expected_revision=2)
    assert ta_cli.main(["write_graph", "--json", json.dumps(args)])["status"] == "configured"


def test_scheduled_and_running_agent_activities_stop_and_main_stays(world, monkeypatch):
    from tinyassets import activity_runner
    from tinyassets import agent_activities as activities
    from tinyassets.runs import RunCancelledError

    records = [activities.create(world["udir"], owner_principal=OWNER, title="Work",
                                 brief="Keep evidence", origin_kind="schedule", origin_ref=str(i),
                                 agent_id=world["weaver"]) for i in range(2)]
    main = activities.create(world["udir"], owner_principal=OWNER, title="Main", brief="Continue",
                             origin_kind="ask")
    aid = records[0]["activity_id"]
    generation = activities.claim(world["udir"], aid, replaceable=lambda _: False)
    assert activities.bind_run(world["udir"], aid, generation, "run-agent")
    running = activity_runner.ActivityRunBinding(world["udir"], aid, generation, "run-agent")
    cancelled = []
    monkeypatch.setattr(activity_runner, "stop", lambda base, run: cancelled.append(run))
    change(world)
    assert cancelled == ["run-agent"]
    for record in records:
        stopped = activities.get(world["udir"], record["activity_id"])
        assert stopped["status"] == activities.COMPLETED
        assert stopped["outcome"] == "agent retired"
    with pytest.raises(RunCancelledError, match="agent retired"):
        running.check()
    assert activities.get(world["udir"], main["activity_id"])["status"] == activities.SCHEDULED
    with pytest.raises(addressed_agents.AgentNotAddressable):
        activities.create(world["udir"], owner_principal=OWNER, title="New", brief="Refuse",
                          origin_kind="ask", agent_id=world["weaver"])
    change(world, "restore", 2)
    with pytest.raises(RunCancelledError, match="agent retired"):
        running.check()


@pytest.mark.real_browser
def test_real_browser_switcher_drops_retired_agent_and_restores_it(world):
    from playwright.sync_api import sync_playwright

    from tests.test_onboarding_app import _js_function
    from tinyassets import onboarding

    html, _ = onboarding.render_app_html()
    refresh = _js_function(html, "refreshAgentSwitcher")
    document = '''<div id="chat-cloud-bar"></div><script>
        const $=id=>document.getElementById(id);
        let addressedAgent={agent_id:'main',name:'Your agent'};
        const AppUI={enabled:true,listAgents:()=>fetch('/agents').then(r=>r.json())};
        async function addressAgent(agent){addressedAgent=agent;}
        function setStatusLine(text){throw new Error(text);}
    ''' + refresh + "\nrefreshAgentSwitcher();</script>"
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        page = browser.new_page()

        def route(request):
            if request.request.url.endswith("/agents"):
                body = json.dumps({"agents": addressed_agents.roster(
                    world["base"], universe_id="u-home", owner=OWNER)})
                request.fulfill(content_type="application/json", body=body)
            else:
                request.fulfill(content_type="text/html", body=document)

        page.route("https://tinyassets.test/**", route)
        page.goto("https://tinyassets.test/app")
        options = page.locator("#agent-switcher option")
        page.wait_for_function("document.querySelectorAll('#agent-switcher option').length===2")
        assert options.all_text_contents() == ["Your agent", "Evidence Weaver"]
        change(world)
        page.reload()
        page.wait_for_function("document.querySelectorAll('#agent-switcher option').length===1")
        assert options.all_text_contents() == ["Your agent"]
        change(world, "restore", 2)
        page.reload()
        page.wait_for_function("document.querySelectorAll('#agent-switcher option').length===2")
        assert options.all_text_contents() == ["Your agent", "Evidence Weaver"]
        browser.close()
