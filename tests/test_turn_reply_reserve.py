"""The owner loop spends its last request answering, and skips unsent models."""

import pytest

from tests import test_interactive_http_agent as integration
from tests.test_provider_model_refusal import _order
from tinyassets.exceptions import ProviderAuthorityHeldError, ProviderProtocolError
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.request_budget import FREE_TURN_ATTEMPTS

rig = integration.rig
reader = integration.reader
served = integration.served
agent = integration.agent


@pytest.fixture(autouse=True)
def metered_offer(monkeypatch):
    from tinyassets.providers import free_sources

    monkeypatch.setattr(free_sources, "daily_cap_for_host", lambda host: {
        "name": "Synthetic free source", "requests_per_day": 100,
        "reset_timezone": "UTC",
    })


def test_last_free_request_answers_with_completed_tool_history(agent):
    agent.requested_rounds = 20
    agent.tools_per_round = 2
    response = integration.run(agent)
    assert response == "finished exact answer"
    assert len(agent.wires) == FREE_TURN_ATTEMPTS
    assert len(agent.tools) == 2 * (FREE_TURN_ATTEMPTS - 1)
    body = agent.wires[-1][1]["body"]
    assert body["tool_choice"] == "none"
    assert any(message["role"] == "tool" for message in body["messages"])
    assert "anything left unfinished" in body["messages"][0]["content"]
    assert agent.latest().state == "completed"


def _unsent_models(monkeypatch, count):
    original = ApiKeyHttpProvider.complete
    calls = []

    async def complete(self, prompt, system, config, **kwargs):
        calls.append(config.selected_model.model_id)
        if len(calls) <= count:
            # The router reserved, but no proxy dispatch ever happened.
            raise ProviderProtocolError("synthetic model could not encode the request")
        return await original(self, prompt, system, config, **kwargs)

    monkeypatch.setattr(ApiKeyHttpProvider, "complete", complete)
    return calls


def test_two_unsent_model_failures_reach_third_candidate(agent, monkeypatch):
    _order(agent, monkeypatch, ["lab/second:free", "lab/third:free"])
    calls = _unsent_models(monkeypatch, 2)
    agent.requested_rounds = 0
    response = integration.run(agent)
    assert response == "finished exact answer"
    assert calls[-2:] == ["lab/second:free", "lab/third:free"]
    assert len(calls) == 3
    assert len(agent.wires) == 1


def test_no_candidate_stop_names_each_unsent_failure(agent, monkeypatch):
    _order(agent, monkeypatch, ["lab/second:free"])
    calls = _unsent_models(monkeypatch, 2)
    with pytest.raises(ProviderAuthorityHeldError) as error:
        integration.run(agent)
    assert "Failed before provider dispatch" in str(error.value)
    assert "could not encode" in str(error.value)
    assert all(model in str(error.value) for model in calls)
    assert len(calls) == 2
    assert agent.wires == []
    assert error.value.request_receipt["dispatched"] == 0
    assert all(a["state"] == "not_sent" for a in error.value.request_receipt["attempts"])


def test_provider_refusals_keep_consecutive_failure_cap(agent, monkeypatch):
    _order(agent, monkeypatch, ["lab/second:free", "lab/third:free"])
    agent.capacity_failures.update({1: 403, 2: 403})
    response = integration.run(agent)
    assert "request budget (2 provider attempts)" in response
    assert len(agent.wires) == 2


def test_unsent_failure_never_widens_to_paid_candidate(agent, monkeypatch):
    snapshots = integration.authority_tests.snapshot_tests
    original = snapshots._model

    def priced(model_id="new-company/new-model"):
        row = original(model_id)
        if model_id == "lab/paid":
            row["pricing"]["prompt"] = "0.01"
        return row

    monkeypatch.setattr(snapshots, "_model", priced)
    _order(agent, monkeypatch, ["lab/paid"])
    calls = _unsent_models(monkeypatch, 1)
    with pytest.raises(ProviderAuthorityHeldError, match="Failed before provider dispatch"):
        integration.run(agent)
    assert len(calls) == 1
    assert agent.wires == []


def test_final_reply_keeps_text_and_discards_ignored_tool_choice(agent, monkeypatch):
    from dataclasses import replace

    from tinyassets.providers.agent_chat_codec import ToolRequest

    original = ApiKeyHttpProvider.complete

    async def complete(self, *args, **kwargs):
        response = await original(self, *args, **kwargs)
        if response.agent_reply and response.agent_reply.text == "finished exact answer":
            reply = replace(response.agent_reply, stop="tool_requests", tool_requests=(
                ToolRequest(call_id="ignored", name="bash", arguments_json='{"command":"exit 1"}'),
            ))
            return replace(response, agent_reply=reply)
        return response

    monkeypatch.setattr(ApiKeyHttpProvider, "complete", complete)
    agent.requested_rounds = 20
    assert integration.run(agent) == "finished exact answer"
    assert len(agent.tools) == FREE_TURN_ATTEMPTS - 1
    assert agent.latest().state == "completed"
    assert not agent.latest().rounds[-1].reply.tool_requests
