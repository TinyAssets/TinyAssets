"""Published unknown source through real selection, broker, writer and journal.

Only remote network and engine tool service are synthetic; no source registry
mutation, forged discovery snapshot, or caller-created execution authority.
"""

import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests import test_custom_discovery_publication as publication
from tests import test_interactive_http_agent as interactive
from tests.inference_usage_helpers import accounting_resolver
from tests.test_selected_model_authority import _authorize
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.discovery_snapshot import ModelDiscoveryUnavailable
from tinyassets.providers.model_options import model_options_document
from tinyassets.providers.model_policy import ModelRef
from tinyassets.providers.model_selection import prepare_selected_model
from tinyassets.providers.served_model_plan import prepare_owned_model_plan

rig = publication.rig
source = publication.source
served = interactive.served
agent = interactive.agent
_RESOLVE_PROXY = ApiKeyHttpProvider._resolve_proxy
MODEL = "unseen:model-v7"
OTHER = "not-yet-existing-company/model-next"


@pytest.fixture
def reader(rig, source):
    document = source.document
    document["contract"]["inference"]["allowed"].extend(["tools", "tool_choice", "temperature"])
    # A source's explicit model-only capacity scope permits a sibling fallback.
    document["contract"]["capacity"]["cases"][0]["scope"] = "model"
    wire = json.loads(source.catalogue_json)
    row = wire["inventory"]["items"][0]
    row["tariff"] = {"input": 0, "output": 0, "call": 0}
    sibling = deepcopy(row)
    sibling["identity"] = {"key": OTHER, "evaluation": "evaluation/other"}
    wire["inventory"]["items"].append(sibling)
    wire["inventory"]["count"] = 2
    source.catalogue_json = json.dumps(wire)
    assert rig.api(descriptor=document)["status"] == "configured"
    return source


@pytest.fixture
def running(agent, source, monkeypatch):
    # Restore the production adapter's scoped-proxy resolver; publication.source
    # supplies the real broker and synthetic network, not a fake inference proxy.
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy", accounting_resolver(_RESOLVE_PROXY))
    response_state = SimpleNamespace(failure_at=None, reported_cost="0", before_reply=lambda: None)

    def infer(request):
        assert agent.latest().state == "inference_started"
        agent.wires.append(("POST", request))
        response_state.before_reply()
        if len(agent.wires) == response_state.failure_at:
            return {"status": 418, "headers": {"retry-after": "60"}, "body": "{}"}
        tools = len(agent.tools) == 0
        message = {"role": "assistant", "content": None if tools else "finished exact answer"}
        if tools:
            # The model's own tool inventory is read/write/edit/bash; an engine
            # tool is reached through ``ta`` in bash, the shape the agent fixture uses.
            message["tool_calls"] = [{"id": "source-tool", "type": "function", "function": {
                "name": agent.tool_call[0], "arguments": agent.tool_call[1],
            }}]
        response = json.dumps({"model": "source-actual-model", "choices": [{
            "message": message, "finish_reason": "tool_calls" if tools else "stop",
        }]})
        if response_state.reported_cost is not None:
            response = response[:-1] + ',"meter":{"usd":' + response_state.reported_cost + '}}'
        return {"status": 200, "body": response}

    source.infer = infer
    agent.served.context = replace(agent.served.context, model_selection=ModelRef(
        agent.served.context.model_selection.connection_id, MODEL,
    ))
    return SimpleNamespace(agent=agent, source=source, response=response_state)


def prepare(running):
    state = running.agent.served
    return prepare_owned_model_plan(base=state.rig.base, universe=state.context.universe_dir,
                                    owner="owner", agent=state.agent)


@pytest.mark.parametrize("cost,expected", [(None, None), ("0", 0), ("1e-99", 1)])
def test_published_contract_powers_real_agent_and_truthful_usage(running, cost, expected):
    running.response.reported_cost = cost
    agent = running.agent
    receipts = []
    assert interactive.run(agent, receipts.append) == "finished exact answer"
    assert agent.latest().state == "completed" and len(agent.tools) == 1
    assert len(agent.wires) == 2
    assert receipts[0].reported_model == "source-actual-model"
    assert receipts[0].cost_microunits == expected
    for verb, request in agent.wires:
        assert verb == "POST" and request["url"] == "https://owned.example/custom/chat"
        assert request["body"]["samples"] == 8
        assert request["body"]["billing"]["ceilings"] == {"0": "0", "1": "0", "2": "0"}
        assert request["body"]["model"] == MODEL
    result = json.loads(agent.wires[-1][1]["body"]["messages"][-1]["content"])
    assert result["content"][0]["text"] == "exact result 🪐"


def test_real_plan_and_picker_admit_declared_source_without_fabricating_privacy(running):
    prepared = prepare(running)
    assert prepared.plan.next_candidate("owner", "u-models").model_id == MODEL
    document = model_options_document(prepared.catalog, prepared.plan, prepared.ineligible)
    assert document["order"][0]["model_id"] == MODEL
    row = document["options"][0]
    assert row["availability_basis"] == "owner_configured_contract"
    assert "source_claims_not_independently_verified" in row["labels"]
    assert prepared.catalog.connections[0].owner_filtered is False
    assert prepared.catalog.connections[0].authenticated_account_id is None
    assert prepared.plan.policy.ranking_source == prepared.snapshots[0].contract().ranking_source


def test_declared_capacity_drives_real_fallback_without_replaying_tool(running):
    agent = running.agent
    prepared = prepare(running)
    agent.served.context = replace(agent.served.context, agent_model_plan=prepared.plan)
    running.response.failure_at = 2
    assert interactive.run(agent) == "finished exact answer"
    assert len(agent.wires) == 3 and len(agent.tools) == 1
    assert agent.wires[-1][1]["body"]["model"] == OTHER
    assert [round.state for round in agent.latest().rounds] == ["received", "failed", "received"]


def test_unknown_capacity_scope_tries_a_free_sibling_in_the_same_grant(running):
    """Unknown scope still proves nothing about the account.

    On a source whose accepted ceilings are all zero the turn narrows it to the
    model that actually failed and tries a sibling under the SAME grant at the
    SAME ceilings -- the live 2026-09-25 dead end was a first message refused by
    one busy free model with siblings right behind it. What it must never
    become is a claim that the account is fine: nothing paid is admitted, and
    the retry cannot leave this connection.
    """
    running.source.document["contract"]["capacity"]["cases"][0]["scope"] = "unknown"
    running.agent.served.rig.api(descriptor=running.source.document)
    prepared = prepare(running)
    running.agent.served.context = replace(
        running.agent.served.context, agent_model_plan=prepared.plan,
    )
    running.response.failure_at = 1
    assert interactive.run(running.agent) == "finished exact answer"
    assert len(running.agent.wires) == 3 and len(running.agent.tools) == 1
    assert running.agent.wires[-1][1]["body"]["model"] == OTHER
    assert {wire[1]["url"] for wire in running.agent.wires} == {
        "https://owned.example/custom/chat"
    }
    assert all(
        wire[1]["body"]["billing"]["ceilings"] == {"0": "0", "1": "0", "2": "0"}
        for wire in running.agent.wires
    )


def test_unknown_capacity_scope_on_a_spending_source_continues_within_its_ceilings(running):
    """One rule for every account (founder, 2026-09-25): an unknown-scope window
    narrows to the failed model on a metered source exactly as on a free one.

    Money stays bounded by the per-attempt ceilings, not by skipping the retry:
    every sibling request still carries the source's own (non-zero) price cap.
    """
    running.source.document["contract"]["capacity"]["cases"][0]["scope"] = "unknown"
    running.agent.served.rig.api(descriptor=running.source.document)
    prepared = prepare(running)
    metered = replace(prepared.plan.source_policies[0], cost_caps=tuple(
        replace(charge, amount_micros=10_000)
        for charge in prepared.plan.source_policies[0].cost_caps
    ))
    running.agent.served.context = replace(
        running.agent.served.context,
        agent_model_plan=replace(prepared.plan, source_policies=(metered,)),
    )
    running.response.failure_at = 1
    interactive.run(running.agent)
    assert len(running.agent.wires) > 1
    # This rig's contract carries the ceiling in ``billing``; the router
    # re-authorizes every sibling from the serving authority, so each attempt
    # carries the same compiled ceiling and strict enforcement.
    billed = [request["body"].get("billing") for _verb, request in running.agent.wires]
    assert all(b and b.get("ceilings") and b["enforce"] == {"strict": True} for b in billed)
    assert all(b == billed[0] for b in billed)


def test_published_source_does_not_authorize_paid_model(running):
    wire = json.loads(running.source.catalogue_json)
    wire["inventory"]["items"][0]["tariff"]["output"] = 10
    running.source.catalogue_json = json.dumps(wire)
    with pytest.raises(ProviderAuthorityHeldError):
        interactive.run(running.agent)
    assert not running.agent.wires


def test_selection_uses_captured_quantities_and_rechecks_publication(rig, source):
    rig.api(descriptor=source.document)
    caps = (("input_million_tokens_usd", 1_250_000),
            ("output_million_tokens_usd", 10_000_000), ("request_usd", 0))
    selection, recheck = prepare_selected_model(
        base_path=rig.base, owner_user_id="owner", universe_id="u-models",
        provider=f"api_key_http:{rig.definition.id}", model_id=MODEL,
        access=ModelAccess("discovered", cost_caps=caps),
    )
    assert selection.cost_upper_bound(1000) == 90_000  # 10k input + eight 10k outputs.
    assert selection.affordable_output(20_000, output_limit=1000) == 125
    assert selection.affordable_output(9_999, output_limit=1000) == 0
    with pytest.raises(ValueError, match="finite"):
        selection.affordable_output(20_000)
    source.document["contract"]["quantity_model"]["quantities"]["output_tokens"]["output"] = 9
    rig.api(descriptor=source.document)
    assert selection.cost_upper_bound(1000) == 90_000
    with pytest.raises(ModelDiscoveryUnavailable, match="changed"):
        recheck()


@pytest.mark.parametrize("budget,expected", [(100_000, 125), (100_799, 125), (100_800, 126)])
def test_atomic_admission_reserves_aggregate_quantity_not_one_output(running, budget, expected):
    from tinyassets.provider_assignment import reserve_served_provider_budget

    state = running.agent.served
    # Isolate admission arithmetic with a trusted server-side selected value,
    # as in the legacy reservation test. This does not claim a paid grant.
    with _authorize(state) as authority:
        selected = replace(authority.selected_model, cost_caps=(
            ("input_million_tokens_usd", 0),
            ("output_million_tokens_usd", 100_000_000), ("request_usd", 0),
        ))
        reservation = reserve_served_provider_budget(
            state.rig.base, universe_dir=state.context.universe_dir,
            authority=replace(authority, selected_model=selected, max_cost_microunits=budget),
            requested_output_tokens=1000, estimated_input_tokens=10,
        )
        assert reservation.output_tokens == expected
        assert reservation.reserved_cost_microunits == expected * 800
        assert reservation.reserved_cost_microunits <= budget


def test_final_reservation_rejects_inconsistent_affordability_before_insert(running, monkeypatch):
    from tinyassets.provider_assignment import reserve_served_provider_budget
    from tinyassets.providers.model_selection import SelectedModel

    state = running.agent.served
    with _authorize(state) as authority:
        # Deliberately faulty future implementation cannot bypass the final fence.
        monkeypatch.setattr(SelectedModel, "affordable_output", lambda *a, **k: 1000)
        monkeypatch.setattr(SelectedModel, "cost_upper_bound", lambda *a: 100_001)
        with pytest.raises(ProviderAuthorityHeldError):
            reserve_served_provider_budget(
                state.rig.base, universe_dir=state.context.universe_dir,
                authority=replace(authority, max_cost_microunits=100_000),
                requested_output_tokens=1000, estimated_input_tokens=10,
            )
    with running.agent.journal._ledger.connection() as conn:
        # Admission's schema creation was in the same transaction and rolled back.
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = ?",
                            ("served_provider_budget_reservations",)).fetchone() is None


def test_revoked_source_is_revalidated_before_tool_dispatch(running):
    running.response.before_reply = lambda: running.agent.served.rig.ledger.revoke_grant(
        "grant-models",
    )
    with pytest.raises(PermissionError, match="^open provider connection authority unavailable$"):
        interactive.run(running.agent)
    assert len(running.agent.wires) == 1 and not running.agent.tools
