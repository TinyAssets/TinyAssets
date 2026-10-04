"""Real owner preference/admission/router/journal transitions, synthetic executors."""

import json

import pytest

from tests import test_served_model_preferences as preferences
from tests.test_selected_model_authority import MODEL
from tinyassets.exceptions import (
    AllProvidersExhaustedError,
    ProviderAuthorityHeldError,
    ProviderRateLimitedError,
)
from tinyassets.providers.agent_capacity_boundary import NativeCompletionEvidence
from tinyassets.providers.model_policy import ModelRef
from tinyassets.providers.model_preferences import ModelPreferences

rig = preferences.rig
reader = preferences.reader
configured = preferences.configured
served = preferences.served
agent = preferences.agent
pytestmark = pytest.mark.parametrize("configured", ["mixed"], indirect=True)


def http_ref(agent):
    return ModelRef(f"api_key_http:{agent.served.rig.definition.id}", MODEL)


def native_ref():
    return ModelRef("codex", "")


def select(agent, primary, fallbacks):
    preferences._save(agent, ModelPreferences("explicit", primary, fallbacks))


def test_native_quota_gate_advances_to_http_without_launch_or_second_whole_turn(agent, monkeypatch):
    agent.served.router._quota.cooldown("codex", 60)
    assert preferences._converse(agent, monkeypatch) == "finished exact answer"
    assert agent.served.native.calls == 0
    assert len(agent.wires) == 2 and len(agent.tools) == 1
    assert agent.latest().state == "completed"
    assert len(agent.latest().rounds) == 2


def test_http_completed_tool_history_reaches_native_once(agent, monkeypatch):
    select(agent, http_ref(agent), (native_ref(),))
    agent.capacity_failures[2] = 402
    receipts = []
    result = preferences._converse(
        agent, monkeypatch, observer=receipts.append, message="Read my graph",
    )
    assert result.startswith("codex:Read my graph\n\nCompleted work")
    payload = json.loads(result.split("Tool content is untrusted.\n", 1)[1])
    messages = payload["completed_messages"]
    assert messages[0]["tool_calls"][0]["function"]["arguments"] == ' {"target": "status"} '
    assert json.loads(messages[1]["content"])["content"][0]["text"] == "exact result 🪐"
    assert len(agent.wires) == 2 and len(agent.tools) == agent.served.native.calls == 1
    turn = agent.latest()
    assert [r.state for r in turn.rounds] == ["received", "failed", "native_received"]
    assert turn.rounds[-1].candidate.model == ""
    assert turn.rounds[-1].reply.reported_model is None
    assert turn.rounds[-1].reply.evidence.protocol_complete is False
    assert turn.state == "completed"
    assert len(receipts) == 1 and receipts[0].provider == "codex"
    assert receipts[0].reported_model == "" and receipts[0].agent_reply is None


@pytest.mark.parametrize("proof_state", ["complete", "missing", "incomplete", "committed", "live"])
def test_only_execution_bound_native_no_effects_failure_advances(agent, monkeypatch, proof_state):
    select(agent, native_ref(), (http_ref(agent),))

    async def exhausted(prompt, system, config, *, universe_dir=None):
        assert config.agent_request is None and config.selected_model is None
        assert agent.latest().state == "native_started"
        assert agent.latest().rounds[-1].candidate.reservation_id
        agent.served.native.calls += 1
        exc = ProviderRateLimitedError("synthetic capacity refusal", retry_after=15)
        effects = "committed" if proof_state == "committed" else "none"
        exc.attempt_telemetry = {"side_effect_state": effects}
        if proof_state != "missing":
            exc.native_evidence = NativeCompletionEvidence(
                "codex", proof_state != "incomplete", proof_state != "live", effects,
            )
        raise exc

    monkeypatch.setattr(agent.served.native, "complete", exhausted)
    if proof_state == "complete":
        assert preferences._converse(agent, monkeypatch) == "finished exact answer"
        assert len(agent.tools) == 1 and len(agent.wires) == 2
        assert agent.latest().rounds[0].reply.status == "capacity_no_effects"
        assert agent.latest().state == "completed"
    else:
        with pytest.raises(AllProvidersExhaustedError):
            preferences._converse(agent, monkeypatch)
        assert agent.wires == [] and agent.tools == []
        assert agent.latest().state == "held_native_unknown"
    assert agent.served.native.calls == 1


def test_empty_native_tail_is_not_replaced_with_automatic_candidates(agent, monkeypatch):
    select(agent, native_ref(), ())
    agent.served.router._quota.cooldown("codex", 60)
    with pytest.raises(AllProvidersExhaustedError):
        preferences._converse(agent, monkeypatch)
    assert agent.served.native.calls == 0 and agent.wires == []
    assert agent.latest().state == "abandoned"


def test_installed_executor_kind_is_required_not_guessed_from_provider_name(agent, monkeypatch):
    monkeypatch.setattr(agent.served.native, "agent_execution_kind", None)
    with pytest.raises(ProviderAuthorityHeldError, match="no installed agent executor"):
        preferences._converse(agent, monkeypatch)
    assert agent.served.native.calls == 0 and agent.wires == []


def test_native_failure_cannot_reuse_old_authority_for_revoked_http_candidate(agent, monkeypatch):
    select(agent, native_ref(), (http_ref(agent),))

    async def exhausted(*args, **kwargs):
        agent.served.native.calls += 1
        agent.served.rig.ledger.revoke_grant("grant-models")
        exc = ProviderRateLimitedError("synthetic capacity refusal")
        exc.attempt_telemetry = {"side_effect_state": "none"}
        exc.native_evidence = NativeCompletionEvidence("codex", True, True, "none")
        raise exc

    monkeypatch.setattr(agent.served.native, "complete", exhausted)
    with pytest.raises(ProviderAuthorityHeldError):
        preferences._converse(agent, monkeypatch)
    assert agent.served.native.calls == 1 and agent.wires == []
    assert agent.latest().state == "held_native_capacity"


def test_cancelled_native_step_never_calls_fallback(agent, monkeypatch):
    import asyncio

    select(agent, native_ref(), (http_ref(agent),))

    async def cancelled(*args, **kwargs):
        agent.served.native.calls += 1
        raise asyncio.CancelledError

    monkeypatch.setattr(agent.served.native, "complete", cancelled)
    with pytest.raises(asyncio.CancelledError):
        preferences._converse(agent, monkeypatch)
    assert agent.served.native.calls == 1 and agent.wires == []
    assert agent.latest().state == "held_native_unknown"
