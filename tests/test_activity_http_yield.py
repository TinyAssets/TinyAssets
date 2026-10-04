"""Activities yield through the real HTTP compiler/router/journal lifecycle."""

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests import test_workflow_http_agent as workflow_tests
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_run_provider_session import _branch, _run_branch
from tinyassets import activity_runner, agent_activities, engine_mcp_server, shared_self
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.foreground_run_provider import _ForegroundRunProviderSession
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.base import ModelConfig, UniverseContext
from tinyassets.providers.model_policy import ModelRef
from tinyassets.storage.provider_work_authority import db_path
from tinyassets.workflow_agent import WorkAgentAdapter

pytestmark = pytest.mark.usefixtures("cloud_runtime")
http_wire = workflow_tests.http_wire
work_agent = workflow_tests.work_agent


@pytest.mark.parametrize("batched_tool", [False, True])
@pytest.mark.parametrize("immediate_answer", [False, True])
def test_http_activity_yield_stops_tools_and_inference_and_releases_claim(
    tmp_path, monkeypatch, authenticate_request, work_agent, batched_tool, immediate_answer,
):
    universe = tmp_path / "universe_alice"
    universe.mkdir(exist_ok=True)
    record = agent_activities.create(
        universe, owner_principal="acct_alice", title="Ask before sending", brief="b",
        origin_kind="ask",
    )
    aid = record["activity_id"]
    generation = agent_activities.claim(universe, aid, replaceable=lambda _: False)
    prepare = _ForegroundRunProviderSession.prepare
    run_ids = []

    def bind_before_start(session, **kwargs):
        prepare(session, **kwargs)
        run_ids.append(kwargs["run_id"])
        assert agent_activities.bind_run(universe, aid, generation, kwargs["run_id"])

    monkeypatch.setattr(_ForegroundRunProviderSession, "prepare", bind_before_start)
    shared_turn = shared_self.prepare_shared_self_turn

    def prepare_activity(*args, activity):
        assert activity["activity_id"] == aid and activity["runner_generation"] == generation
        assert activity["runner_token"] == run_ids[0]
        return shared_turn(*args)

    monkeypatch.setattr(shared_self, "prepare_shared_self_turn", prepare_activity)
    monkeypatch.setattr(engine_mcp_server, "_GRAPH_ID", universe.name)
    monkeypatch.setattr(engine_mcp_server, "_calling_session", lambda: f"activity:{aid}")
    resolve_proxy = ApiKeyHttpProvider._resolve_proxy

    class AskThenAct:
        def __init__(self, proxy):
            self.proxy = proxy

        def close(self):
            self.proxy.close()

        def request(self, verb, document, **kwargs):
            result = self.proxy.request(verb, document, **kwargs)
            body = json.loads(result["body"])
            message = body["choices"][0]["message"]
            if "tool_calls" in message:
                message["tool_calls"][0]["function"] = {
                    "name": "write_graph", "arguments": json.dumps({
                        "target": "pending_request", "operation": "ask", "payload_json": "{}",
                    }),
                }
                if batched_tool:
                    message["tool_calls"].append({
                        "id": "must-not-run", "type": "function", "function": {
                            "name": "write_graph", "arguments": '{"target":"branch"}',
                        },
                    })
            return {**result, "body": json.dumps(body)}

    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                        lambda *a, **k: AskThenAct(resolve_proxy(*a, **k)))

    def yield_after_ask():
        assert work_agent.tools[-1][1]["target"] == "pending_request"
        result = engine_mcp_server._yield_activity({"request_id": "req-http-yield"})
        assert result["activity_waiting"]
        if immediate_answer:
            assert agent_activities.answered(universe, aid, "req-http-yield")

    work_agent.after_tool = yield_after_ask
    branch = _branch(node_count=1)
    branch.branch_def_id = activity_runner.branch_def_id(universe.name)
    branch.node_defs[0].tools_allowed = ["universe_self"]
    branch.node_defs[0].llm_policy = {"preferred": {"model": "synthetic-model"},
                                    "fallback_chain": []}
    result = _run_branch(tmp_path, monkeypatch, authenticate_request, branch,
                         open_provider=True, model_access=ModelAccess("discovered"))[0]
    assert result["terminal_status"] == "completed", result
    assert len(work_agent.wires) == 1 and len(work_agent.tools) == 1 and work_agent.closed
    turn = work_agent.latest()
    assert len(turn.rounds) == 1 and turn.rounds[0].tools[0].state == "completed"
    assert "known work result" in turn.rounds[0].tools[0].result_json
    if batched_tool:
        assert turn.rounds[0].tools[1].state == "not_sent"
        assert turn.state == "held_tool_not_sent"
    else:
        assert turn.state == "abandoned", "the settled round closes without another inference"
    current = agent_activities.get(universe, aid)
    assert current["status"] == (agent_activities.SCHEDULED if immediate_answer
                                 else agent_activities.WAITING_ON_YOU)
    assert current["retiring_token"] == run_ids[0] and not current["runner_token"]
    assert activity_runner.state(tmp_path, run_ids[0]) == activity_runner.ENDED
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute("SELECT state FROM provider_work_execution_claims").fetchall() == [
            ("released",),
        ]
        assert conn.execute("SELECT state FROM provider_invocation_reservations").fetchall() == [
            ("succeeded",),
        ]


def test_activity_binding_cannot_inherit_a_replacement_run(tmp_path):
    universe = tmp_path / "u-alpha"
    universe.mkdir()
    record = agent_activities.create(
        universe, owner_principal="acct_alice", title="t", brief="b", origin_kind="ask",
    )
    aid = record["activity_id"]
    generation = agent_activities.claim(universe, aid, replaceable=lambda _: False)
    assert agent_activities.bind_run(universe, aid, generation, "old-run")
    binding = activity_runner.ActivityRunBinding(universe, aid, generation, "old-run")
    binding.check()
    replacement = agent_activities.claim(universe, aid, replaceable=lambda _: True)
    assert agent_activities.bind_run(universe, aid, replacement, "new-run")
    with pytest.raises(PermissionError, match="activity_runner_superseded"):
        binding.check()
    assert not agent_activities.holds(universe, aid, replacement, run_id="old-run")
    assert agent_activities.holds(universe, aid, replacement, run_id="new-run")


@pytest.mark.parametrize("status", [agent_activities.PAUSED, agent_activities.COMPLETED])
def test_owner_pause_or_stop_is_not_reclassified_as_a_yield(tmp_path, status):
    universe = tmp_path / "u-alpha"
    universe.mkdir()
    record = agent_activities.create(
        universe, owner_principal="acct_alice", title="t", brief="b", origin_kind="ask",
    )
    aid = record["activity_id"]
    generation = agent_activities.claim(universe, aid, replaceable=lambda _: False)
    assert agent_activities.bind_run(universe, aid, generation, "run-1")
    binding = activity_runner.ActivityRunBinding(universe, aid, generation, "run-1")
    agent_activities.transition(universe, aid, status, result_summary="partial result")
    with pytest.raises(PermissionError, match="activity_runner_superseded"):
        binding.check()
    assert agent_activities.get(universe, aid)["result_summary"] == "partial result"


class _LaunchRecorder:
    """Stands in for the router at the ONE place a launch happens.

    The refusal under test must never get here, so recording the call is the
    whole point: the controls below prove the same doubles DO reach it, which is
    what keeps the refusal assertions from passing for the wrong reason.
    """

    def __init__(self):
        self.launches = []

    async def call(self, role, prompt, system, config, **kwargs):
        self.launches.append((role, kwargs.get("_agent_execution_kind")))
        return SimpleNamespace(text="launched", provider="owned-http")


#: The native candidate the owner's order offers mid-turn.
_NATIVE_NEXT = ModelRef("native-cli", "native-model")


def _live_activity_adapter(tmp_path, *, name, with_binding=True):
    """A real adapter over a real live binding; only the session/launch are doubles.

    The doubles are deliberately PERMISSIVE: identity, authority, candidate
    staging and re-authorization all succeed, so the only thing that can stop a
    round reaching ``_LaunchRecorder`` is the refusal under test.
    """
    universe = tmp_path / name
    universe.mkdir(exist_ok=True)
    record = agent_activities.create(
        universe, owner_principal="acct_alice", title="t", brief="b", origin_kind="ask",
    )
    aid = record["activity_id"]
    generation = agent_activities.claim(universe, aid, replaceable=lambda _: False)
    assert agent_activities.bind_run(universe, aid, generation, "run-1")
    receipt = SimpleNamespace(
        principal_id="acct_alice", universe_id=universe.name, receipt_id="receipt-1",
    )
    claim = object()

    def _carrier(provider):
        return SimpleNamespace(
            _receipt=receipt, _claim=claim, provider=provider, operation="run_graph",
            selected_model=SimpleNamespace(model_id="some-model"), native_selection=None,
        )

    authorized = []

    @contextmanager
    def authorize_attempt(*, role, prompt, system, policy):
        authorized.append(policy["preferred"])
        yield (_carrier(policy["preferred"]["provider"]), None,
               policy["preferred"]["provider"])

    session = SimpleNamespace(
        _universe_dir=universe,
        _work_candidates=SimpleNamespace(next_candidate=lambda *a, **k: _NATIVE_NEXT),
        _authorize_attempt=authorize_attempt,
        _check_agent_authority=lambda _carrier: receipt.principal_id,
    )
    binding = activity_runner.ActivityRunBinding(universe, aid, generation, "run-1")
    adapter = WorkAgentAdapter(
        session, (_carrier("owned-http"), None, "owned-http"), {},
        **({"activity_binding": binding} if with_binding else {}),
    )
    config = ModelConfig(
        engine_mcp_enabled=True, engine_mcp_actor_id=receipt.principal_id,
        engine_mcp_graph_id=receipt.universe_id,
    )
    context = UniverseContext(universe_dir=universe, model_selection=adapter.selection)
    return adapter, config, context, authorized


def _stage_native_switch(adapter, context):
    """Round two of the same turn, with the order now offering a native candidate."""
    adapter.initial_pending = False
    adapter._staged_next = _NATIVE_NEXT
    return replace(context, model_selection=_NATIVE_NEXT)


def test_activity_run_refuses_a_native_first_selection_before_any_launch(tmp_path):
    adapter, config, context, authorized = _live_activity_adapter(
        tmp_path, name="u-native-first",
    )
    router = _LaunchRecorder()
    with pytest.raises(ProviderAuthorityHeldError, match="need an engine-inference executor"):
        asyncio.run(adapter.infer(
            router=router, prompt="p", system="", config=config, context=context,
            observer=None, kind="native_agent",
        ))
    assert router.launches == [] and authorized == []
    # Still pending, so the caller's finally settles CANCELLED_BEFORE_LAUNCH.
    assert adapter.initial_pending


def test_activity_run_refuses_a_native_mid_turn_switch_before_any_launch(tmp_path):
    adapter, config, context, authorized = _live_activity_adapter(
        tmp_path, name="u-native-switch",
    )
    router = _LaunchRecorder()
    # ``infer`` runs every round, so a turn that started on engine inference
    # cannot walk onto a native candidate either -- and the refusal is the
    # activity's own, not a candidate-staging error.
    switched = _stage_native_switch(adapter, context)
    with pytest.raises(ProviderAuthorityHeldError, match="need an engine-inference executor"):
        asyncio.run(adapter.infer(
            router=router, prompt="p", system="", config=config, context=switched,
            observer=None, kind="native_agent",
        ))
    assert router.launches == [] and authorized == []
    assert adapter.selection != _NATIVE_NEXT, "the refused candidate is never adopted"


def test_activity_run_still_launches_engine_inference(tmp_path):
    adapter, config, context, _ = _live_activity_adapter(tmp_path, name="u-engine-ok")
    router = _LaunchRecorder()
    response = asyncio.run(adapter.infer(
        router=router, prompt="p", system="", config=config, context=context,
        observer=None, kind="engine_inference",
    ))
    assert response.text == "launched"
    assert router.launches == [("writer", "engine_inference")]


@pytest.mark.parametrize("mid_turn", [False, True])
def test_a_non_activity_run_still_launches_native(tmp_path, mid_turn):
    """The control that keeps the two refusals from passing for the wrong reason."""
    adapter, config, context, _ = _live_activity_adapter(
        tmp_path, name=f"u-native-chat-{int(mid_turn)}", with_binding=False,
    )
    router = _LaunchRecorder()
    if mid_turn:
        context = _stage_native_switch(adapter, context)
    response = asyncio.run(adapter.infer(
        router=router, prompt="p", system="", config=config, context=context,
        observer=None, kind="native_agent",
    ))
    assert response.text == "launched"
    assert router.launches == [("writer", "native_agent")]
